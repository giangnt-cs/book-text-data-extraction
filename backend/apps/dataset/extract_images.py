"""Rút trích bbox hình từ sách → ImageSourceDocument / ImageChunk."""

from __future__ import annotations

import logging
import re

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.dataset.catalog import (
    assign_image_catalog_code,
    copy_book_classification,
    format_image_chunk_code,
    image_chunk_png_name,
)
from apps.dataset.markdown import ensure_leading_page_comment
from apps.dataset.models import ImageChunk, ImageExtractStatus, ImageSourceDocument
from apps.dataset.vision import annotate_illustration, inspect_illustration
from apps.documents.models import Book, Box, ContentType, Page

logger = logging.getLogger(__name__)


def book_has_image_boxes(book: Book) -> bool:
    return Box.objects.filter(page__book=book, content_type=ContentType.IMAGE).exists()


def enqueue_book_image_extract(book: Book) -> tuple[bool, str]:
    from celery import current_app

    from apps.dataset.models import ImageExtractStatus
    from apps.documents.task_control import get_image_extract_task_id, set_image_extract_task_id

    if not book_has_image_boxes(book):
        return False, "Sách không có bounding box loại hình ảnh."
    if get_image_extract_task_id(str(book.pk)):
        return False, "Đang rút trích hình."
    document = get_or_create_image_document(book)
    if (document.extract_status or "") == ImageExtractStatus.PROCESSING:
        return False, "Đang rút trích hình."
    ImageSourceDocument.objects.filter(pk=document.pk).update(
        extract_status=ImageExtractStatus.PROCESSING,
        extract_error="",
        updated_at=timezone.now(),
    )
    try:
        async_result = current_app.send_task(
            "apps.dataset.tasks.extract_book_images",
            kwargs={"book_id": str(book.pk)},
            queue="cpu",
        )
        set_image_extract_task_id(str(book.pk), async_result.id)
    except Exception as exc:
        ImageSourceDocument.objects.filter(pk=document.pk).update(
            extract_status=ImageExtractStatus.FAILED,
            extract_error=str(exc),
            updated_at=timezone.now(),
        )
        return False, str(exc)
    return True, ""


def cancel_book_image_extract(book: Book) -> tuple[bool, str]:
    from apps.documents.task_control import cancel_image_extract, get_image_extract_task_id

    document = getattr(book, "dataset_image_source", None)
    running = bool(get_image_extract_task_id(str(book.pk)))
    if document is not None:
        running = running or (document.extract_status or "") == ImageExtractStatus.PROCESSING
    if not running:
        return False, "Không đang rút trích hình."
    detail = cancel_image_extract(str(book.pk))
    if document is not None:
        ImageSourceDocument.objects.filter(
            pk=document.pk,
            extract_status=ImageExtractStatus.PROCESSING,
        ).update(
            extract_status=ImageExtractStatus.FAILED,
            extract_error="Người dùng đã ngưng rút trích hình.",
            updated_at=timezone.now(),
        )
    return True, detail


def get_or_create_image_document(book: Book) -> ImageSourceDocument:
    document = getattr(book, "dataset_image_source", None)
    if document is None:
        document = ImageSourceDocument(book=book, title=book.title or "")
        copy_book_classification(document, book)
        document.save()
    else:
        document.title = book.title or document.title
        copy_book_classification(document, book)
        document.save(
            update_fields=[
                "title",
                "author",
                "publisher",
                "published_year",
                "class_source",
                "class_field",
                "class_language",
                "class_doc_type",
                "class_era",
                "updated_at",
            ]
        )
    assign_image_catalog_code(document)
    return document


def load_page_png_bytes(page: Page) -> bytes:
    from apps.dataset.ragflow import PageImageBase64Cache

    if page.page_image:
        try:
            with page.page_image.open("rb") as fh:
                data = fh.read()
            if data:
                return data
        except Exception:
            logger.warning("Không đọc được page_image trang %s", page.page_number, exc_info=True)
    book = page.book
    if book is None:
        return b""
    return PageImageBase64Cache()._load_png(book, int(page.page_number))


def image_box_page_seq(box: Box) -> int:
    return Box.objects.filter(
        page_id=box.page_id,
        content_type=ContentType.IMAGE,
        reading_order__lte=box.reading_order,
    ).count()


def extract_book_images(book: Book, *, check_cancel=None) -> dict:
    """Duyệt bbox image, crop, OCR chữ, VLM mô tả, lưu chunk. Bỏ qua box đã có ImageChunk."""
    document = get_or_create_image_document(book)
    ImageSourceDocument.objects.filter(pk=document.pk).update(
        extract_status=ImageExtractStatus.PROCESSING,
        extract_error="",
        updated_at=timezone.now(),
    )
    document.extract_status = ImageExtractStatus.PROCESSING
    document.extract_error = ""

    boxes = list(
        Box.objects.filter(page__book=book, content_type=ContentType.IMAGE)
        .select_related("page", "page__book")
        .order_by("page__page_number", "reading_order")
    )
    existing = set(
        ImageChunk.objects.filter(document=document).values_list("box_id", flat=True)
    )
    position = int(document.chunks.aggregate(m=Max("position")).get("m") or 0)
    created = 0
    skipped = 0
    skipped_text = 0
    errors: list[str] = []

    for box in boxes:
        if check_cancel is not None:
            check_cancel()
        if box.id in existing:
            skipped += 1
            continue
        page = box.page
        page_number = int(page.page_number or 1)
        page_seq = image_box_page_seq(box)
        try:
            page_png = load_page_png_bytes(page)
            if not page_png:
                raise RuntimeError("Không lấy được ảnh trang.")
            from ocr_llm.image_utils import crop_region

            crop = crop_region(page_png, box.x, box.y, box.width, box.height)
            inspected = inspect_illustration(crop)
            if inspected.is_text_only:
                skipped_text += 1
                logger.info(
                    "Bỏ khung chữ trang %s box %s (coverage=%.2f hull=%.2f words=%s)",
                    page_number,
                    box.reading_order,
                    inspected.word_coverage,
                    inspected.hull_coverage,
                    inspected.word_count,
                )
                continue
            vision_md = annotate_illustration(crop, page=page, inspect=inspected)
            if not vision_md:
                skipped_text += 1
                continue
            text_md = ensure_leading_page_comment(vision_md, page_number)
            position += 1
            code = format_image_chunk_code(document.catalog_code, page_number, page_seq) or f"{box.id}_img"
            chunk = ImageChunk(
                document=document,
                box=box,
                position=position,
                page_number=page_number,
                page_seq=page_seq,
                chunk_code=code,
                text_md=text_md,
            )
            chunk.image.save(f"{code}.png", ContentFile(crop), save=False)
            chunk.save()
            created += 1
        except Exception as exc:
            logger.warning(
                "Rút trích hình trang %s box %s sách «%s» lỗi: %s",
                page_number,
                box.reading_order,
                book.title,
                exc,
                exc_info=True,
            )
            errors.append(f"Trang {page_number} box {box.reading_order}: {exc}")

    error_text = "\n".join(errors[:80])
    if created == 0 and errors and not document.chunks.exists():
        status = ImageExtractStatus.FAILED
    else:
        status = ImageExtractStatus.DONE
    ImageSourceDocument.objects.filter(pk=document.pk).update(
        extract_status=status,
        extract_error=error_text,
        updated_at=timezone.now(),
    )
    document.extract_status = status
    document.extract_error = error_text
    return {
        "document_id": str(document.pk),
        "created": created,
        "skipped": skipped,
        "skipped_text": skipped_text,
        "errors": len(errors),
        "extract_status": status,
    }


def enqueue_filter_text_only_chunks(chunk_ids: list[str]) -> tuple[bool, str]:
    from celery import current_app

    ids = [str(item) for item in chunk_ids if item]
    if not ids:
        return False, "Không có phân đoạn hình."
    current_app.send_task(
        "apps.dataset.tasks.filter_text_only_image_chunks",
        kwargs={"chunk_ids": ids},
        queue="cpu",
    )
    return True, f"Đã gửi {len(ids)} phân đoạn vào hàng đợi lọc khung chữ."


def purge_text_only_image_chunks(*, chunk_ids: list[str]) -> dict:
    """Gửi crop đã lưu lên Vision; xóa phân đoạn chỉ là khung chữ."""
    from apps.dataset.ragflow import image_chunk_png

    chunks = list(
        ImageChunk.objects.filter(pk__in=chunk_ids).select_related("document").order_by("position")
    )
    removed = 0
    kept = 0
    errors: list[str] = []
    documents: dict = {}
    touched: dict = {chunk.document_id: chunk.document for chunk in chunks}
    to_delete: list[ImageChunk] = []
    for chunk in chunks:
        png = image_chunk_png(chunk)
        if not png:
            errors.append(f"{chunk.chunk_code or chunk.pk}: không đọc được ảnh crop.")
            continue
        try:
            inspected = inspect_illustration(png)
        except Exception as exc:
            errors.append(f"{chunk.chunk_code or chunk.pk}: {exc}")
            continue
        if inspected.is_text_only:
            to_delete.append(chunk)
            documents[chunk.document_id] = chunk.document
            logger.info(
                "Xóa khung chữ %s (coverage=%.2f hull=%.2f words=%s)",
                chunk.chunk_code or chunk.pk,
                inspected.word_coverage,
                inspected.hull_coverage,
                inspected.word_count,
            )
        else:
            kept += 1

    files_to_remove: list[tuple] = []
    with transaction.atomic():
        for chunk in to_delete:
            if chunk.image and chunk.image.name:
                files_to_remove.append((chunk.image.storage, chunk.image.name))
            chunk.delete()
            removed += 1
        for document in touched.values():
            resequence_image_chunks(document)
            if document.pk not in documents:
                continue
            ImageSourceDocument.objects.filter(pk=document.pk).update(
                ragflow_match_status="",
                ragflow_match_detail="Đã lọc khung chữ — nạp lại RAGFlow nếu đã nạp trước đó.",
                updated_at=timezone.now(),
            )
            document.ragflow_match_status = ""
            document.ragflow_match_detail = "Đã lọc khung chữ — nạp lại RAGFlow nếu đã nạp trước đó."
    for storage, name in files_to_remove:
        if name and storage.exists(name):
            try:
                storage.delete(name)
            except Exception:
                logger.warning("Không xóa được file crop %s", name, exc_info=True)
    return {
        "removed": removed,
        "kept": kept,
        "errors": len(errors),
        "error_detail": errors[:40],
    }


def delete_image_chunk(chunk: ImageChunk) -> None:
    document = chunk.document
    if chunk.image:
        chunk.image.delete(save=False)
    chunk.delete()
    resequence_image_chunks(document)


_IMAGE_FILE_RE = re.compile(r"_(\d{3})_(\d{3})_img\.png$", re.I)


def _storage_has(storage, name: str) -> bool:
    name = (name or "").strip()
    if not name:
        return False
    try:
        return bool(storage.exists(name))
    except Exception:
        return False


def _list_document_pngs(storage, document_id) -> list[str]:
    prefix = f"dataset/images/{document_id}"
    try:
        _dirs, files = storage.listdir(prefix)
    except Exception:
        return []
    return [f"{prefix}/{name}" for name in files if str(name).lower().endswith(".png")]


def _page_from_png_name(path: str) -> int:
    match = _IMAGE_FILE_RE.search((path or "").rsplit("/", 1)[-1])
    return int(match.group(1)) if match else 0


def relink_missing_image_files(document: ImageSourceDocument) -> dict:
    """Gắn lại FileField nếu disk đã đổi tên nhưng DB vẫn trỏ path cũ."""
    rows = list(document.chunks.all())
    if not rows:
        return {"fixed": 0, "missing": 0}
    storage = rows[0].image.storage
    claimed: set[str] = set()
    for chunk in rows:
        name = (chunk.image.name or "").strip()
        if _storage_has(storage, name):
            claimed.add(name)

    ordered = sorted(rows, key=lambda item: (item.page_number or 1, item.page_seq or 1, item.position or 0))
    seq_by_page: dict[int, int] = {}
    intended: dict = {}
    for chunk in ordered:
        page = int(chunk.page_number or 1)
        seq_by_page[page] = seq_by_page.get(page, 0) + 1
        intended[chunk.pk] = format_image_chunk_code(document.catalog_code, page, seq_by_page[page])

    orphans_by_page: dict[int, list[str]] = {}
    for path in _list_document_pngs(storage, document.pk):
        if path in claimed:
            continue
        orphans_by_page.setdefault(_page_from_png_name(path), []).append(path)
    for page in orphans_by_page:
        orphans_by_page[page].sort()

    fixed = 0
    missing = 0
    changed: list[ImageChunk] = []
    for chunk in rows:
        name = (chunk.image.name or "").strip()
        if _storage_has(storage, name):
            continue
        candidates = []
        code = (chunk.chunk_code or "").strip()
        if code:
            candidates.append(image_chunk_png_name(document.pk, code))
        want = (intended.get(chunk.pk) or "").strip()
        if want:
            candidates.append(image_chunk_png_name(document.pk, want))
        found = ""
        for cand in candidates:
            if cand in claimed or not _storage_has(storage, cand):
                continue
            found = cand
            break
        if not found:
            pool = orphans_by_page.get(int(chunk.page_number or 1)) or []
            if pool:
                found = pool.pop(0)
        if found:
            chunk.image.name = found
            claimed.add(found)
            changed.append(chunk)
            fixed += 1
        else:
            missing += 1
    if changed:
        ImageChunk.objects.bulk_update(changed, ["image"])
    return {"fixed": fixed, "missing": missing}


def resequence_image_chunks(document: ImageSourceDocument) -> int:
    from apps.dataset.catalog import format_image_chunk_code, rename_image_chunk_file

    relink_missing_image_files(document)
    rows = list(document.chunks.select_related("box").order_by("page_number", "page_seq", "position"))
    if not rows:
        return 0
    # Tránh UniqueViolation khi bulk_update dồn position/mã còn trùng hàng chưa đổi.
    with transaction.atomic():
        for index, chunk in enumerate(rows, start=1):
            chunk.position = 1_000_000 + index
            chunk.chunk_code = f"__tmp_{chunk.pk.hex}_{index}"
        ImageChunk.objects.bulk_update(rows, ["position", "chunk_code"])
        seq_by_page: dict[int, int] = {}
        for index, chunk in enumerate(rows, start=1):
            page = int(chunk.page_number or 1)
            seq_by_page[page] = seq_by_page.get(page, 0) + 1
            chunk.position = index
            chunk.page_seq = seq_by_page[page]
            new_code = format_image_chunk_code(document.catalog_code, page, chunk.page_seq) or f"{chunk.box_id}_img"
            if (chunk.image.name or "").strip():
                rename_image_chunk_file(chunk, new_code)
            chunk.chunk_code = new_code
        ImageChunk.objects.bulk_update(rows, ["position", "page_seq", "chunk_code", "image"])
    return len(rows)
