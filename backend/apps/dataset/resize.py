"""Tách lại phân đoạn vượt hạn token."""

from __future__ import annotations

import logging
from typing import Any

from celery import current_app

from apps.dataset.markdown import replace_chunk_with_parts, split_break_chunks
from apps.dataset.models import SourceChunk, SourceDocument
from apps.dataset.ragflow import enqueue_ragflow_chunk_update
from apps.llm.chunking_runner import (
    chunk_page_text,
    get_chunk_resize_config,
    resolve_chunk_resize_provider,
)

logger = logging.getLogger(__name__)

RESIZE_MAX_ROUNDS = 3


def resize_one_chunk(chunk: SourceChunk) -> dict[str, Any]:
    """Gọi LLM một lần cho một đoạn. Trả về số đoạn sau tách."""
    config = get_chunk_resize_config()
    limit = int(config.max_tokens or 5000)
    if int(chunk.token_count or 0) <= limit:
        return {"chunk_id": str(chunk.pk), "created": 1, "skipped": True}
    provider = resolve_chunk_resize_provider(config)
    try:
        chunked = chunk_page_text(
            provider=provider,
            prompt_template=config.prompt,
            book_title=chunk.document.title,
            page_number=int(chunk.page_number or 1),
            page_text=chunk.text_md or "",
            model=config.model or "",
        )
    except Exception as exc:
        logger.warning(
            "Giảm kích thước đoạn %s «%s» lỗi: %s",
            chunk.position,
            chunk.document.title,
            exc,
        )
        return {"chunk_id": str(chunk.pk), "created": 1, "error": str(exc)}
    parts = split_break_chunks(chunked)
    if len(parts) <= 1:
        return {"chunk_id": str(chunk.pk), "created": 1, "unchanged": True}
    created = replace_chunk_with_parts(chunk, parts)
    enqueue_ragflow_chunk_update(created[0])
    return {"chunk_id": str(chunk.pk), "created": len(created)}


def resize_oversized_chunks(
    *,
    document: SourceDocument | None = None,
    chunk_ids: list[str] | None = None,
    max_rounds: int = RESIZE_MAX_ROUNDS,
) -> dict[str, Any]:
    config = get_chunk_resize_config()
    limit = int(config.max_tokens or 5000)
    qs = SourceChunk.objects.select_related("document").filter(token_count__gt=limit)
    if document is not None:
        qs = qs.filter(document=document)
    if chunk_ids is not None:
        qs = qs.filter(pk__in=chunk_ids)
    pending = list(qs.order_by("document_id", "position").values_list("pk", flat=True))
    split = 0
    skipped = 0
    failed = 0
    for _round in range(max(1, int(max_rounds))):
        if not pending:
            break
        next_pending: list = []
        for chunk_id in pending:
            chunk = (
                SourceChunk.objects.select_related("document")
                .filter(pk=chunk_id)
                .first()
            )
            if chunk is None or int(chunk.token_count or 0) <= limit:
                skipped += 1
                continue
            result = resize_one_chunk(chunk)
            if result.get("error"):
                failed += 1
                continue
            created = int(result.get("created") or 1)
            if created > 1:
                split += created - 1
                still = list(
                    SourceChunk.objects.filter(
                        document=chunk.document,
                        position__gte=chunk.position,
                        position__lt=chunk.position + created,
                        token_count__gt=limit,
                    ).values_list("pk", flat=True)
                )
                next_pending.extend(still)
            else:
                skipped += 1
        pending = next_pending
    return {
        "limit": limit,
        "split": split,
        "skipped": skipped,
        "failed": failed,
    }


def enqueue_resize_document(document: SourceDocument) -> tuple[bool, str]:
    limit = int(get_chunk_resize_config().max_tokens or 5000)
    n = document.chunks.filter(token_count__gt=limit).count()
    if not n:
        return False, f"Không có phân đoạn vượt {limit} token."
    try:
        current_app.send_task(
            "apps.dataset.tasks.resize_document_chunks",
            kwargs={"document_id": str(document.pk)},
            queue="cpu",
        )
    except Exception as exc:
        logger.exception("Không gửi được task giảm kích thước «%s»", document.title)
        return False, str(exc)
    return True, f"{n} phân đoạn vượt {limit} token."


def enqueue_resize_chunks(chunk_ids: list[str]) -> tuple[bool, str]:
    ids = [str(item) for item in chunk_ids if item]
    if not ids:
        return False, "Chưa chọn phân đoạn."
    limit = int(get_chunk_resize_config().max_tokens or 5000)
    n = SourceChunk.objects.filter(pk__in=ids, token_count__gt=limit).count()
    if not n:
        return False, f"Các đoạn đã chọn đều ≤ {limit} token."
    try:
        current_app.send_task(
            "apps.dataset.tasks.resize_selected_chunks",
            kwargs={"chunk_ids": ids},
            queue="cpu",
        )
    except Exception as exc:
        logger.exception("Không gửi được task giảm kích thước %s đoạn", len(ids))
        return False, str(exc)
    return True, f"{n} phân đoạn vượt {limit} token."
