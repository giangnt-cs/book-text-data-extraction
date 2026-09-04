"""Ghép / tách Markdown văn bản nguồn Dataset."""

from __future__ import annotations

import re

from django.core.files.storage import default_storage
from django.db import connection, transaction

from apps.dataset.models import (
    ChunkingBatchStatus,
    SourceChunkedText,
    SourceChunkingBatch,
    SourceDocument,
    SourceSegment,
)

_PAGE_HEADING_RE = re.compile(r"(?m)^## Trang (\d+)\s*$")
_HTML_COMMENT_RE = re.compile(r"(?m)^<!--.*?-->\s*")
_BREAK_RE = re.compile(r"</\s*break\s*>", re.IGNORECASE)
_DUP_PAGE_COMMENT_RE = re.compile(
    r"(<!--\s*Trang\s+(\d+)\s*-->)(?:\s*<!--\s*Trang\s+\2\s*-->)+",
    re.IGNORECASE,
)
_PAGE_COMMENT_FIND_RE = re.compile(r"<!--\s*Trang\s+(\d+)\s*-->", re.IGNORECASE)
_LEADING_PAGE_COMMENT_RE = re.compile(
    r"^(?:\s*<!--\s*Trang\s+\d+\s*-->\s*)+",
    re.IGNORECASE,
)


def split_source_markdown(content: str) -> list[tuple[int, str]]:
    """Tách theo `## Trang N`. Không có heading thì cả file là một trang."""
    text = (content or "").replace("\r\n", "\n")
    matches = list(_PAGE_HEADING_RE.finditer(text))
    if not matches:
        body = text.strip()
        return [(1, body)] if body else []
    parts: list[tuple[int, str]] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        number = int(match.group(1))
        body = _HTML_COMMENT_RE.sub("", text[start:end]).strip()
        parts.append((number, body))
    return parts


def rebuild_segments(document: SourceDocument, content: str) -> int:
    parts = split_source_markdown(content)
    document.segments.all().delete()
    SourceSegment.objects.bulk_create(
        [
            SourceSegment(document=document, page_number=number, text_md=body)
            for number, body in parts
        ]
    )
    return len(parts)


def read_source_file(document: SourceDocument) -> str:
    if document.markdown_file and document.markdown_file.name and default_storage.exists(document.markdown_file.name):
        with document.markdown_file.open("rb") as fh:
            return fh.read().decode("utf-8")
    return assemble_source_markdown(document, use_chunked=False)


def get_active_chunking_batch(document: SourceDocument) -> SourceChunkingBatch | None:
    return (
        document.chunking_batches.filter(status=ChunkingBatchStatus.DONE)
        .order_by("-created_at")
        .first()
    )


def document_has_chunked_markdown(document: SourceDocument) -> bool:
    return document.chunking_batches.filter(status=ChunkingBatchStatus.DONE).exists()


def markdown_display_source(document: SourceDocument) -> str:
    return "chunked" if document_has_chunked_markdown(document) else "source"


def normalize_chunked_export(text: str) -> str:
    """MD liền mạch: không ## Trang N; </break> rồi <!-- Trang P -->; gộp comment trùng."""
    body = (text or "").replace("\r\n", "\n")
    body = _PAGE_HEADING_RE.sub("", body)
    body = _BREAK_RE.sub("</break>", body)
    body = re.sub(r"</break>[ \t]*\n?", "</break>\n", body)
    while True:
        nxt = _DUP_PAGE_COMMENT_RE.sub(r"\1\n", body)
        if nxt == body:
            break
        body = nxt
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip() + "\n"


def assemble_source_markdown(document: SourceDocument, *, use_chunked: bool = True) -> str:
    batch = get_active_chunking_batch(document) if use_chunked else None
    chunked_map: dict = {}
    if batch:
        chunked_map = {
            row.segment_id: (row.text_md or "").strip()
            for row in SourceChunkedText.objects.filter(batch=batch)
        }
    segments = list(document.segments.order_by("page_number"))
    if not segments:
        body = ""
        if document.markdown_file and document.markdown_file.name and default_storage.exists(document.markdown_file.name):
            with document.markdown_file.open("rb") as fh:
                body = fh.read().decode("utf-8")
        return body
    if batch:
        parts: list[str] = []
        for segment in segments:
            text = (chunked_map.get(segment.pk, "") or "").strip()
            if text:
                parts.append(text)
        return normalize_chunked_export(f"# {document.title}\n\n" + "\n\n".join(parts) + "\n")

    lines = [f"# {document.title}", ""]
    for segment in segments:
        lines.append(f"## Trang {segment.page_number}")
        lines.append("")
        text = (segment.text_md or "").strip()
        if text:
            lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def ensure_leading_page_comment(text: str, page: int) -> str:
    """Đặt `<!-- Trang N -->` ở đầu đoạn để trích dẫn RAG."""
    body = (text or "").replace("\r\n", "\n").strip()
    body = _LEADING_PAGE_COMMENT_RE.sub("", body).strip()
    marker = f"<!-- Trang {int(page)} -->"
    if not body:
        return marker
    return f"{marker}\n{body}"


def first_page_comment(text: str) -> int | None:
    match = _PAGE_COMMENT_FIND_RE.search(text or "")
    if not match:
        return None
    return int(match.group(1))


def annotate_chunk_pages(parts: list[str]) -> list[tuple[str, int, int]]:
    """Gán trang (ccc) và thứ tự trong trang (ppp) theo <!-- Trang --> và thứ tự đoạn."""
    current_page = 1
    seq_by_page: dict[int, int] = {}
    rows: list[tuple[str, int, int]] = []
    for text in parts:
        found = first_page_comment(text)
        if found is not None:
            current_page = found
        seq_by_page[current_page] = seq_by_page.get(current_page, 0) + 1
        rows.append((text, current_page, seq_by_page[current_page]))
    return rows


def _lock_document_chunks(document):
    from apps.dataset.models import SourceChunk

    return list(
        SourceChunk.objects.select_for_update().filter(document=document).order_by("position")
    )


def _apply_position_shift(document, *, gte: int, delta: int) -> None:
    from apps.dataset.models import SourceChunk

    rows = list(document.chunks.filter(position__gte=gte).order_by("position"))
    if not rows:
        return
    originals = [chunk.position for chunk in rows]
    for index, chunk in enumerate(rows):
        chunk.position = 2_000_000 + index
    SourceChunk.objects.bulk_update(rows, ["position"])
    for chunk, pos in zip(rows, originals):
        chunk.position = pos + delta
    SourceChunk.objects.bulk_update(rows, ["position"])


def _apply_page_seq_shift(document, page: int, *, gte_seq: int, delta: int) -> None:
    from apps.dataset.catalog import format_chunk_code
    from apps.dataset.models import SourceChunk

    rows = list(
        document.chunks.filter(page_number=page, page_seq__gte=gte_seq).order_by("page_seq")
    )
    if not rows:
        return
    catalog = (document.catalog_code or "").strip()
    for index, chunk in enumerate(rows):
        chunk.chunk_code = f"__tmp_{chunk.pk.hex}_{index}"
    SourceChunk.objects.bulk_update(rows, ["chunk_code"])
    for chunk in rows:
        chunk.page_seq = int(chunk.page_seq) + delta
        chunk.chunk_code = format_chunk_code(catalog, page, chunk.page_seq)
    SourceChunk.objects.bulk_update(rows, ["page_seq", "chunk_code"])


def insert_source_chunk(*, document, text: str, page: int, after_chunk=None):
    """Chèn đoạn mới, dồn position và mã ppp các đoạn sau trên cùng trang."""
    from apps.dataset.catalog import format_chunk_code
    from apps.dataset.models import SourceChunk

    page = int(page)
    text = ensure_leading_page_comment(text, page)
    with transaction.atomic():
        _lock_document_chunks(document)
        if after_chunk is not None:
            after_chunk = SourceChunk.objects.select_for_update().get(pk=after_chunk.pk)
            new_seq = int(after_chunk.page_seq) + 1
            insert_pos = int(after_chunk.position) + 1
        else:
            new_seq = 1
            first = document.chunks.filter(page_number=page).order_by("position").first()
            if first:
                insert_pos = int(first.position)
            else:
                prev = document.chunks.filter(page_number__lt=page).order_by("-position").first()
                insert_pos = int(prev.position) + 1 if prev else 1
        _apply_position_shift(document, gte=insert_pos, delta=1)
        _apply_page_seq_shift(document, page, gte_seq=new_seq, delta=1)
        chunk = SourceChunk(
            document=document,
            position=insert_pos,
            page_number=page,
            page_seq=new_seq,
            chunk_code=format_chunk_code(document.catalog_code, page, new_seq),
            text_md=text or "",
        )
        chunk.save()
        return chunk


def delete_source_chunk(chunk) -> None:
    """Xóa đoạn và dồn mã ppp các đoạn sau trên cùng trang."""
    from apps.dataset.models import SourceChunk

    document = chunk.document
    page = int(chunk.page_number or 1)
    seq = int(chunk.page_seq or 1)
    pos = int(chunk.position)
    with transaction.atomic():
        _lock_document_chunks(document)
        SourceChunk.objects.filter(pk=chunk.pk).delete()
        _apply_position_shift(document, gte=pos + 1, delta=-1)
        _apply_page_seq_shift(document, page, gte_seq=seq + 1, delta=-1)


def strip_break_markers(text: str) -> str:
    return _BREAK_RE.sub("", text or "").strip()


def split_break_chunks(content: str) -> list[str]:
    """Tách MD theo </break> và bỏ mọi dấu vết của chuỗi đó."""
    body = (content or "").replace("\r\n", "\n")
    body = re.sub(r"^#\s+[^\n]+\n+", "", body.strip(), count=1)
    body = _BREAK_RE.sub("</break>", body)
    parts: list[str] = []
    for raw in body.split("</break>"):
        piece = strip_break_markers(raw)
        if piece:
            parts.append(piece)
    return parts


def rebuild_document_chunks(document: SourceDocument) -> int:
    """Ghi lại các khối phân đoạn từ MD đã gắn </break>."""
    from apps.dataset.catalog import format_chunk_code
    from apps.dataset.models import SourceChunk

    if not document_has_chunked_markdown(document):
        document.chunks.all().delete()
        return 0
    content = assemble_source_markdown(document, use_chunked=True)
    parts = split_break_chunks(content)
    catalog = (document.catalog_code or "").strip()
    with transaction.atomic():
        document.chunks.all().delete()
        SourceChunk.objects.bulk_create(
            [
                SourceChunk(
                    document=document,
                    position=index,
                    page_number=page,
                    page_seq=seq,
                    chunk_code=format_chunk_code(catalog, page, seq),
                    text_md=text,
                )
                for index, (text, page, seq) in enumerate(annotate_chunk_pages(parts), start=1)
            ]
        )
    return len(parts)


def renumber_document_chunks(document: SourceDocument) -> None:
    from apps.dataset.models import SourceChunk

    rows = list(document.chunks.order_by("position", "created_at"))
    if not rows:
        return
    for index, chunk in enumerate(rows, start=1):
        chunk.position = 1_000_000 + index
    SourceChunk.objects.bulk_update(rows, ["position"])
    for index, chunk in enumerate(rows, start=1):
        chunk.position = index
    SourceChunk.objects.bulk_update(rows, ["position"])


def source_markdown_content(document: SourceDocument) -> str:
    if document_has_chunked_markdown(document):
        return assemble_source_markdown(document, use_chunked=True)
    return read_source_file(document)


def purge_source_chunking(document: SourceDocument) -> int:
    """Xóa mọi lần phân đoạn bằng SQL — không load text_md vào RAM."""
    from apps.dataset.models import SourceChunk

    SourceChunk.objects.filter(document=document).delete()
    n = SourceChunkingBatch.objects.filter(document=document).count()
    if not n:
        return 0
    qn = connection.ops.quote_name
    parent_table = qn(SourceChunkingBatch._meta.db_table)
    parent_pk = qn(SourceChunkingBatch._meta.pk.column)
    doc_col = qn(SourceChunkingBatch._meta.get_field("document").column)
    child_table = qn(SourceChunkedText._meta.db_table)
    child_fk = qn(SourceChunkedText._meta.get_field("batch").column)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"LOCK TABLE {child_table}, {parent_table} IN ACCESS EXCLUSIVE MODE")
            cursor.execute(
                f"DELETE FROM {child_table} WHERE {child_fk} IN "
                f"(SELECT {parent_pk} FROM {parent_table} WHERE {doc_col} = %s)",
                [document.pk],
            )
            cursor.execute(
                f"DELETE FROM {parent_table} WHERE {doc_col} = %s",
                [document.pk],
            )
    return n
