"""Tạo / cập nhật văn bản nguồn Dataset từ sách đã duyệt hoặc file tải lên."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from django.core.files.base import ContentFile

from apps.dataset.catalog import (
    CLASS_FIELD_NAMES,
    PUBLICATION_FIELD_NAMES,
    assign_catalog_code,
    copy_book_classification,
)
from apps.dataset.markdown import rebuild_segments, split_source_markdown
from apps.dataset.models import ChunkingBatchStatus, SourceDocument, SourceType

logger = logging.getLogger(__name__)

_ALLOWED_EXT = {".md", ".txt", ".markdown"}


def decode_text_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1258", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def title_from_filename(name: str) -> str:
    stem = Path(name or "van-ban").stem.strip()
    return stem or "Văn bản"


def normalize_uploaded_markdown(*, filename: str, text: str) -> str:
    body = (text or "").replace("\r\n", "\n").strip()
    if not body:
        return ""
    ext = Path(filename or "").suffix.lower()
    title = title_from_filename(filename)
    if ext in {".txt"}:
        if not body.startswith("#"):
            return f"# {title}\n\n{body}\n"
        return body + ("\n" if not body.endswith("\n") else "")
    if not re.match(r"(?m)^#\s+", body):
        return f"# {title}\n\n{body}\n"
    return body + ("\n" if not body.endswith("\n") else "")


def _document_has_chunking(document: SourceDocument) -> bool:
    return document.chunking_batches.filter(
        status__in=[
            ChunkingBatchStatus.PENDING,
            ChunkingBatchStatus.PROCESSING,
            ChunkingBatchStatus.DONE,
        ]
    ).exists()


def save_markdown_file(document: SourceDocument, content: str, *, filename: str) -> None:
    stem = Path(filename or document.title or "van-ban").stem
    safe = re.sub(r"[^\w\-.]+", "_", stem, flags=re.UNICODE).strip("._") or "van-ban"
    document.markdown_file.save(f"{safe}.md", ContentFile((content or "").encode("utf-8")), save=False)


def book_source_markdown(book) -> str:
    from apps.documents.markdown import aggregate_markdown, book_has_markdown, book_markdown_content

    if book.pages.exists():
        return aggregate_markdown(book, use_cleaned=True, use_chunked=False)
    if book_has_markdown(book):
        return book_markdown_content(book)
    return ""


def sync_source_from_book(book, *, created_by=None) -> SourceDocument | None:
    """Snapshot MD sách đã duyệt vào Dataset. Không ghi đè MD nếu đã phân đoạn."""
    from apps.documents.models import BookStatus

    if getattr(book, "status", None) != BookStatus.APPROVED:
        return None

    document = SourceDocument.objects.filter(book=book).first()
    skip_content = bool(document and _document_has_chunking(document))
    content = "" if skip_content else book_source_markdown(book)
    if not skip_content and not (content or "").strip():
        if document is None:
            logger.warning("Sách «%s» đã duyệt nhưng chưa có Markdown — bỏ qua Dataset.", book.title)
            return None
        skip_content = True

    created = False
    if document is None:
        document = SourceDocument(
            title=book.title,
            source_type=SourceType.BOOK,
            book=book,
            original_name=f"{book.slug or book.title}.md",
            created_by=created_by,
        )
        created = True
    else:
        document.title = book.title
        document.source_type = SourceType.BOOK
        document.original_name = document.original_name or f"{book.slug or book.title}.md"

    copy_book_classification(document, book)
    if not skip_content:
        save_markdown_file(document, content, filename=document.original_name)

    update_fields = [
        "title",
        "source_type",
        "original_name",
        *CLASS_FIELD_NAMES,
        *PUBLICATION_FIELD_NAMES,
        "updated_at",
    ]
    if created:
        document.save()
    else:
        if not skip_content:
            update_fields.append("markdown_file")
        document.save(update_fields=update_fields)
    assign_catalog_code(document)
    if not skip_content:
        rebuild_segments(document, content)
    return document


def sync_approved_books(*, created_by=None) -> int:
    from apps.documents.models import Book, BookStatus

    created = 0
    for book in Book.objects.filter(status=BookStatus.APPROVED).iterator():
        if SourceDocument.objects.filter(book=book).exists():
            continue
        if sync_source_from_book(book, created_by=created_by):
            created += 1
    return created


def create_source_from_upload(*, filename: str, data: bytes, created_by=None) -> SourceDocument:
    ext = Path(filename or "").suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise ValueError("Chỉ nhận file .md hoặc .txt.")
    raw = decode_text_bytes(data)
    content = normalize_uploaded_markdown(filename=filename, text=raw)
    if not content.strip():
        raise ValueError("File trống.")
    if not split_source_markdown(content):
        raise ValueError("Không đọc được nội dung văn bản.")

    title = title_from_filename(filename)
    document = SourceDocument(
        title=title,
        source_type=SourceType.UPLOAD,
        original_name=filename or f"{title}.md",
        created_by=created_by,
    )
    save_markdown_file(document, content, filename=filename)
    document.save()
    rebuild_segments(document, content)
    return document
