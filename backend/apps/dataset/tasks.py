from __future__ import annotations

import logging
import time
from typing import Any

from celery import current_app, shared_task
from django.utils import timezone

from apps.dataset.models import (
    ChunkingBatchStatus,
    SourceChunkedText,
    SourceChunkingBatch,
    SourceSegment,
)
from apps.documents.task_control import (
    TaskCancelled,
    acquire_chunking_lock,
    check_chunking_cancelled,
    clear_chunking_cancel,
    release_chunking_lock,
)
from apps.llm.chunking_runner import (
    chunk_page_text,
    chunk_pages,
    count_breaks,
    iter_chunk_windows,
    normalize_chunk_window,
)
from apps.llm.page_stitch_runner import (
    get_stitch_prompt,
    inject_page_comments,
    stitch_page_texts,
)

logger = logging.getLogger(__name__)


def _store_segment_chunk(*, batch: SourceChunkingBatch, segment: SourceSegment, text: str) -> int:
    breaks = count_breaks(text or "")
    SourceChunkedText.objects.update_or_create(
        batch=batch,
        segment=segment,
        defaults={"text_md": text or "", "break_count": breaks},
    )
    return breaks


def _chunk_and_store_segment(*, batch: SourceChunkingBatch, segment: SourceSegment, source: str) -> tuple[bool, int, int, str]:
    try:
        chunked = chunk_page_text(
            provider=batch.provider,
            prompt_template=batch.prompt,
            book_title=batch.document.title,
            page_number=segment.page_number,
            page_text=source,
            model=getattr(batch, "model", "") or "",
        )
        text = chunked or source
        breaks = _store_segment_chunk(batch=batch, segment=segment, text=text)
        return True, len(text), breaks, ""
    except Exception as e:
        _store_segment_chunk(batch=batch, segment=segment, text=source)
        return False, len(source), 0, str(e)


def _chunk_segments_window(
    *,
    batch: SourceChunkingBatch,
    title: str,
    items: list[tuple[SourceSegment, str]],
    lock_prefix: int,
    kept_texts: list[str],
) -> tuple[list[str], str]:
    origs = [orig for _seg, orig in items]
    fallback = [
        kept_texts[i] if i < lock_prefix and i < len(kept_texts) and (kept_texts[i] or "").strip() else orig
        for i, orig in enumerate(origs)
    ]
    try:
        texts = chunk_pages(
            provider=batch.provider,
            prompt_template=batch.prompt,
            book_title=title,
            pages=[(segment.page_number, orig) for segment, orig in items],
            model=getattr(batch, "model", "") or "",
            lock_prefix=lock_prefix,
            kept_texts=kept_texts,
        )
        if len(texts) != len(items):
            return fallback, "LLM trả về số trang không khớp cửa sổ."
        return [text or fallback[i] for i, text in enumerate(texts)], ""
    except Exception as e:
        return fallback, str(e)


@shared_task(bind=True, name="apps.dataset.tasks.chunk_source_document")
def chunk_source_document(self, *, batch_id: str) -> dict[str, Any]:
    """Phân đoạn văn bản nguồn theo cửa sổ N trang, trùng M trang."""
    task_id = self.request.id or "local"
    if not acquire_chunking_lock(batch_id, task_id):
        logger.warning("Batch phân đoạn dataset %s đang chạy — bỏ qua", batch_id)
        return {"batch_id": batch_id, "skipped": True, "reason": "already_processing"}

    batch = SourceChunkingBatch.objects.select_related("document", "provider").get(id=batch_id)
    document = batch.document
    window_pages, overlap_pages = normalize_chunk_window(batch.window_pages, batch.overlap_pages)

    SourceChunkingBatch.objects.filter(pk=batch.pk).update(
        status=ChunkingBatchStatus.PROCESSING,
        error_message="",
        updated_at=timezone.now(),
    )

    segments = list(SourceSegment.objects.filter(document=document).order_by("page_number"))
    done = 0
    failed = 0
    t0 = time.perf_counter()

    try:
        check_chunking_cancelled(batch_id)
        work: list[tuple[SourceSegment, str]] = []
        for segment in segments:
            source = (segment.text_md or "").strip()
            if not source:
                _store_segment_chunk(batch=batch, segment=segment, text="")
                continue
            work.append((segment, source))

        results: dict = {}
        joined_n = 0
        if work:
            page_numbers = [segment.page_number for segment, _text in work]
            stitched, joined_into_prev, joined_n = stitch_page_texts(
                [text for _segment, text in work],
                page_numbers=page_numbers,
                provider=batch.provider,
                model=getattr(batch, "model", "") or "",
                prompt_template=get_stitch_prompt(),
                on_check=lambda: check_chunking_cancelled(batch_id),
            )
            marked = inject_page_comments(
                stitched,
                page_numbers=page_numbers,
                joined_into_prev=joined_into_prev,
            )
            work = [(segment, text) for (segment, _old), text in zip(work, marked)]
            if joined_n:
                logger.info(
                    "Nối %s ranh giới trang trước khi phân đoạn «%s»",
                    joined_n,
                    document.title,
                    extra={"document_id": str(document.id), "batch_id": str(batch.id)},
                )

        if len(work) == 1:
            segment, source = work[0]
            t_page = time.perf_counter()
            ok, chars, breaks, error = _chunk_and_store_segment(batch=batch, segment=segment, source=source)
            if ok:
                done += 1
                logger.info(
                    "Phân đoạn dataset trang %s/%s OK — %.1fs, %s ký tự, %s mốc",
                    segment.page_number,
                    len(segments),
                    time.perf_counter() - t_page,
                    chars,
                    breaks,
                    extra={"document_id": str(document.id), "batch_id": str(batch.id)},
                )
            else:
                failed += 1
                logger.warning(
                    "Phân đoạn dataset trang %s/%s lỗi: %s — giữ bản gốc",
                    segment.page_number,
                    len(segments),
                    error,
                    extra={"document_id": str(document.id), "batch_id": str(batch.id)},
                )
        elif len(work) >= 2:
            for start, end, lock_prefix in iter_chunk_windows(len(work), window_pages, overlap_pages):
                check_chunking_cancelled(batch_id)
                items = work[start:end]
                kept_texts = [
                    results.get(segment.pk, orig) if i < lock_prefix else orig
                    for i, (segment, orig) in enumerate(items)
                ]
                t_win = time.perf_counter()
                texts, error = _chunk_segments_window(
                    batch=batch,
                    title=document.title,
                    items=items,
                    lock_prefix=lock_prefix,
                    kept_texts=kept_texts,
                )
                break_counts = []
                for (segment, _orig), text in zip(items, texts):
                    break_counts.append(_store_segment_chunk(batch=batch, segment=segment, text=text))
                    results[segment.pk] = text
                first_page = items[0][0].page_number
                last_page = items[-1][0].page_number
                new_count = len(items) - lock_prefix
                if error:
                    failed += 1
                    logger.warning(
                        "Phân đoạn dataset cửa sổ %s–%s lỗi: %s",
                        first_page,
                        last_page,
                        error,
                        extra={"document_id": str(document.id), "batch_id": str(batch.id)},
                    )
                else:
                    done += new_count
                    logger.info(
                        "Phân đoạn dataset cửa sổ %s–%s (khóa %s, mới %s) OK — %.1fs, mốc %s",
                        first_page,
                        last_page,
                        lock_prefix,
                        new_count,
                        time.perf_counter() - t_win,
                        "+".join(str(c) for c in break_counts),
                        extra={"document_id": str(document.id), "batch_id": str(batch.id)},
                    )

        total_breaks = sum(
            SourceChunkedText.objects.filter(batch=batch).values_list("break_count", flat=True)
        )
        error_message = ""
        if failed:
            error_message = f"{failed} cửa sổ phân đoạn lỗi — đã giữ bản đã có cho các trang đó."
        if joined_n:
            extra = f"Đã nối {joined_n} ranh giới trang."
            error_message = f"{error_message} {extra}".strip() if error_message else extra
        if total_breaks:
            extra = f"Đã chèn {total_breaks} mốc </break>."
            error_message = f"{error_message} {extra}".strip() if error_message else extra
        window_note = f"Cửa sổ {window_pages} trang, trùng {overlap_pages}."
        error_message = f"{window_note} {error_message}".strip() if error_message else window_note

        SourceChunkingBatch.objects.filter(pk=batch.pk).update(
            status=ChunkingBatchStatus.DONE,
            error_message=error_message,
            updated_at=timezone.now(),
        )
        current_app.send_task(
            "apps.dataset.tasks.split_source_chunks",
            kwargs={"document_id": str(document.id)},
            queue="cpu",
        )
        logger.info(
            "Hoàn thành phân đoạn dataset «%s» — cửa sổ %s trùng %s, %s trang mới OK, %s lỗi, %s mốc, %.1fs",
            document.title,
            window_pages,
            overlap_pages,
            done,
            failed,
            total_breaks,
            time.perf_counter() - t0,
            extra={"document_id": str(document.id), "batch_id": str(batch.id)},
        )
        return {
            "batch_id": str(batch.id),
            "document_id": str(document.id),
            "pages": len(segments),
            "window_pages": window_pages,
            "overlap_pages": overlap_pages,
            "done": done,
            "failed": failed,
            "breaks": total_breaks,
            "duration_s": round(time.perf_counter() - t0, 2),
        }

    except TaskCancelled as e:
        SourceChunkingBatch.objects.filter(pk=batch.pk).update(
            status=ChunkingBatchStatus.FAILED,
            error_message=str(e),
            updated_at=timezone.now(),
        )
        logger.info(
            "Phân đoạn dataset «%s» đã hủy",
            document.title,
            extra={"document_id": str(document.id), "batch_id": batch_id},
        )
        return {"batch_id": batch_id, "cancelled": True, "error": str(e)}

    except Exception as e:
        SourceChunkingBatch.objects.filter(pk=batch.pk).update(
            status=ChunkingBatchStatus.FAILED,
            error_message=f"{type(e).__name__}: {e}",
            updated_at=timezone.now(),
        )
        raise

    finally:
        release_chunking_lock(batch_id, task_id)
        clear_chunking_cancel(batch_id)


@shared_task(bind=True, name="apps.dataset.tasks.split_source_chunks")
def split_source_chunks(self, *, document_id: str) -> dict[str, Any]:
    """Tách MD đã phân đoạn theo </break> thành các khối lưu DB."""
    from apps.dataset.markdown import rebuild_document_chunks
    from apps.dataset.models import SourceDocument

    document = SourceDocument.objects.get(id=document_id)
    count = rebuild_document_chunks(document)
    logger.info(
        "Đã tách %s khối phân đoạn cho «%s»",
        count,
        document.title,
        extra={"document_id": str(document.id), "chunks": count},
    )
    return {"document_id": document_id, "chunks": count}


@shared_task(bind=True, name="apps.dataset.tasks.classify_source_document")
def classify_source_document(self, *, document_id: str) -> dict[str, Any]:
    """Đọc các đoạn Markdown đầu và điền phân loại bằng LLM."""
    from apps.dataset.models import SourceDocument
    from apps.llm.source_classify_runner import classify_source_metadata

    document = SourceDocument.objects.get(id=document_id)
    result = classify_source_metadata(document)
    logger.info(
        "Đã phân loại văn bản «%s» bằng %s: %s",
        document.title,
        result.get("provider"),
        ", ".join(result.get("filled") or []) or "(không điền được trường nào)",
    )
    return {"document_id": document_id, **{k: v for k, v in result.items() if k != "raw"}}


_RAGFLOW_SYNC_LIMIT = 6 * 3600
_RAGFLOW_UPDATE_LIMIT = 600


@shared_task(
    bind=True,
    name="apps.dataset.tasks.sync_source_to_ragflow",
    time_limit=_RAGFLOW_SYNC_LIMIT,
    soft_time_limit=_RAGFLOW_SYNC_LIMIT - 60,
)
def sync_source_to_ragflow(self, *, document_id: str) -> dict[str, Any]:
    """Tạo document rỗng trên RAGFlow, gắn meta, rồi Add chunk tuần tự."""
    from apps.dataset.models import RagflowSyncStatus, SourceChunk, SourceDocument
    from apps.dataset.ragflow import (
        RAGFlowClient,
        RAGFlowError,
        document_meta_fields,
        format_ragflow_chunk_content,
        get_ragflow_config,
        ragflow_document_name,
        ragflow_ready,
    )

    document = SourceDocument.objects.select_related(
        "class_source",
        "class_field",
        "class_language",
        "class_doc_type",
        "class_era",
    ).get(id=document_id)

    def _fail(message: str) -> dict[str, Any]:
        SourceDocument.objects.filter(pk=document.pk).update(
            ragflow_status=RagflowSyncStatus.FAILED,
            ragflow_error=message,
            updated_at=timezone.now(),
        )
        logger.warning(
            "Nạp RAGFlow «%s» lỗi: %s",
            document.title,
            message,
            extra={"document_id": str(document.id)},
        )
        return {"document_id": document_id, "ok": False, "error": message}

    config = get_ragflow_config()
    ok, detail = ragflow_ready(config)
    if not ok:
        return _fail(detail)
    catalog = (document.catalog_code or "").strip()
    if not catalog:
        return _fail("Chưa có mã văn bản.")

    SourceDocument.objects.filter(pk=document.pk).update(
        ragflow_status=RagflowSyncStatus.PROCESSING,
        ragflow_error="",
        updated_at=timezone.now(),
    )

    client = RAGFlowClient(config)
    try:
        ragflow_doc_id = (document.ragflow_document_id or "").strip()
        if not ragflow_doc_id:
            ragflow_doc_id = client.create_empty_document(ragflow_document_name(document.title))
            SourceDocument.objects.filter(pk=document.pk).update(
                ragflow_document_id=ragflow_doc_id,
                updated_at=timezone.now(),
            )
            document.ragflow_document_id = ragflow_doc_id
        client.update_document(ragflow_doc_id, meta_fields=document_meta_fields(document))
    except RAGFlowError as exc:
        return _fail(str(exc))

    pending = list(
        SourceChunk.objects.filter(document=document)
        .filter(ragflow_chunk_id="")
        .order_by("position")
    )
    uploaded = 0
    for chunk in pending:
        t0 = time.perf_counter()
        content = format_ragflow_chunk_content(chunk.text_md or "", config.embed_prefix)
        try:
            ragflow_chunk_id = client.add_chunk(ragflow_doc_id, content)
        except RAGFlowError as exc:
            return _fail(f"Đoạn {chunk.position}: {exc}")
        SourceChunk.objects.filter(pk=chunk.pk).update(
            ragflow_chunk_id=ragflow_chunk_id,
            updated_at=timezone.now(),
        )
        uploaded += 1
        elapsed = time.perf_counter() - t0
        logger.info(
            "Đã nạp đoạn %s «%s» lên RAGFlow trong %.1fs",
            chunk.position,
            document.title,
            elapsed,
            extra={
                "document_id": str(document.id),
                "chunk_id": str(chunk.id),
                "duration_s": round(elapsed, 2),
            },
        )

    SourceDocument.objects.filter(pk=document.pk).update(
        ragflow_status=RagflowSyncStatus.DONE,
        ragflow_error="",
        ragflow_synced_at=timezone.now(),
        updated_at=timezone.now(),
    )
    logger.info(
        "Hoàn thành nạp RAGFlow «%s» — %s khối mới",
        document.title,
        uploaded,
        extra={"document_id": str(document.id), "uploaded": uploaded},
    )
    return {"document_id": document_id, "ok": True, "uploaded": uploaded}


@shared_task(
    bind=True,
    name="apps.dataset.tasks.update_ragflow_chunk",
    time_limit=_RAGFLOW_UPDATE_LIMIT,
    soft_time_limit=_RAGFLOW_UPDATE_LIMIT - 30,
)
def update_ragflow_chunk(self, *, chunk_id: str) -> dict[str, Any]:
    """PATCH nội dung chunk đã nạp trên RAGFlow (re-embed)."""
    from apps.dataset.models import SourceChunk
    from apps.dataset.ragflow import (
        RAGFlowClient,
        RAGFlowError,
        format_ragflow_chunk_content,
        get_ragflow_config,
        ragflow_ready,
    )

    chunk = SourceChunk.objects.select_related("document").get(id=chunk_id)
    document = chunk.document
    ragflow_doc_id = (document.ragflow_document_id or "").strip()
    ragflow_chunk_id = (chunk.ragflow_chunk_id or "").strip()
    if not ragflow_doc_id or not ragflow_chunk_id:
        return {"chunk_id": chunk_id, "ok": False, "skipped": True}

    config = get_ragflow_config()
    ok, detail = ragflow_ready(config)
    if not ok:
        logger.warning("Cập nhật RAGFlow đoạn %s bỏ qua: %s", chunk.position, detail)
        return {"chunk_id": chunk_id, "ok": False, "error": detail}

    content = format_ragflow_chunk_content(chunk.text_md or "", config.embed_prefix)
    t0 = time.perf_counter()
    try:
        RAGFlowClient(config).update_chunk(ragflow_doc_id, ragflow_chunk_id, content)
    except RAGFlowError as exc:
        logger.warning(
            "Cập nhật RAGFlow đoạn %s «%s» lỗi: %s",
            chunk.position,
            document.title,
            exc,
            extra={"document_id": str(document.id), "chunk_id": str(chunk.id)},
        )
        return {"chunk_id": chunk_id, "ok": False, "error": str(exc)}
    elapsed = time.perf_counter() - t0
    logger.info(
        "Đã cập nhật đoạn %s «%s» trên RAGFlow trong %.1fs",
        chunk.position,
        document.title,
        elapsed,
        extra={"document_id": str(document.id), "chunk_id": str(chunk.id), "duration_s": round(elapsed, 2)},
    )
    return {"chunk_id": chunk_id, "ok": True, "duration_s": round(elapsed, 2)}
