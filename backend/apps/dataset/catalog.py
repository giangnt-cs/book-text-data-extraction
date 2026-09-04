"""Mã văn bản Dataset: DSG_fff — độc lập với sách."""

from __future__ import annotations

import hashlib
import re

from django.db import connection, transaction
from django.utils import timezone

from apps.documents.classification import BOOK_CLASS_FIELDS

CLASS_FIELD_NAMES = tuple(name for name, _slug in BOOK_CLASS_FIELDS)
MAIN_CLASS_FIELDS = ("class_source", "class_field", "class_language")
_CODE_RE = re.compile(r"^(.+)_(\d+)$")


def copy_book_classification(document, book) -> None:
    for field_name in CLASS_FIELD_NAMES:
        setattr(document, field_name, getattr(book, field_name, None))


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
        return current

    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_advisory_lock_key(prefix)])
        qs = SourceDocument.objects.select_for_update().filter(catalog_code__startswith=f"{prefix}_")
        if document.pk:
            qs = qs.exclude(pk=document.pk)
        max_n = 0
        for code in qs.values_list("catalog_code", flat=True):
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
    return document.catalog_code


def classification_update_fields() -> list[str]:
    return [*CLASS_FIELD_NAMES, "catalog_code", "updated_at"]


def format_chunk_code(catalog_code: str, page: int, seq: int) -> str:
    catalog = (catalog_code or "").strip()
    if not catalog:
        return ""
    return f"{catalog}_{int(page):03d}_{int(seq):03d}"


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
