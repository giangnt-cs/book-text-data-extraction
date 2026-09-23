"""Mã văn bản Dataset: DSG_fff — độc lập với sách."""

from __future__ import annotations

import hashlib
import re

from django.db import connection, transaction
from django.utils import timezone

from apps.documents.classification import BOOK_CLASS_FIELDS

CLASS_FIELD_NAMES = tuple(name for name, _slug in BOOK_CLASS_FIELDS)
PUBLICATION_FIELD_NAMES = ("author", "publisher", "published_year")
MAIN_CLASS_FIELDS = ("class_source", "class_field", "class_language")
_CODE_RE = re.compile(r"^(.+)_(\d+)$")


def copy_book_classification(document, book) -> None:
    for field_name in CLASS_FIELD_NAMES:
        setattr(document, field_name, getattr(book, field_name, None))
    for field_name in PUBLICATION_FIELD_NAMES:
        setattr(document, field_name, (getattr(book, field_name, None) or "").strip())


def dsg_prefix(document) -> str:
    parts: list[str] = []
    for field_name in MAIN_CLASS_FIELDS:
        term = getattr(document, field_name, None)
        code = (getattr(term, "code", None) or "").strip()
        if not code:
            return ""
        parts.append(code)
    return "".join(parts)


def _advisory_lock_key(prefix: str) -> int:
    digest = hashlib.sha256(f"dataset.catalog:{prefix}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (2**63)


def assign_catalog_code(document) -> str:
    """Gán DSG_fff nếu đủ 3 nhóm chính. Giữ mã khi prefix không đổi."""
    from apps.dataset.models import SourceDocument

    prefix = dsg_prefix(document)
    if not prefix:
        return (document.catalog_code or "").strip()
    current = (document.catalog_code or "").strip()
    match = _CODE_RE.match(current)
    if match and match.group(1) == prefix:
        sync_image_catalog_from_text(document)
        return current

    from apps.dataset.models import ImageSourceDocument

    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_advisory_lock_key(prefix)])
        max_n = 0
        text_qs = SourceDocument.objects.select_for_update().filter(catalog_code__startswith=f"{prefix}_")
        if document.pk:
            text_qs = text_qs.exclude(pk=document.pk)
        image_qs = ImageSourceDocument.objects.select_for_update().filter(catalog_code__startswith=f"{prefix}_")
        for code in list(text_qs.values_list("catalog_code", flat=True)) + list(
            image_qs.values_list("catalog_code", flat=True)
        ):
            parsed = _CODE_RE.match(code or "")
            if parsed and parsed.group(1) == prefix:
                max_n = max(max_n, int(parsed.group(2)))
        document.catalog_code = f"{prefix}_{max_n + 1:03d}"
        if document.pk:
            SourceDocument.objects.filter(pk=document.pk).update(
                catalog_code=document.catalog_code,
                updated_at=timezone.now(),
            )
    refresh_chunk_codes(document)
    sync_image_catalog_from_text(document)
    return document.catalog_code


def classification_update_fields() -> list[str]:
    return [*CLASS_FIELD_NAMES, "catalog_code", "updated_at"]


def format_chunk_code(catalog_code: str, page: int, seq: int) -> str:
    catalog = (catalog_code or "").strip()
    if not catalog:
        return ""
    return f"{catalog}_{int(page):03d}_{int(seq):03d}"


def format_image_chunk_code(catalog_code: str, page: int, seq: int) -> str:
    base = format_chunk_code(catalog_code, page, seq)
    return f"{base}_img" if base else ""


def assign_image_catalog_code(document) -> str:
    """Gán mã văn bản hình: copy từ văn bản chữ cùng sách, không thì tự tăng trên bảng hình."""
    from apps.dataset.models import ImageSourceDocument

    book = getattr(document, "book", None)
    text_doc = getattr(book, "dataset_source", None) if book is not None else None
    text_code = (getattr(text_doc, "catalog_code", None) or "").strip() if text_doc else ""
    if text_code:
        if (document.catalog_code or "").strip() != text_code:
            document.catalog_code = text_code
            if document.pk:
                ImageSourceDocument.objects.filter(pk=document.pk).update(
                    catalog_code=text_code,
                    updated_at=timezone.now(),
                )
            refresh_image_chunk_codes(document)
        return text_code

    prefix = dsg_prefix(document)
    if not prefix:
        return (document.catalog_code or "").strip()
    current = (document.catalog_code or "").strip()
    match = _CODE_RE.match(current)
    if match and match.group(1) == prefix:
        return current

    from apps.dataset.models import SourceDocument as TextSourceDocument

    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_advisory_lock_key(prefix)])
        max_n = 0
        for model, pk in (
            (TextSourceDocument, None),
            (ImageSourceDocument, document.pk),
        ):
            qs = model.objects.select_for_update().filter(catalog_code__startswith=f"{prefix}_")
            if pk:
                qs = qs.exclude(pk=pk)
            for code in qs.values_list("catalog_code", flat=True):
                parsed = _CODE_RE.match(code or "")
                if parsed and parsed.group(1) == prefix:
                    max_n = max(max_n, int(parsed.group(2)))
        document.catalog_code = f"{prefix}_{max_n + 1:03d}"
        if document.pk:
            ImageSourceDocument.objects.filter(pk=document.pk).update(
                catalog_code=document.catalog_code,
                updated_at=timezone.now(),
            )
    refresh_image_chunk_codes(document)
    return document.catalog_code


def sync_image_catalog_from_text(document) -> None:
    """Khi mã văn bản chữ đổi, copy sang văn bản hình cùng sách."""
    from apps.dataset.models import ImageSourceDocument

    book = getattr(document, "book", None)
    if book is None:
        return
    image_doc = getattr(book, "dataset_image_source", None)
    if image_doc is None:
        return
    catalog = (document.catalog_code or "").strip()
    if (image_doc.catalog_code or "").strip() == catalog:
        return
    ImageSourceDocument.objects.filter(pk=image_doc.pk).update(
        catalog_code=catalog,
        updated_at=timezone.now(),
    )
    image_doc.catalog_code = catalog
    refresh_image_chunk_codes(image_doc)


def image_chunk_png_name(document_id, code: str) -> str:
    return f"dataset/images/{document_id}/{code}.png"


def rename_image_chunk_file(chunk, new_code: str) -> None:
    """Đổi tên file PNG cho khớp mã phân đoạn. Tìm file ở đường dẫn cũ hoặc mã mới nếu DB lệch disk."""
    from django.core.files.base import ContentFile

    new_code = (new_code or "").strip()
    if not new_code or not chunk.image:
        return
    old_name = (chunk.image.name or "").strip()
    old_code = (chunk.chunk_code or "").strip()
    new_name = image_chunk_png_name(chunk.document_id, new_code)
    chunk.chunk_code = new_code
    if old_name == new_name:
        return
    storage = chunk.image.storage
    candidates = []
    for name in (
        old_name,
        image_chunk_png_name(chunk.document_id, old_code) if old_code else "",
        new_name,
    ):
        name = (name or "").strip()
        if name and name not in candidates:
            candidates.append(name)
    data = b""
    src = ""
    for name in candidates:
        try:
            if not storage.exists(name):
                continue
            with storage.open(name, "rb") as fh:
                data = fh.read() or b""
        except Exception:
            continue
        if data:
            src = name
            break
    if not data:
        return
    if src == new_name:
        chunk.image.name = new_name
        return
    chunk.image.save(f"{new_code}.png", ContentFile(data), save=False)
    if src and src != chunk.image.name and storage.exists(src):
        try:
            storage.delete(src)
        except Exception:
            pass


def refresh_chunk_codes(document) -> int:
    """Cập nhật tiền tố DSG_fff trên mã đoạn khi mã văn bản đổi."""
    from apps.dataset.models import SourceChunk

    catalog = (document.catalog_code or "").strip()
    rows = list(document.chunks.all())
    for chunk in rows:
        chunk.chunk_code = format_chunk_code(catalog, chunk.page_number or 1, chunk.page_seq or 1)
    if rows:
        SourceChunk.objects.bulk_update(rows, ["chunk_code"])
    return len(rows)


def refresh_image_chunk_codes(document) -> int:
    """Cập nhật mã đoạn hình và đổi tên file PNG khi mã văn bản đổi."""
    from apps.dataset.models import ImageChunk

    catalog = (document.catalog_code or "").strip()
    rows = list(document.chunks.all())
    for index, chunk in enumerate(rows):
        new_code = format_image_chunk_code(catalog, chunk.page_number or 1, chunk.page_seq or 1)
        if not new_code:
            new_code = f"{chunk.box_id}_img"
        if (chunk.chunk_code or "").strip() != new_code:
            rename_image_chunk_file(chunk, new_code)
        chunk.chunk_code = new_code
    if rows:
        ImageChunk.objects.bulk_update(rows, ["chunk_code", "image"])
    return len(rows)
