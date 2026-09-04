"""Nhập sách từ danh sách tạm: OCR trang đầu, đối chiếu, tạo Book hoặc xóa file."""

from __future__ import annotations

import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any

from django.core.files.base import ContentFile

from apps.documents.models import Book, PageOcrMode, StagedBook, StagedBookStatus
from apps.llm.book_ingest_runner import confirm_title_duplicate, extract_title_from_page
from apps.llm.ocr_runner import get_fallback_provider, load_global_page_ocr_providers, ocr_full_page
from bb_detection.pdf_validate import validate_pdf

logger = logging.getLogger(__name__)


def normalize_title(value: str) -> str:
    text = (value or "").casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def existing_book_titles(*, exclude_pk=None) -> list[str]:
    qs = Book.objects.order_by("title").values_list("title", flat=True)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return [t for t in qs if (t or "").strip()]


def find_book_by_title(title: str) -> Book | None:
    needle = normalize_title(title)
    if not needle:
        return None
    for book in Book.objects.only("id", "title").iterator():
        if normalize_title(book.title) == needle:
            return book
    return Book.objects.filter(title__iexact=title.strip()).first()


def similar_titles(title: str, existing: list[str], *, limit: int = 12, min_ratio: float = 0.55) -> list[tuple[float, str]]:
    needle = normalize_title(title)
    if not needle:
        return []
    scored: list[tuple[float, str]] = []
    for candidate in existing:
        other = normalize_title(candidate)
        if not other:
            continue
        ratio = SequenceMatcher(None, needle, other).ratio()
        if ratio >= min_ratio:
            scored.append((ratio, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:limit]


def resolve_duplicate_book(title: str, existing: list[str]) -> Book | None:
    matched = find_book_by_title(title)
    if matched:
        return matched
    candidates = similar_titles(title, existing)
    if not candidates:
        return None
    top_ratio, top_title = candidates[0]
    if top_ratio >= 0.92:
        return find_book_by_title(top_title)
    try:
        confirmed = confirm_title_duplicate(
            title=title,
            candidates=[name for _ratio, name in candidates],
        )
    except Exception as exc:
        logger.warning("LLM đối chiếu tên «%s» lỗi: %s", title, exc)
        return None
    if confirmed:
        return find_book_by_title(confirmed)
    return None


def ocr_pdf_first_page(pdf_bytes: bytes) -> str:
    ok, err = validate_pdf(pdf_bytes=pdf_bytes)
    if not ok:
        raise RuntimeError(err or "PDF không hợp lệ.")

    from apps.layout.models import DetectorConfig
    from bb_detection.pdf_render import render_page_png

    det = (
        DetectorConfig.objects.filter(is_active=True, is_default=True).first()
        or DetectorConfig.objects.filter(is_active=True).order_by("id").first()
    )
    dpi = int(getattr(det, "dpi", None) or 150)
    use_cropbox = bool(getattr(det, "use_cropbox", True))
    trim_scan = bool(getattr(det, "trim_scan", False))

    png, width, height = render_page_png(
        pdf_bytes=pdf_bytes,
        page_number=1,
        dpi=dpi,
        use_cropbox=use_cropbox,
        trim_scan=trim_scan,
    )
    mapping = load_global_page_ocr_providers()
    provider = mapping.get(PageOcrMode.STANDARD) or get_fallback_provider()
    result = ocr_full_page(
        provider=provider,
        page_image_bytes=png,
        ocr_mode=PageOcrMode.STANDARD,
        pdf_bytes=pdf_bytes,
        page_index=0,
        page_width=float(width or 0),
        page_height=float(height or 0),
    )
    text = (result.text or "").strip()
    if not text:
        raise RuntimeError("OCR trang đầu không đọc được chữ.")
    return text


def _create_book_from_staged(staged: StagedBook, title: str) -> Book:
    if not staged.pdf_file:
        raise RuntimeError("File PDF tạm đã bị xóa.")
    filename = os.path.basename(staged.pdf_file.name) or staged.original_name or "book.pdf"
    with staged.pdf_file.open("rb") as f:
        data = f.read()
    book = Book(title=title[:255], created_by=staged.created_by)
    book.pdf_file.save(filename, ContentFile(data), save=False)
    book.save()
    return book


def process_staged_book(staged: StagedBook) -> dict[str, Any]:
    StagedBook.objects.filter(pk=staged.pk).update(
        status=StagedBookStatus.PROCESSING,
        error_message="",
    )
    staged.refresh_from_db()

    try:
        if not staged.pdf_file:
            raise RuntimeError("Không có file PDF.")
        with staged.pdf_file.open("rb") as f:
            pdf_bytes = f.read()

        page_text = ocr_pdf_first_page(pdf_bytes)
        extracted = extract_title_from_page(
            page_text=page_text,
            original_name=staged.original_name,
        )
        title = (extracted.get("title") or "").strip()
        if not title:
            raise RuntimeError("LLM không rút được tên sách từ trang đầu.")

        matched = resolve_duplicate_book(title, existing_book_titles())

        staged.first_page_text = page_text
        staged.extracted_title = title[:255]

        if matched:
            staged.delete_pdf()
            staged.status = StagedBookStatus.DUPLICATE
            staged.matched_book = matched
            staged.created_book = None
            staged.error_message = f"Trùng «{matched.title}» — đã xóa file tạm."
            staged.save()
            return {"status": "duplicate", "matched_id": str(matched.id), "title": title}

        book = _create_book_from_staged(staged, title)
        staged.delete_pdf()
        staged.status = StagedBookStatus.CREATED
        staged.created_book = book
        staged.matched_book = None
        staged.error_message = ""
        staged.save()
        return {"status": "created", "book_id": str(book.id), "title": title}

    except Exception as exc:
        logger.exception("Nhập sách tạm «%s» thất bại", staged.original_name)
        staged.status = StagedBookStatus.FAILED
        staged.error_message = f"{type(exc).__name__}: {exc}"
        staged.save(update_fields=["status", "error_message", "updated_at"])
        return {"status": "failed", "error": str(exc)}
