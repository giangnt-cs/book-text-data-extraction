"""Ghép Markdown sách từ nội dung trang trong DB (không phụ thuộc worker/OCR)."""

from __future__ import annotations

import re

from django.core.files.storage import default_storage

from apps.documents.models import (
    Book,
    BookChunkingBatch,
    BookCleaningBatch,
    ChunkingBatchStatus,
    CleaningBatchStatus,
    Page,
    PageChunkedText,
    PageCleanedText,
)


def _strip_break_tokens(text: str) -> str:
    return re.sub(r"</\s*break\s*>", "", text or "", flags=re.IGNORECASE)


def get_active_cleaning_batch(book: Book) -> BookCleaningBatch | None:
    """Lần làm sạch hoàn thành mới nhất — dùng để hiển thị/tải markdown."""
    return (
        book.cleaning_batches.filter(status=CleaningBatchStatus.DONE)
        .order_by("-created_at")
        .first()
    )


def book_has_cleaned_markdown(book: Book) -> bool:
    return book.cleaning_batches.filter(status=CleaningBatchStatus.DONE).exists()


def get_active_chunking_batch(book: Book) -> BookChunkingBatch | None:
    """Lần phân đoạn hoàn thành mới nhất."""
    return (
        book.chunking_batches.filter(status=ChunkingBatchStatus.DONE)
        .order_by("-created_at")
        .first()
    )


def book_has_chunked_markdown(book: Book) -> bool:
    return book.chunking_batches.filter(status=ChunkingBatchStatus.DONE).exists()


def markdown_display_source(book: Book) -> str:
    if book_has_cleaned_markdown(book):
        return "cleaned"
    return "ocr"


def _chunked_text_map(batch: BookChunkingBatch | None) -> dict:
    if not batch:
        return {}
    return {
        row.page_id: (row.text_md or "").strip()
        for row in PageChunkedText.objects.filter(batch=batch).select_related("page")
    }


def _cleaned_text_map(batch: BookCleaningBatch | None) -> dict:
    if not batch:
        return {}
    return {
        row.page_id: (row.text_md or "").strip()
        for row in PageCleanedText.objects.filter(batch=batch).select_related("page")
    }


def get_page_cleaned_row(page: Page, *, batch: BookCleaningBatch | None = None) -> PageCleanedText | None:
    if batch is None:
        batch = get_active_cleaning_batch(page.book)
    if not batch:
        return None
    return PageCleanedText.objects.filter(batch=batch, page=page).first()


def page_has_cleaned_text(page: Page, *, batch: BookCleaningBatch | None = None) -> bool:
    row = get_page_cleaned_row(page, batch=batch)
    return bool(row and (row.text_md or "").strip())


def page_text_source(page: Page, *, batch: BookCleaningBatch | None = None) -> str:
    """'cleaned' nếu đang dùng bản làm sạch, không thì 'ocr'."""
    return "cleaned" if page_has_cleaned_text(page, batch=batch) else "ocr"


def page_display_text(page: Page, *, batch: BookCleaningBatch | None = None, cleaned_map: dict | None = None) -> str:
    if batch is None:
        batch = get_active_cleaning_batch(page.book)
    if batch:
        if cleaned_map is not None:
            text = cleaned_map.get(page.pk, "")
        else:
            row = PageCleanedText.objects.filter(batch=batch, page=page).first()
            text = (row.text_md if row else "") or ""
        if text.strip():
            return text.strip()
    return (page.text_md or "").strip()


def save_page_review_text(page: Page, text: str) -> str:
    """Lưu nội dung đang review: bản làm sạch nếu có, không thì OCR gốc.

    Trả về 'cleaned' hoặc 'ocr' tùy nơi đã ghi.
    """
    batch = get_active_cleaning_batch(page.book)
    if batch and page_has_cleaned_text(page, batch=batch):
        PageCleanedText.objects.filter(batch=batch, page=page).update(text_md=text)
        return "cleaned"
    page.text_md = text
    page.save(update_fields=["text_md"])
    return "ocr"


def discard_page_cleaned_text(page: Page) -> bool:
    """Bỏ bản làm sạch của trang trong lần làm sạch đang dùng. Trả về True nếu có bản bị xóa."""
    batch = get_active_cleaning_batch(page.book)
    if not batch:
        return False
    deleted, _ = PageCleanedText.objects.filter(batch=batch, page=page).delete()
    if deleted:
        page.book.refresh_cleaning_coverage()
    return bool(deleted)


def aggregate_markdown(book: Book, *, use_cleaned: bool = True, use_chunked: bool = False) -> str:
    chunk_batch = get_active_chunking_batch(book) if use_chunked else None
    chunked_map = _chunked_text_map(chunk_batch) if chunk_batch else {}
    batch = get_active_cleaning_batch(book) if use_cleaned else None
    cleaned_map = _cleaned_text_map(batch) if batch else {}

    lines = [f"# {book.title}", ""]
    for page in book.pages.order_by("page_number"):
        lines.append(f"## Trang {page.page_number}")
        notes: list[str] = []
        if page.ocr_tool_display:
            notes.append(page.ocr_tool_display)
        flags = []
        if page.has_table:
            flags.append("bảng")
        if page.has_formula:
            flags.append("công thức")
        if flags:
            notes.append(", ".join(flags))
        if notes:
            lines.append(f"<!-- OCR: {' · '.join(notes)} -->")
        lines.append("")
        chunked = chunked_map.get(page.pk, "") if chunk_batch else ""
        if chunk_batch and chunked.strip():
            text = chunked.strip()
        else:
            text = _strip_break_tokens(page_display_text(page, batch=batch, cleaned_map=cleaned_map))
        if text:
            lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def book_has_markdown(book: Book) -> bool:
    if book.pages.exists():
        return True
    return bool(
        book.markdown_file
        and book.markdown_file.name
        and default_storage.exists(book.markdown_file.name)
    )


def book_markdown_content(book: Book) -> str:
    """Nội dung markdown hiện tại — ưu tiên bản làm sạch, rồi OCR."""
    if book.pages.exists():
        return aggregate_markdown(book, use_cleaned=True, use_chunked=False)
    if book.markdown_file and book.markdown_file.name and default_storage.exists(book.markdown_file.name):
        with book.markdown_file.open("rb") as f:
            return f.read().decode("utf-8")
    return ""


def _sql_purge_batch_children(*, parent_model, child_model, book: Book) -> int:
    """Xóa bảng con rồi bảng cha bằng SQL — không load text_md, không dựa vào CASCADE DB."""
    from django.db import connection, transaction

    n = parent_model.objects.filter(book=book).count()
    if not n:
        return 0
    qn = connection.ops.quote_name
    parent_table = qn(parent_model._meta.db_table)
    parent_pk = qn(parent_model._meta.pk.column)
    book_col = qn(parent_model._meta.get_field("book").column)
    child_table = qn(child_model._meta.db_table)
    child_fk = qn(child_model._meta.get_field("batch").column)
    with transaction.atomic():
        with connection.cursor() as cursor:
            # Chặn worker đang INSERT trong lúc xóa (sách nghìn trang).
            cursor.execute(f"LOCK TABLE {child_table}, {parent_table} IN ACCESS EXCLUSIVE MODE")
            cursor.execute(
                f"DELETE FROM {child_table} WHERE {child_fk} IN "
                f"(SELECT {parent_pk} FROM {parent_table} WHERE {book_col} = %s)",
                [book.pk],
            )
            cursor.execute(
                f"DELETE FROM {parent_table} WHERE {book_col} = %s",
                [book.pk],
            )
    return n


def _sql_strip_break_tokens_from_book(book: Book) -> None:
    """Gỡ </break> còn sót trong OCR / bản làm sạch — không load text về Python."""
    from django.db import connection

    qn = connection.ops.quote_name
    page_table = qn(Page._meta.db_table)
    page_book = qn(Page._meta.get_field("book").column)
    cleaned_table = qn(PageCleanedText._meta.db_table)
    cleaned_fk = qn(PageCleanedText._meta.get_field("batch").column)
    clean_batch_table = qn(BookCleaningBatch._meta.db_table)
    clean_pk = qn(BookCleaningBatch._meta.pk.column)
    clean_book = qn(BookCleaningBatch._meta.get_field("book").column)
    pattern = r"</[[:space:]]*break[[:space:]]*>"
    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            cursor.execute(
                f"UPDATE {page_table} SET text_md = regexp_replace(text_md, %s, '', 'gi') "
                f"WHERE {page_book} = %s AND text_md ~* %s",
                [pattern, book.pk, pattern],
            )
            cursor.execute(
                f"UPDATE {cleaned_table} AS t SET text_md = regexp_replace(t.text_md, %s, '', 'gi') "
                f"FROM {clean_batch_table} AS b "
                f"WHERE t.{cleaned_fk} = b.{clean_pk} AND b.{clean_book} = %s AND t.text_md ~* %s",
                [pattern, book.pk, pattern],
            )
        else:
            cursor.execute(
                f"UPDATE {page_table} SET text_md = REPLACE(REPLACE(text_md, '</break>', ''), '</BREAK>', '') "
                f"WHERE {page_book} = %s",
                [book.pk],
            )


def purge_book_chunking(book: Book) -> int:
    """Xóa mọi lần phân đoạn của sách bằng SQL — không load text_md vào RAM."""
    deleted = _sql_purge_batch_children(
        parent_model=BookChunkingBatch,
        child_model=PageChunkedText,
        book=book,
    )
    _sql_strip_break_tokens_from_book(book)
    return deleted


def purge_book_cleaning(book: Book) -> int:
    """Xóa mọi lần làm sạch của sách bằng SQL — không load text_md vào RAM."""
    return _sql_purge_batch_children(
        parent_model=BookCleaningBatch,
        child_model=PageCleanedText,
        book=book,
    )
