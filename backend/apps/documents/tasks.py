from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from apps.documents.markdown import aggregate_markdown, page_display_text
from apps.documents.models import (
    Book,
    BookChunkingBatch,
    BookCleaningBatch,
    BookStatus,
    Box,
    ChunkingBatchStatus,
    CleaningBatchStatus,
    ContentType,
    Page,
    PageChunkedText,
    PageCleanedText,
    PageOcrMode,
)
from apps.documents.task_control import (
    TaskCancelled,
    acquire_chunking_lock,
    acquire_cleaning_lock,
    check_book_ocr_cancelled,
    check_chunking_cancelled,
    check_cleaning_cancelled,
    clear_book_ocr_cancel,
    clear_chunking_cancel,
    clear_cleaning_cancel,
    release_chunking_lock,
    release_cleaning_lock,
)
from apps.layout.models import DetectorConfig
from apps.llm.ocr_runner import get_fallback_provider, load_page_ocr_providers, ocr_full_page, OcrPageResult
from apps.llm.chunking_runner import (
    chunk_page_text,
    chunk_pages,
    count_breaks,
    iter_chunk_windows,
    normalize_chunk_window,
)
from apps.llm.text_cleaning_runner import clean_page_text
from bb_detection.pdf_validate import InvalidPdfError, validate_pdf
from ocr_platform.gpu_env import cuda_is_usable, normalize_cuda_visible_devices

logger = logging.getLogger(__name__)

OCR_PAGE_ATTEMPTS = 3
OCR_RETRY_BACKOFF_S = 2.0


def _write_temp_pdf(book: Book) -> str:
    with book.pdf_file.open("rb") as f:
        data = f.read()
    fd, tmp_path = tempfile.mkstemp(prefix="book_pdf_", suffix=".pdf")
    os.close(fd)
    Path(tmp_path).write_bytes(data)
    return tmp_path


def _save_filefield_from_path(field, path: str, *, name_hint: Optional[str] = None) -> None:
    p = Path(path)
    if not p.exists():
        return
    field.save(name_hint or p.name, ContentFile(p.read_bytes()), save=False)


def _page_image_bytes(page: Page) -> bytes:
    if not page.page_image:
        return b""
    with page.page_image.open("rb") as f:
        return f.read()


def _get_default_detector() -> DetectorConfig:
    cfg = DetectorConfig.objects.filter(is_active=True, is_default=True).first()
    if cfg:
        return cfg
    cfg = DetectorConfig.objects.filter(is_active=True).order_by("id").first()
    if cfg:
        return cfg
    return DetectorConfig.objects.create(name="default", is_default=True)


def _classify_page_from_boxes(bounding_boxes) -> tuple[bool, bool, str]:
    has_table = False
    has_formula = False
    for bb in bounding_boxes or []:
        ct = bb.content_type.value if hasattr(bb.content_type, "value") else str(bb.content_type)
        ct = ct.lower()
        if ct == ContentType.TABLE:
            has_table = True
        if ct == ContentType.FORMULA:
            has_formula = True
    ocr_mode = PageOcrMode.LAYOUT if (has_table or has_formula) else PageOcrMode.STANDARD
    return has_table, has_formula, ocr_mode


def _resolve_detector_device(det_cfg: DetectorConfig) -> str:
    if getattr(settings, "CELERY_FORCE_CPU", False):
        return "cpu"

    raw = (det_cfg.device or "cuda:0").strip().lower()
    if raw == "cpu":
        return "cpu"

    normalize_cuda_visible_devices()
    if not cuda_is_usable():
        logger.warning(
            "CUDA không khả dụng trong worker (%s) — doclayout-yolo chạy CPU",
            _gpu_env_summary(),
        )
        return "cpu"

    return "cuda:0"


def _gpu_env_summary() -> str:
    return (
        f"GPU_DEVICE_IDS={os.getenv('GPU_DEVICE_IDS', '?')} "
        f"NVIDIA_VISIBLE_DEVICES={os.getenv('NVIDIA_VISIBLE_DEVICES', '?')} "
        f"CUDA_VISIBLE_DEVICES={os.getenv('CUDA_VISIBLE_DEVICES', '?')}"
    )


def _redis_client():
    import redis

    return redis.from_url(settings.CELERY_BROKER_URL)


def _acquire_book_lock(book_id: str, task_id: str, *, ttl: int = 7200) -> bool:
    """Khóa Redis — tránh 2 worker OCR cùng một sách (gây xóa/trùng trang)."""
    try:
        return bool(_redis_client().set(f"ocr:lock:book:{book_id}", task_id, nx=True, ex=ttl))
    except Exception as e:
        logger.warning("Không tạo được lock Redis cho sách %s: %s", book_id, e)
        return True


def _release_book_lock(book_id: str, task_id: str) -> None:
    try:
        client = _redis_client()
        key = f"ocr:lock:book:{book_id}"
        if client.get(key) == task_id.encode():
            client.delete(key)
    except Exception as e:
        logger.warning("Không giải phóng lock Redis cho sách %s: %s", book_id, e)


def _save_page_ocr(page_pk, text: str, result: OcrPageResult | None = None) -> bool:
    """Cập nhật OCR trang + công cụ đã dùng — không raise nếu dòng đã bị xóa."""
    fields: Dict[str, Any] = {"text_md": text}
    if result is None:
        fields.update(
            ocr_provider_id=None,
            ocr_tool_name="",
            ocr_tool_backend="",
            ocr_fallback_from="",
        )
    else:
        fields.update(
            ocr_provider_id=result.provider.pk,
            ocr_tool_name=result.provider.name,
            ocr_tool_backend=result.provider.backend,
            ocr_fallback_from=result.requested.name if result.used_fallback else "",
        )
    updated = Page.objects.filter(pk=page_pk).update(**fields)
    if updated == 0:
        logger.warning("Trang %s không còn trong DB — bỏ qua lưu OCR", page_pk)
        return False
    return True


def _save_book_status(book: Book, *, status: str, error_message: str = "") -> None:
    book.status = status
    book.error_message = error_message
    updated = Book.objects.filter(pk=book.pk).update(
        status=status,
        error_message=error_message,
        updated_at=timezone.now(),
    )
    if updated == 0:
        raise RuntimeError(f"Sách {book.pk} không còn tồn tại trong DB")


def _book_lang_label(book: Book) -> str:
    return "Việt" if book.lang in {"vi", "vie", "vietnamese"} else book.lang


def _save_book_markdown(book: Book) -> None:
    md_content = aggregate_markdown(book)
    if book.markdown_file:
        book.markdown_file.delete(save=False)
    book.markdown_file.save(f"{book.slug}.md", ContentFile(md_content.encode("utf-8")), save=False)
    if not default_storage.exists(book.markdown_file.name):
        raise RuntimeError(f"Không ghi được markdown vào storage: {book.markdown_file.name}")
    Book.objects.filter(pk=book.pk).update(
        markdown_file=book.markdown_file.name,
        updated_at=timezone.now(),
    )


def _ensure_page_image_bytes(page: Page, pdf_bytes: bytes) -> bytes:
    data = _page_image_bytes(page)
    if data:
        return data
    from bb_detection.pdf_render import render_page_png

    det = _get_default_detector()
    png, w, h = render_page_png(
        pdf_bytes=pdf_bytes,
        page_number=int(page.page_number),
        dpi=int(det.dpi),
        use_cropbox=bool(det.use_cropbox),
        trim_scan=bool(det.trim_scan),
    )
    page.page_image.save(
        f"{page.book_id}/page_{int(page.page_number):04d}.png",
        ContentFile(png),
        save=False,
    )
    page.width = w
    page.height = h
    page.save(update_fields=["page_image", "width", "height"])
    return png


def _interruptible_sleep(seconds: float, book_id: str | None) -> None:
    deadline = time.perf_counter() + max(0.0, seconds)
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if book_id:
            check_book_ocr_cancelled(book_id)
        time.sleep(min(0.5, remaining))


def _ocr_full_page_with_retries(
    *,
    page_number: int,
    book_id: str | None = None,
    total_pages: int | None = None,
    mode_label: str = "",
    **kwargs,
) -> OcrPageResult:
    """OCR một trang, thử tối đa OCR_PAGE_ATTEMPTS lần khi API lỗi tạm thời."""
    last_exc: Exception | None = None
    where = f"trang {page_number}"
    if total_pages:
        where = f"trang {page_number}/{total_pages}"
    if mode_label:
        where = f"{where} ({mode_label})"
    extra = {"book_id": book_id} if book_id else {}

    for attempt in range(1, OCR_PAGE_ATTEMPTS + 1):
        if book_id:
            check_book_ocr_cancelled(book_id)
        try:
            return ocr_full_page(**kwargs)
        except TaskCancelled:
            raise
        except Exception as e:
            last_exc = e
            if attempt < OCR_PAGE_ATTEMPTS:
                delay = OCR_RETRY_BACKOFF_S * (2 ** (attempt - 1))
                logger.warning(
                    "OCR %s lỗi (lần %s/%s): %s — thử lại sau %.0fs",
                    where,
                    attempt,
                    OCR_PAGE_ATTEMPTS,
                    e,
                    delay,
                    extra=extra,
                )
                _interruptible_sleep(delay, book_id)
                continue
            logger.error(
                "OCR %s thất bại sau %s lần thử: %s",
                where,
                OCR_PAGE_ATTEMPTS,
                e,
                extra=extra,
            )
            raise
    raise last_exc or RuntimeError("OCR thất bại.")


def _ocr_page_failures_message(failed_details: list[str], *, attempts: int) -> str:
    preview = failed_details[:30]
    msg = (
        f"{len(failed_details)} trang OCR lỗi sau {attempts} lần thử — "
        + "; ".join(preview)
    )
    if len(failed_details) > 30:
        msg += f"; … và {len(failed_details) - 30} trang khác."
    return msg


def _ocr_page_text(page: Page, *, image_bytes: bytes, pdf_bytes: bytes) -> OcrPageResult:
    page_providers = load_page_ocr_providers(page.book)
    fallback_provider = get_fallback_provider()
    provider = page_providers.get(page.ocr_mode) or fallback_provider
    return _ocr_full_page_with_retries(
        page_number=int(page.page_number),
        book_id=str(page.book_id),
        provider=provider,
        page_image_bytes=image_bytes,
        ocr_mode=page.ocr_mode,
        lang=_book_lang_label(page.book),
        pdf_bytes=pdf_bytes,
        page_index=int(page.page_number) - 1,
        layout_boxes=list(page.boxes.all()),
        page_width=float(page.width or 0),
        page_height=float(page.height or 0),
    )


@shared_task(bind=True, name="apps.documents.tasks.process_book")
def process_book(self, *, book_id: str) -> Dict[str, Any]:
    """
    Pipeline: PDF → doclayout-yolo (phát hiện bbox, phân loại trang) → OCR cả trang → Markdown.
    """
    task_id = self.request.id or "local"
    if not _acquire_book_lock(book_id, task_id):
        logger.warning("Sách %s đang được OCR bởi task khác — bỏ qua", book_id)
        return {"book_id": book_id, "skipped": True, "reason": "already_processing"}

    clear_book_ocr_cancel(book_id)

    t0 = time.perf_counter()
    book = Book.objects.get(id=book_id)
    _save_book_status(book, status=BookStatus.PROCESSING, error_message="")

    tmp_pdf = None
    tmp_outdir = None

    try:
        check_book_ocr_cancelled(book_id)

        det_cfg = _get_default_detector()
        page_providers = load_page_ocr_providers(book)
        fallback_provider = get_fallback_provider()

        logger.info(
            "OCR sách «%s» — trang thường: %s | trang bảng/công thức: %s",
            book.title,
            page_providers[PageOcrMode.STANDARD].name,
            page_providers[PageOcrMode.LAYOUT].name,
            extra={"book_id": str(book.id)},
        )

        tmp_pdf = _write_temp_pdf(book)
        ok, pdf_err = validate_pdf(pdf_path=tmp_pdf)
        if not ok:
            _save_book_status(book, status=BookStatus.FAILED, error_message=pdf_err)
            logger.warning(
                "PDF không hợp lệ — %s: %s",
                book.title,
                pdf_err,
                extra={"book_id": str(book.id)},
            )
            return {"book_id": str(book.id), "failed": True, "error": pdf_err}

        tmp_outdir = tempfile.mkdtemp(prefix="detect_out_")

        from bb_detection.detector_factory import DetectorFactory

        device = _resolve_detector_device(det_cfg)
        logger.info(
            "doclayout-yolo device=%s (%s)",
            device,
            _gpu_env_summary(),
            extra={"book_id": str(book.id)},
        )

        repo_root = Path(settings.BASE_DIR).parent
        model_path = str(repo_root / det_cfg.model_path)

        detector = DetectorFactory.create(
            "doclayout_yolo",
            output_dir=tmp_outdir,
            weights_path=model_path,
            conf=float(det_cfg.confidence),
            imgsz=int(det_cfg.imgsz),
            device=device,
        )
        det_result = detector.detect_document(
            tmp_pdf,
            dpi=int(det_cfg.dpi),
            use_cropbox=bool(det_cfg.use_cropbox),
            trim_scan=bool(det_cfg.trim_scan),
        )
        check_book_ocr_cancelled(book_id)
        logger.info(
            "Detect xong %s trang — lưu DB + OCR cả trang...",
            len(det_result.pages),
            extra={"book_id": str(book.id), "book": book.title},
        )

        layout_pages = 0
        standard_pages = 0

        with transaction.atomic():
            Page.objects.filter(book=book).delete()

            total_boxes = 0
            for page_data in det_result.pages:
                has_table, has_formula, ocr_mode = _classify_page_from_boxes(page_data.bounding_boxes)
                if ocr_mode == PageOcrMode.LAYOUT:
                    layout_pages += 1
                else:
                    standard_pages += 1

                page = Page(
                    book=book,
                    page_number=int(page_data.page_number),
                    width=page_data.page_width,
                    height=page_data.page_height,
                    ocr_mode=ocr_mode,
                    has_table=has_table,
                    has_formula=has_formula,
                )
                if page_data.page_image_path:
                    _save_filefield_from_path(
                        page.page_image,
                        page_data.page_image_path,
                        name_hint=f"{book.id}/page_{int(page_data.page_number):04d}.png",
                    )
                page.save()

                for idx, bb in enumerate(page_data.bounding_boxes or []):
                    ct = (bb.content_type.value if hasattr(bb.content_type, "value") else str(bb.content_type)).lower()
                    if ct not in {c.value for c in ContentType}:
                        ct = ContentType.UNKNOWN
                    Box.objects.create(
                        page=page,
                        reading_order=int(idx),
                        content_type=ct,
                        x=float(getattr(bb, "x", 0.0)),
                        y=float(getattr(bb, "y", 0.0)),
                        width=float(getattr(bb, "width", 0.0)),
                        height=float(getattr(bb, "height", 0.0)),
                        confidence=float(getattr(bb, "confidence", 0.0) or 0.0),
                    )
                    total_boxes += 1

        logger.info(
            "Đã lưu %s trang, %s bbox (%s có bảng/công thức / %s thường) — bắt đầu OCR cả trang...",
            book.pages.count(),
            total_boxes,
            layout_pages,
            standard_pages,
            extra={"book_id": str(book.id)},
        )

        ocr_done = 0
        ocr_failed = 0
        failed_details: list[str] = []
        lang_label = "Việt" if book.lang in {"vi", "vie", "vietnamese"} else book.lang
        pages = Page.objects.filter(book=book).order_by("page_number")
        page_count = book.pages.count()
        pdf_bytes = Path(tmp_pdf).read_bytes()

        for page in pages.iterator(chunk_size=50):
            check_book_ocr_cancelled(book_id)
            page_pk = page.pk
            image_bytes = _page_image_bytes(page)
            if not image_bytes:
                _save_page_ocr(page_pk, "")
                ocr_failed += 1
                failed_details.append(f"trang {page.page_number}: không có ảnh trang")
                continue

            provider = page_providers.get(page.ocr_mode) or fallback_provider
            mode_label = "layout-parser" if page.ocr_mode == PageOcrMode.LAYOUT else "enterprise-ocr"
            t_page = time.perf_counter()
            try:
                result = _ocr_full_page_with_retries(
                    page_number=int(page.page_number),
                    book_id=book_id,
                    total_pages=page_count,
                    mode_label=mode_label,
                    provider=provider,
                    page_image_bytes=image_bytes,
                    ocr_mode=page.ocr_mode,
                    lang=lang_label,
                    pdf_bytes=pdf_bytes,
                    page_index=int(page.page_number) - 1,
                    layout_boxes=list(page.boxes.all()),
                    page_width=float(page.width or 0),
                    page_height=float(page.height or 0),
                )
                Box.objects.filter(page_id=page_pk).update(text_md="")

                text = (result.text or "").strip() or "[KHÔNG_ĐỌC_ĐƯỢC]"
                page.text_md = text
                if _save_page_ocr(page_pk, text, result):
                    ocr_done += 1
                else:
                    ocr_failed += 1
                    failed_details.append(f"trang {page.page_number}: không còn trong DB")
                    continue
                used = result.provider.name
                if result.used_fallback:
                    used = f"{used} (fallback từ {result.requested.name})"
                logger.info(
                    "OCR trang %s/%s (%s, %s) OK — %.1fs, %s ký tự",
                    page.page_number,
                    page_count,
                    mode_label,
                    used,
                    time.perf_counter() - t_page,
                    len(page.text_md),
                    extra={"book_id": str(book.id)},
                )
            except TaskCancelled:
                raise
            except Exception as e:
                _save_page_ocr(page_pk, "")
                ocr_failed += 1
                failed_details.append(f"trang {page.page_number}: {e}")

        _save_book_markdown(book)
        error_message = ""
        if failed_details:
            error_message = _ocr_page_failures_message(failed_details, attempts=OCR_PAGE_ATTEMPTS)
            logger.error(
                "OCR sách %s: %s",
                book.title,
                error_message,
                extra={"book_id": str(book.id)},
            )
        _save_book_status(book, status=BookStatus.DONE, error_message=error_message)
        logger.info(
            "Đã lưu markdown: %s",
            book.markdown_file.name,
            extra={"book_id": str(book.id)},
        )

        logger.info(
            "Hoàn thành OCR %s — %s trang OK, %s lỗi, %.1fs",
            book.title,
            ocr_done,
            ocr_failed,
            time.perf_counter() - t0,
            extra={"book_id": str(book.id)},
        )

        return {
            "book_id": str(book.id),
            "pages": book.pages.count(),
            "boxes": total_boxes,
            "layout_pages": layout_pages,
            "standard_pages": standard_pages,
            "ocr_done": ocr_done,
            "ocr_failed": ocr_failed,
            "duration_s": round(time.perf_counter() - t0, 2),
            "detector": det_cfg.name,
            "ocr_provider": fallback_provider.name,
        }

    except TaskCancelled as e:
        _save_book_status(book, status=BookStatus.NEW, error_message=str(e))
        logger.info("OCR sách %s đã hủy", book.title, extra={"book_id": str(book.id)})
        return {"book_id": str(book.id), "cancelled": True, "error": str(e)}

    except InvalidPdfError as e:
        _save_book_status(book, status=BookStatus.FAILED, error_message=str(e))
        logger.warning("PDF không hợp lệ — %s: %s", book.title, e, extra={"book_id": str(book.id)})
        return {"book_id": str(book.id), "failed": True, "error": str(e)}

    except Exception as e:
        msg = str(e).strip() or type(e).__name__
        if type(e).__name__ == "PDFPageCountError" or "xref" in msg.lower() or "trailer" in msg.lower():
            msg = (
                "PDF hỏng hoặc không phải file PDF hợp lệ. "
                "Hãy mở file bằng phần mềm đọc PDF, lưu bản PDF mới rồi upload lại."
            )
        _save_book_status(book, status=BookStatus.FAILED, error_message=msg)
        logger.exception("OCR sách %s thất bại", book.title, extra={"book_id": str(book.id)})
        return {"book_id": str(book.id), "failed": True, "error": msg}

    finally:
        _release_book_lock(book_id, task_id)
        clear_book_ocr_cancel(book_id)
        if tmp_pdf:
            Path(tmp_pdf).unlink(missing_ok=True)
        if tmp_outdir:
            import shutil

            shutil.rmtree(tmp_outdir, ignore_errors=True)


@shared_task(bind=True, name="apps.documents.tasks.ocr_single_page")
def ocr_single_page(self, *, page_id: str) -> Dict[str, Any]:
    """OCR lại một trang — không detect lại cả sách, không xóa các trang khác."""
    page = Page.objects.select_related("book").get(id=page_id)
    book = page.book
    if book.status == BookStatus.PROCESSING:
        logger.warning(
            "Bỏ qua OCR trang %s — sách %s đang OCR cả cuốn",
            page.page_number,
            book.title,
            extra={"book_id": str(book.id), "page_id": page_id},
        )
        return {"page_id": page_id, "skipped": True, "reason": "book_processing"}

    tmp_pdf = None
    t0 = time.perf_counter()
    try:
        tmp_pdf = _write_temp_pdf(book)
        pdf_bytes = Path(tmp_pdf).read_bytes()
        image_bytes = _ensure_page_image_bytes(page, pdf_bytes)
        if not image_bytes:
            _save_page_ocr(page.pk, "")
            return {"page_id": page_id, "failed": True, "error": "Không có ảnh trang"}

        result = _ocr_page_text(page, image_bytes=image_bytes, pdf_bytes=pdf_bytes)
        text = (result.text or "").strip() or "[KHÔNG_ĐỌC_ĐƯỢC]"
        saved = _save_page_ocr(page.pk, text, result)
        if not saved:
            return {"page_id": page_id, "failed": True, "error": "Trang không còn trong DB"}

        Box.objects.filter(page_id=page.pk).update(text_md="")
        PageCleanedText.objects.filter(page_id=page.pk).delete()
        _save_book_markdown(book)
        used = result.provider.name
        if result.used_fallback:
            used = f"{used} (fallback từ {result.requested.name})"
        logger.info(
            "OCR lại trang %s/%s (%s) OK — %.1fs, %s ký tự",
            page.page_number,
            book.pages.count(),
            used,
            time.perf_counter() - t0,
            len(text),
            extra={"book_id": str(book.id), "page_id": page_id},
        )
        return {
            "page_id": page_id,
            "book_id": str(book.id),
            "page_number": page.page_number,
            "chars": len(text),
            "ocr_tool": used,
            "duration_s": round(time.perf_counter() - t0, 2),
        }
    except TaskCancelled:
        raise
    except Exception as e:
        logger.error(
            "OCR lại trang %s sách %s thất bại: %s",
            page.page_number,
            book.title,
            e,
            extra={"book_id": str(book.id), "page_id": page_id},
        )
        return {"page_id": page_id, "failed": True, "error": str(e)}
    finally:
        if tmp_pdf:
            Path(tmp_pdf).unlink(missing_ok=True)


def _clean_and_store_page(*, batch: BookCleaningBatch, page: Page) -> tuple[bool, int, str]:
    """Làm sạch một trang, lưu PageCleanedText, không ghi đè OCR.

    Trả về (ok, số ký tự, mã lỗi). 'empty' = trang chưa có OCR.
    """
    ocr_text = (page.text_md or "").strip()
    if not ocr_text:
        PageCleanedText.objects.update_or_create(
            batch=batch,
            page=page,
            defaults={"text_md": ""},
        )
        return False, 0, "empty"

    try:
        cleaned = clean_page_text(
            provider=batch.provider,
            prompt_template=batch.prompt,
            book_title=page.book.title,
            page_number=page.page_number,
            ocr_text=ocr_text,
            model=getattr(batch, "model", "") or "",
        )
        text = cleaned or ocr_text
        PageCleanedText.objects.update_or_create(
            batch=batch,
            page=page,
            defaults={"text_md": text},
        )
        return True, len(text), ""
    except Exception as e:
        PageCleanedText.objects.update_or_create(
            batch=batch,
            page=page,
            defaults={"text_md": ocr_text},
        )
        return False, len(ocr_text), str(e)


@shared_task(bind=True, name="apps.documents.tasks.clean_single_page")
def clean_single_page(self, *, page_id: str, batch_id: str) -> Dict[str, Any]:
    """Làm sạch một trang — ghi vào batch đang dùng, không đụng OCR gốc."""
    task_id = self.request.id or "local"
    page = Page.objects.select_related("book").get(id=page_id)
    book = page.book
    batch = BookCleaningBatch.objects.select_related("book", "provider").get(id=batch_id)

    if book.cleaning_batches.filter(
        status__in=[CleaningBatchStatus.PENDING, CleaningBatchStatus.PROCESSING]
    ).exclude(pk=batch.pk).exists():
        logger.warning(
            "Bỏ qua làm sạch trang %s — sách %s đang làm sạch cả cuốn",
            page.page_number,
            book.title,
            extra={"book_id": str(book.id), "page_id": page_id},
        )
        return {"page_id": page_id, "skipped": True, "reason": "book_cleaning"}

    page_lock_id = f"page:{page_id}"
    if not acquire_cleaning_lock(page_lock_id, task_id):
        logger.warning("Trang %s đang được làm sạch — bỏ qua", page_id)
        return {"page_id": page_id, "skipped": True, "reason": "already_processing"}

    if not acquire_cleaning_lock(batch_id, task_id):
        release_cleaning_lock(page_lock_id, task_id)
        logger.warning("Batch làm sạch %s đang chạy — bỏ qua trang %s", batch_id, page_id)
        return {"page_id": page_id, "skipped": True, "reason": "batch_processing"}

    t0 = time.perf_counter()
    was_done = batch.status == CleaningBatchStatus.DONE
    original_error = batch.error_message or ""
    try:
        check_cleaning_cancelled(batch_id)
        BookCleaningBatch.objects.filter(pk=batch.pk).update(
            status=CleaningBatchStatus.PROCESSING,
            error_message="" if not was_done else original_error,
            updated_at=timezone.now(),
        )

        check_cleaning_cancelled(batch_id)
        ok, chars, error = _clean_and_store_page(batch=batch, page=page)
        check_cleaning_cancelled(batch_id)
        if error == "empty":
            BookCleaningBatch.objects.filter(pk=batch.pk).update(
                status=CleaningBatchStatus.DONE if was_done else CleaningBatchStatus.FAILED,
                error_message=original_error if was_done else "Trang chưa có nội dung OCR.",
                updated_at=timezone.now(),
            )
            return {"page_id": page_id, "failed": True, "error": "empty"}

        status = CleaningBatchStatus.DONE
        error_message = ""
        if not ok:
            error_message = f"Trang {page.page_number}: {error} — đã giữ OCR gốc."
        BookCleaningBatch.objects.filter(pk=batch.pk).update(
            status=status,
            error_message=error_message if not was_done else (original_error or error_message),
            updated_at=timezone.now(),
        )
        book.refresh_cleaning_coverage()
        logger.info(
            "Làm sạch trang %s sách %s %s — %.1fs, %s ký tự",
            page.page_number,
            book.title,
            "OK" if ok else "lỗi",
            time.perf_counter() - t0,
            chars,
            extra={"book_id": str(book.id), "page_id": page_id, "batch_id": batch_id},
        )
        return {
            "page_id": page_id,
            "batch_id": batch_id,
            "ok": ok,
            "chars": chars,
            "duration_s": round(time.perf_counter() - t0, 2),
            "error": error,
        }
    except TaskCancelled as e:
        BookCleaningBatch.objects.filter(pk=batch.pk).update(
            status=CleaningBatchStatus.DONE if was_done else CleaningBatchStatus.FAILED,
            error_message=original_error if was_done else str(e),
            updated_at=timezone.now(),
        )
        logger.info(
            "Làm sạch trang %s sách %s đã hủy",
            page.page_number,
            book.title,
            extra={"book_id": str(book.id), "page_id": page_id, "batch_id": batch_id},
        )
        return {"page_id": page_id, "cancelled": True, "error": str(e)}
    except Exception as e:
        BookCleaningBatch.objects.filter(pk=batch.pk).update(
            status=CleaningBatchStatus.DONE if was_done else CleaningBatchStatus.FAILED,
            error_message=original_error if was_done else f"{type(e).__name__}: {e}",
            updated_at=timezone.now(),
        )
        raise
    finally:
        release_cleaning_lock(page_lock_id, task_id)
        release_cleaning_lock(batch_id, task_id)
        clear_cleaning_cancel(batch_id)


@shared_task(bind=True, name="apps.documents.tasks.clean_book_text")
def clean_book_text(self, *, batch_id: str) -> Dict[str, Any]:
    """Làm sạch văn bản OCR từng trang bằng LLM — lưu record mới, không ghi đè OCR."""
    task_id = self.request.id or "local"
    if not acquire_cleaning_lock(batch_id, task_id):
        logger.warning("Batch làm sạch %s đang chạy — bỏ qua", batch_id)
        return {"batch_id": batch_id, "skipped": True, "reason": "already_processing"}

    batch = BookCleaningBatch.objects.select_related("book", "provider").get(id=batch_id)
    book = batch.book

    BookCleaningBatch.objects.filter(pk=batch.pk).update(
        status=CleaningBatchStatus.PROCESSING,
        error_message="",
        updated_at=timezone.now(),
    )

    pages = list(Page.objects.filter(book=book).select_related("book").order_by("page_number"))
    done = 0
    failed = 0
    t0 = time.perf_counter()

    try:
        check_cleaning_cancelled(batch_id)

        for page in pages:
            check_cleaning_cancelled(batch_id)
            t_page = time.perf_counter()
            ok, chars, error = _clean_and_store_page(batch=batch, page=page)
            if error == "empty":
                continue
            if ok:
                done += 1
                logger.info(
                    "Làm sạch trang %s/%s OK — %.1fs, %s ký tự",
                    page.page_number,
                    len(pages),
                    time.perf_counter() - t_page,
                    chars,
                    extra={"book_id": str(book.id), "batch_id": str(batch.id)},
                )
            else:
                failed += 1
                logger.warning(
                    "Làm sạch trang %s/%s lỗi: %s — giữ OCR gốc",
                    page.page_number,
                    len(pages),
                    error,
                    extra={"book_id": str(book.id), "batch_id": str(batch.id)},
                )

        error_message = ""
        if failed:
            error_message = f"{failed}/{len(pages)} trang lỗi — đã giữ OCR gốc cho các trang đó."

        BookCleaningBatch.objects.filter(pk=batch.pk).update(
            status=CleaningBatchStatus.DONE,
            error_message=error_message,
            updated_at=timezone.now(),
        )
        book.refresh_cleaning_coverage()

        logger.info(
            "Hoàn thành làm sạch %s — %s trang OK, %s lỗi, %.1fs",
            book.title,
            done,
            failed,
            time.perf_counter() - t0,
            extra={"book_id": str(book.id), "batch_id": str(batch.id)},
        )

        return {
            "batch_id": str(batch.id),
            "book_id": str(book.id),
            "pages": len(pages),
            "done": done,
            "failed": failed,
            "duration_s": round(time.perf_counter() - t0, 2),
        }

    except TaskCancelled as e:
        BookCleaningBatch.objects.filter(pk=batch.pk).update(
            status=CleaningBatchStatus.FAILED,
            error_message=str(e),
            updated_at=timezone.now(),
        )
        logger.info("Làm sạch sách %s đã hủy", book.title, extra={"book_id": str(book.id), "batch_id": batch_id})
        return {"batch_id": batch_id, "cancelled": True, "error": str(e)}

    except Exception as e:
        BookCleaningBatch.objects.filter(pk=batch.pk).update(
            status=CleaningBatchStatus.FAILED,
            error_message=f"{type(e).__name__}: {e}",
            updated_at=timezone.now(),
        )
        raise

    finally:
        release_cleaning_lock(batch_id, task_id)
        clear_cleaning_cancel(batch_id)


def _store_page_chunk(*, batch: BookChunkingBatch, page: Page, text: str) -> int:
    breaks = count_breaks(text or "")
    PageChunkedText.objects.update_or_create(
        batch=batch,
        page=page,
        defaults={"text_md": text or "", "break_count": breaks},
    )
    return breaks


def _chunk_and_store_page(*, batch: BookChunkingBatch, page: Page) -> tuple[bool, int, int, str]:
    """Phân đoạn một trang (sách chỉ có 1 trang có chữ).

    Trả về (ok, số ký tự, số </break>, mã lỗi).
    """
    source = page_display_text(page)
    if not source.strip():
        _store_page_chunk(batch=batch, page=page, text="")
        return False, 0, 0, "empty"

    try:
        chunked = chunk_page_text(
            provider=batch.provider,
            prompt_template=batch.prompt,
            book_title=page.book.title,
            page_number=page.page_number,
            page_text=source,
            model=getattr(batch, "model", "") or "",
        )
        text = chunked or source
        breaks = _store_page_chunk(batch=batch, page=page, text=text)
        return True, len(text), breaks, ""
    except Exception as e:
        _store_page_chunk(batch=batch, page=page, text=source)
        return False, len(source), 0, str(e)


def _chunk_pages_window(
    *,
    batch: BookChunkingBatch,
    book_title: str,
    items: list[tuple[Page, str]],
    lock_prefix: int,
    kept_texts: list[str],
) -> tuple[list[str], str]:
    """Gửi một cửa sổ trang. Trả về (texts, error)."""
    origs = [orig for _page, orig in items]
    fallback = [
        kept_texts[i] if i < lock_prefix and i < len(kept_texts) and (kept_texts[i] or "").strip() else orig
        for i, orig in enumerate(origs)
    ]
    try:
        texts = chunk_pages(
            provider=batch.provider,
            prompt_template=batch.prompt,
            book_title=book_title,
            pages=[(page.page_number, orig) for page, orig in items],
            model=getattr(batch, "model", "") or "",
            lock_prefix=lock_prefix,
            kept_texts=kept_texts,
        )
        if len(texts) != len(items):
            return fallback, "LLM trả về số trang không khớp cửa sổ."
        return [text or fallback[i] for i, text in enumerate(texts)], ""
    except Exception as e:
        return fallback, str(e)


@shared_task(bind=True, name="apps.documents.tasks.chunk_book_text")
def chunk_book_text(self, *, batch_id: str) -> Dict[str, Any]:
    """Phân đoạn theo cửa sổ N trang, trùng M trang giữa hai lần liên tiếp."""
    task_id = self.request.id or "local"
    if not acquire_chunking_lock(batch_id, task_id):
        logger.warning("Batch phân đoạn %s đang chạy — bỏ qua", batch_id)
        return {"batch_id": batch_id, "skipped": True, "reason": "already_processing"}

    batch = BookChunkingBatch.objects.select_related("book", "provider").get(id=batch_id)
    book = batch.book
    window_pages, overlap_pages = normalize_chunk_window(
        getattr(batch, "window_pages", None),
        getattr(batch, "overlap_pages", None),
    )

    BookChunkingBatch.objects.filter(pk=batch.pk).update(
        status=ChunkingBatchStatus.PROCESSING,
        error_message="",
        updated_at=timezone.now(),
    )

    pages = list(Page.objects.filter(book=book).select_related("book").order_by("page_number"))
    done = 0
    failed = 0
    total_breaks = 0
    t0 = time.perf_counter()

    try:
        check_chunking_cancelled(batch_id)

        work: list[tuple[Page, str]] = []
        for page in pages:
            source = page_display_text(page)
            if not source.strip():
                _store_page_chunk(batch=batch, page=page, text="")
                continue
            work.append((page, source))

        results: dict[int, str] = {}
        if not work:
            pass
        elif len(work) == 1:
            page, _source = work[0]
            t_page = time.perf_counter()
            ok, chars, breaks, error = _chunk_and_store_page(batch=batch, page=page)
            if ok:
                done += 1
                logger.info(
                    "Phân đoạn trang %s/%s OK — %.1fs, %s ký tự, %s mốc",
                    page.page_number,
                    len(pages),
                    time.perf_counter() - t_page,
                    chars,
                    breaks,
                    extra={"book_id": str(book.id), "batch_id": str(batch.id)},
                )
            else:
                failed += 1
                logger.warning(
                    "Phân đoạn trang %s/%s lỗi: %s — giữ bản gốc",
                    page.page_number,
                    len(pages),
                    error,
                    extra={"book_id": str(book.id), "batch_id": str(batch.id)},
                )
        else:
            for start, end, lock_prefix in iter_chunk_windows(len(work), window_pages, overlap_pages):
                check_chunking_cancelled(batch_id)
                items = work[start:end]
                kept_texts = [
                    results.get(page.pk, orig) if i < lock_prefix else orig
                    for i, (page, orig) in enumerate(items)
                ]
                t_win = time.perf_counter()
                texts, error = _chunk_pages_window(
                    batch=batch,
                    book_title=book.title,
                    items=items,
                    lock_prefix=lock_prefix,
                    kept_texts=kept_texts,
                )
                break_counts = []
                for (page, _orig), text in zip(items, texts):
                    break_counts.append(_store_page_chunk(batch=batch, page=page, text=text))
                    results[page.pk] = text
                first_page = items[0][0].page_number
                last_page = items[-1][0].page_number
                new_count = len(items) - lock_prefix
                if error:
                    failed += 1
                    logger.warning(
                        "Phân đoạn cửa sổ trang %s–%s lỗi: %s — giữ bản đã có",
                        first_page,
                        last_page,
                        error,
                        extra={"book_id": str(book.id), "batch_id": str(batch.id)},
                    )
                else:
                    done += new_count
                    logger.info(
                        "Phân đoạn cửa sổ trang %s–%s (khóa %s, mới %s) OK — %.1fs, mốc %s",
                        first_page,
                        last_page,
                        lock_prefix,
                        new_count,
                        time.perf_counter() - t_win,
                        "+".join(str(c) for c in break_counts),
                        extra={"book_id": str(book.id), "batch_id": str(batch.id)},
                    )

        total_breaks = sum(
            PageChunkedText.objects.filter(batch=batch).values_list("break_count", flat=True)
        )

        error_message = ""
        if failed:
            error_message = f"{failed} cửa sổ phân đoạn lỗi — đã giữ bản đã có cho các trang đó."
        if total_breaks:
            extra = f"Đã chèn {total_breaks} mốc </break>."
            error_message = f"{error_message} {extra}".strip() if error_message else extra
        window_note = f"Cửa sổ {window_pages} trang, trùng {overlap_pages}."
        error_message = f"{window_note} {error_message}".strip() if error_message else window_note

        BookChunkingBatch.objects.filter(pk=batch.pk).update(
            status=ChunkingBatchStatus.DONE,
            error_message=error_message,
            updated_at=timezone.now(),
        )

        logger.info(
            "Hoàn thành phân đoạn %s — cửa sổ %s trùng %s, %s trang mới OK, %s lỗi, %s mốc, %.1fs",
            book.title,
            window_pages,
            overlap_pages,
            done,
            failed,
            total_breaks,
            time.perf_counter() - t0,
            extra={"book_id": str(book.id), "batch_id": str(batch.id)},
        )

        return {
            "batch_id": str(batch.id),
            "book_id": str(book.id),
            "pages": len(pages),
            "window_pages": window_pages,
            "overlap_pages": overlap_pages,
            "done": done,
            "failed": failed,
            "breaks": total_breaks,
            "duration_s": round(time.perf_counter() - t0, 2),
        }

    except TaskCancelled as e:
        BookChunkingBatch.objects.filter(pk=batch.pk).update(
            status=ChunkingBatchStatus.FAILED,
            error_message=str(e),
            updated_at=timezone.now(),
        )
        logger.info("Phân đoạn sách %s đã hủy", book.title, extra={"book_id": str(book.id), "batch_id": batch_id})
        return {"batch_id": batch_id, "cancelled": True, "error": str(e)}

    except Exception as e:
        BookChunkingBatch.objects.filter(pk=batch.pk).update(
            status=ChunkingBatchStatus.FAILED,
            error_message=f"{type(e).__name__}: {e}",
            updated_at=timezone.now(),
        )
        raise

    finally:
        release_chunking_lock(batch_id, task_id)
        clear_chunking_cancel(batch_id)


@shared_task(bind=True, name="apps.documents.tasks.classify_book")
def classify_book(self, *, book_id: str) -> Dict[str, Any]:
    """Đọc ~20 trang đầu và điền metadata + phân loại bằng LLM."""
    from apps.llm.book_classify_runner import classify_book_metadata

    book = Book.objects.get(id=book_id)
    result = classify_book_metadata(book)
    logger.info(
        "Đã phân loại «%s» bằng %s: %s",
        book.title,
        result.get("provider"),
        ", ".join(result.get("filled") or []) or "(không điền được trường nào)",
    )
    return {"book_id": book_id, **{k: v for k, v in result.items() if k != "raw"}}


@shared_task(bind=True, name="apps.documents.tasks.ingest_staged_books")
def ingest_staged_books(self, *, staged_ids: list[str] | None = None) -> Dict[str, Any]:
    """OCR trang đầu + LLM đặt tên / lọc trùng cho các PDF trong danh sách tạm."""
    from apps.documents.ingest import process_staged_book
    from apps.documents.models import StagedBook, StagedBookStatus
    from apps.documents.task_control import (
        acquire_ingest_lock,
        check_ingest_cancelled,
        clear_ingest_cancel,
        refresh_ingest_lock,
        release_ingest_lock,
    )

    task_id = self.request.id or "local"
    if not acquire_ingest_lock(task_id):
        logger.warning("Đang có lần nhập sách chạy — bỏ qua")
        return {"skipped": True, "reason": "already_processing"}

    qs = StagedBook.objects.filter(status=StagedBookStatus.PENDING).order_by("created_at")
    if staged_ids:
        qs = StagedBook.objects.filter(pk__in=staged_ids).exclude(
            status__in=[StagedBookStatus.CREATED, StagedBookStatus.DUPLICATE]
        ).order_by("created_at")

    items = list(qs)
    created = 0
    duplicates = 0
    failed = 0
    t0 = time.perf_counter()
    logger.info("Bắt đầu nhập %s sách tạm", len(items))

    try:
        check_ingest_cancelled()
        for index, staged in enumerate(items, start=1):
            check_ingest_cancelled()
            refresh_ingest_lock(task_id)
            result = process_staged_book(staged)
            status = result.get("status")
            if status == "created":
                created += 1
            elif status == "duplicate":
                duplicates += 1
            else:
                failed += 1
            logger.info(
                "Nhập tạm %s/%s «%s» → %s",
                index,
                len(items),
                staged.original_name,
                status,
                extra={"staged_id": str(staged.id)},
            )
        return {
            "total": len(items),
            "created": created,
            "duplicates": duplicates,
            "failed": failed,
            "duration_s": round(time.perf_counter() - t0, 2),
        }
    except TaskCancelled as e:
        StagedBook.objects.filter(
            pk__in=[s.pk for s in items],
            status=StagedBookStatus.PROCESSING,
        ).update(status=StagedBookStatus.PENDING, error_message=str(e), updated_at=timezone.now())
        return {"cancelled": True, "created": created, "duplicates": duplicates, "failed": failed}
    finally:
        release_ingest_lock(task_id)
        clear_ingest_cancel()


@shared_task(bind=True, name="apps.documents.tasks.check_duplicate_book_titles")
def check_duplicate_book_titles(self, *, report_id: str) -> Dict[str, Any]:
    """Chia tên sách thành lô ~30, gọi LLM tìm nhóm trùng."""
    from apps.documents.models import Book, BookDuplicateReport, DuplicateReportStatus
    from apps.documents.task_control import acquire_dedupe_lock, release_dedupe_lock
    from apps.llm.book_ingest_runner import find_duplicate_title_groups, get_dedupe_config

    task_id = self.request.id or "local"
    if not acquire_dedupe_lock(task_id):
        logger.warning("Đang có lần kiểm tra trùng chạy — bỏ qua")
        return {"skipped": True, "reason": "already_processing"}

    report = BookDuplicateReport.objects.get(id=report_id)
    BookDuplicateReport.objects.filter(pk=report.pk).update(
        status=DuplicateReportStatus.PROCESSING,
        error_message="",
        updated_at=timezone.now(),
    )

    try:
        config = get_dedupe_config()
        batch_size = max(2, min(int(config.batch_size or 30), 50))
        rows = list(Book.objects.order_by("title").values("id", "title"))
        rows.sort(key=lambda r: (r["title"] or "").casefold())

        title_to_books: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            title = (row["title"] or "").strip()
            if not title:
                continue
            title_to_books.setdefault(title, []).append({"id": str(row["id"]), "title": title})

        # Trùng khớp tuyệt đối (cùng chuỗi tên) — không cần LLM
        groups: list[dict] = []
        seen_pairs: set[tuple[str, ...]] = set()
        for title, books in title_to_books.items():
            if len(books) >= 2:
                key = tuple(sorted(b["id"] for b in books))
                seen_pairs.add(key)
                groups.append({"canonical": title, "books": books})

        titles = list(title_to_books.keys())
        step = max(1, batch_size - 5)
        starts = [s for s in range(0, len(titles), step) if len(titles[s : s + batch_size]) >= 2]
        logger.info(
            "Kiểm tra trùng tên: %s sách, %s tên khác nhau, %s lô LLM (mỗi lô tối đa %s tên)",
            len(rows),
            len(titles),
            len(starts),
            batch_size,
        )
        if groups:
            logger.info("Đã thấy %s nhóm trùng khớp tuyệt đối (cùng chuỗi tên)", len(groups))

        for index, start in enumerate(starts, start=1):
            chunk = titles[start : start + batch_size]
            logger.info(
                "LLM lô %s/%s — tên %s–%s (%s tên)",
                index,
                len(starts),
                start + 1,
                start + len(chunk),
                len(chunk),
            )
            try:
                llm_groups = find_duplicate_title_groups(chunk)
            except Exception as exc:
                logger.warning("LLM kiểm tra trùng lô %s–%s lỗi: %s", start + 1, start + len(chunk), exc)
                continue
            logger.info("LLM lô %s/%s xong — %s nhóm", index, len(starts), len(llm_groups))
            for group in llm_groups:
                books: list[dict[str, str]] = []
                for t in group.get("titles") or []:
                    books.extend(title_to_books.get(t) or [])
                ids = tuple(sorted({b["id"] for b in books}))
                if len(ids) < 2 or ids in seen_pairs:
                    continue
                seen_pairs.add(ids)
                uniq_books = []
                seen_id = set()
                for b in books:
                    if b["id"] in seen_id:
                        continue
                    seen_id.add(b["id"])
                    uniq_books.append(b)
                groups.append({"canonical": group.get("canonical") or uniq_books[0]["title"], "books": uniq_books})

        BookDuplicateReport.objects.filter(pk=report.pk).update(
            status=DuplicateReportStatus.DONE,
            groups=groups,
            error_message="" if groups else "Không thấy nhóm tên trùng.",
            updated_at=timezone.now(),
        )
        logger.info(
            "Xong kiểm tra trùng tên — %s nhóm, %s sách",
            len(groups),
            len(rows),
        )
        return {"report_id": report_id, "groups": len(groups), "books": len(rows)}
    except Exception as e:
        BookDuplicateReport.objects.filter(pk=report.pk).update(
            status=DuplicateReportStatus.FAILED,
            error_message=f"{type(e).__name__}: {e}",
            updated_at=timezone.now(),
        )
        raise
    finally:
        release_dedupe_lock(task_id)
