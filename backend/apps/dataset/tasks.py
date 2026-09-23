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
    check_ragflow_cancelled,
    clear_chunking_cancel,
    clear_ragflow_cancel,
    release_chunking_lock,
    release_ragflow_lock,
    set_ragflow_task_id,
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


def _ensure_chunking_writable(batch: SourceChunkingBatch, batch_id: str) -> None:
    """Dừng ghi kết quả nếu user đã hủy hoặc xóa lần chạy."""
    check_chunking_cancelled(batch_id)
    if not SourceChunkingBatch.objects.filter(
        pk=batch.pk,
        status=ChunkingBatchStatus.PROCESSING,
    ).exists():
        raise TaskCancelled("Lần phân đoạn đã bị hủy hoặc xóa.")


def _finish_chunking_batch(batch: SourceChunkingBatch, *, note: str) -> None:
    updated = SourceChunkingBatch.objects.filter(
        pk=batch.pk,
        status=ChunkingBatchStatus.PROCESSING,
    ).update(
        status=ChunkingBatchStatus.DONE,
        error_message=note,
        updated_at=timezone.now(),
    )
    if not updated:
        raise TaskCancelled("Lần phân đoạn đã bị hủy hoặc xóa.")
    document = getattr(batch, "document", None)
    if document is not None:
        document.touch()
    else:
        from apps.dataset.models import SourceDocument

        SourceDocument.objects.filter(pk=batch.document_id).update(updated_at=timezone.now())


def _restore_chunks_after_cancel(document) -> None:
    """Hủy giữa chừng: lấy lại khối của lần DONE trước, hoặc xóa nếu không còn."""
    from apps.dataset.markdown import get_active_chunking_batch, rebuild_document_chunks
    from apps.dataset.models import SourceChunk

    if get_active_chunking_batch(document):
        rebuild_document_chunks(document)
        return
    SourceChunk.objects.filter(document=document).delete()


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


def _chunk_source_document_poincare(self, *, batch, document, batch_id: str, task_id: str) -> dict[str, Any]:
    from apps.dataset.poincare_runner import run_poincare_chunking, split_poincare_pages
    from apps.llm.chunking_runner import get_chunking_config

    SourceChunkingBatch.objects.filter(
        pk=batch.pk,
        status__in=[ChunkingBatchStatus.PENDING, ChunkingBatchStatus.PROCESSING],
    ).update(
        status=ChunkingBatchStatus.PROCESSING,
        error_message="",
        updated_at=timezone.now(),
    )
    document.touch()
    t0 = time.perf_counter()
    try:
        _ensure_chunking_writable(batch, batch_id)
        config = get_chunking_config()
        result = run_poincare_chunking(
            document,
            config,
            on_check=lambda: check_chunking_cancelled(batch_id),
        )
        _ensure_chunking_writable(batch, batch_id)
        segments = list(SourceSegment.objects.filter(document=document).order_by("page_number"))
        if segments:
            pages = split_poincare_pages(
                result["text"],
                [int(segment.page_number or 1) for segment in segments],
            )
            for segment in segments:
                text = pages.get(int(segment.page_number or 1), "") or ""
                _store_segment_chunk(batch=batch, segment=segment, text=text)
        total_breaks = int(result.get("breaks") or 0)
        joined_n = int(result.get("joined_pages") or 0)
        note = (
            f"Poincaré: {total_breaks} mốc </break>, {result.get('windows') or 0} cửa sổ"
            f"{f', nối {joined_n} đoạn qua trang' if joined_n else ''}. "
            f"{result.get('summary') or ''}"
        )
        _ensure_chunking_writable(batch, batch_id)
        from apps.dataset.markdown import rebuild_document_chunks_from_content

        split_n = rebuild_document_chunks_from_content(
            document,
            f"# {document.title}\n\n{result['text']}",
        )
        _finish_chunking_batch(batch, note=note)
        logger.info(
            "Hoàn thành phân đoạn Poincaré «%s» — %s mốc, %s khối, %.1fs",
            document.title,
            total_breaks,
            split_n,
            time.perf_counter() - t0,
        )
        return {
            "batch_id": str(batch.id),
            "document_id": str(document.id),
            "method": "poincare",
            "breaks": total_breaks,
            "chunks": split_n,
            "windows": result.get("windows") or 0,
            "duration_s": round(time.perf_counter() - t0, 2),
        }
    except TaskCancelled as exc:
        SourceChunkingBatch.objects.filter(pk=batch.pk).exclude(
            status=ChunkingBatchStatus.DONE,
        ).update(
            status=ChunkingBatchStatus.FAILED,
            error_message=str(exc),
            updated_at=timezone.now(),
        )
        document.touch()
        _restore_chunks_after_cancel(document)
        return {"batch_id": batch_id, "cancelled": True, "error": str(exc)}
    except Exception as exc:
        SourceChunkingBatch.objects.filter(pk=batch.pk).update(
            status=ChunkingBatchStatus.FAILED,
            error_message=f"{type(exc).__name__}: {exc}",
            updated_at=timezone.now(),
        )
        document.touch()
        raise
    finally:
        release_chunking_lock(batch_id, task_id)
        clear_chunking_cancel(batch_id)


@shared_task(bind=True, name="apps.dataset.tasks.chunk_source_document")
def chunk_source_document(self, *, batch_id: str) -> dict[str, Any]:
    """Phân đoạn văn bản nguồn theo cửa sổ N trang, trùng M trang."""
    task_id = self.request.id or "local"
    if not acquire_chunking_lock(batch_id, task_id):
        logger.warning("Batch phân đoạn dataset %s đang chạy — bỏ qua", batch_id)
        return {"batch_id": batch_id, "skipped": True, "reason": "already_processing"}

    try:
        batch = SourceChunkingBatch.objects.select_related("document", "provider").get(id=batch_id)
    except SourceChunkingBatch.DoesNotExist:
        release_chunking_lock(batch_id, task_id)
        clear_chunking_cancel(batch_id)
        return {"batch_id": batch_id, "cancelled": True, "error": "Lần phân đoạn đã bị xóa."}
    document = batch.document
    from apps.llm.models import ChunkingMethod

    if batch.status not in {ChunkingBatchStatus.PENDING, ChunkingBatchStatus.PROCESSING}:
        release_chunking_lock(batch_id, task_id)
        clear_chunking_cancel(batch_id)
        return {"batch_id": batch_id, "cancelled": True, "error": "Lần phân đoạn không còn chạy."}

    if (getattr(batch, "method", None) or ChunkingMethod.LLM) == ChunkingMethod.POINCARE:
        return _chunk_source_document_poincare(self, batch=batch, document=document, batch_id=batch_id, task_id=task_id)

    window_pages, overlap_pages = normalize_chunk_window(batch.window_pages, batch.overlap_pages)

    SourceChunkingBatch.objects.filter(
        pk=batch.pk,
        status__in=[ChunkingBatchStatus.PENDING, ChunkingBatchStatus.PROCESSING],
    ).update(
        status=ChunkingBatchStatus.PROCESSING,
        error_message="",
        updated_at=timezone.now(),
    )
    document.touch()

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

        _ensure_chunking_writable(batch, batch_id)
        from apps.dataset.markdown import rebuild_document_chunks

        try:
            split_n = rebuild_document_chunks(document)
        except Exception:
            logger.exception(
                "Tách khối phân đoạn thất bại sau khi phân đoạn «%s»",
                document.title,
                extra={"document_id": str(document.id), "batch_id": str(batch.id)},
            )
            raise
        _finish_chunking_batch(batch, note=error_message)
        logger.info(
            "Hoàn thành phân đoạn dataset «%s» — cửa sổ %s trùng %s, %s trang mới OK, %s lỗi, %s mốc, %s khối, %.1fs",
            document.title,
            window_pages,
            overlap_pages,
            done,
            failed,
            total_breaks,
            split_n,
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
            "chunks": split_n,
            "duration_s": round(time.perf_counter() - t0, 2),
        }

    except TaskCancelled as e:
        SourceChunkingBatch.objects.filter(pk=batch.pk).exclude(
            status=ChunkingBatchStatus.DONE,
        ).update(
            status=ChunkingBatchStatus.FAILED,
            error_message=str(e),
            updated_at=timezone.now(),
        )
        document.touch()
        _restore_chunks_after_cancel(document)
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
        document.touch()
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
        PageImageBase64Cache,
        RAGFlowClient,
        RAGFlowError,
        document_meta_fields,
        forget_ragflow_binding,
        get_ragflow_config,
        ragflow_document_missing,
        ragflow_document_name,
        ragflow_ready,
    )

    document = SourceDocument.objects.select_related(
        "book",
        "class_source",
        "class_field",
        "class_language",
        "class_doc_type",
        "class_era",
    ).get(id=document_id)
    task_id = self.request.id or "local"
    set_ragflow_task_id(str(document.id), task_id)

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
        release_ragflow_lock(str(document.id), task_id)
        clear_ragflow_cancel(str(document.id))
        return {"document_id": document_id, "ok": False, "error": message}

    try:
        check_ragflow_cancelled(str(document.id))
    except TaskCancelled as exc:
        SourceDocument.objects.filter(pk=document.pk).update(
            ragflow_status=RagflowSyncStatus.FAILED,
            ragflow_error=str(exc),
            updated_at=timezone.now(),
        )
        clear_ragflow_cancel(str(document.id))
        release_ragflow_lock(str(document.id), task_id)
        return {"document_id": document_id, "ok": False, "cancelled": True, "error": str(exc)}

    config = get_ragflow_config()
    ok, detail = ragflow_ready(config)
    if not ok:
        release_ragflow_lock(str(document.id), task_id)
        return _fail(detail)
    catalog = (document.catalog_code or "").strip()
    if not catalog:
        release_ragflow_lock(str(document.id), task_id)
        return _fail("Chưa có mã văn bản.")

    SourceDocument.objects.filter(pk=document.pk).update(
        ragflow_status=RagflowSyncStatus.PROCESSING,
        ragflow_error="",
        updated_at=timezone.now(),
    )

    client = RAGFlowClient(config)
    try:
        ragflow_doc_id = (document.ragflow_document_id or "").strip()
        if ragflow_doc_id:
            try:
                client.update_document(ragflow_doc_id, meta_fields=document_meta_fields(document))
            except RAGFlowError as exc:
                if not ragflow_document_missing(exc):
                    raise
                logger.warning(
                    "Document RAGFlow «%s» không còn (%s) — tạo lại và nạp toàn bộ chunk",
                    document.title,
                    exc,
                    extra={"document_id": str(document.id), "ragflow_document_id": ragflow_doc_id},
                )
                forget_ragflow_binding(document)
                ragflow_doc_id = ""
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
        .select_related("document", "document__book")
        .order_by("position")
    )
    page_images = PageImageBase64Cache()
    uploaded = 0
    try:
        for chunk in pending:
            check_ragflow_cancelled(str(document.id))
            t0 = time.perf_counter()
            content = chunk.text_md or ""
            image_base64 = page_images.for_chunk(chunk)
            try:
                ragflow_ref = client.add_chunk(
                    ragflow_doc_id,
                    content,
                    image_base64=image_base64,
                )
            except RAGFlowError as exc:
                return _fail(f"Đoạn {chunk.position}: {exc}")
            SourceChunk.objects.filter(pk=chunk.pk).update(
                ragflow_chunk_id=ragflow_ref.chunk_id,
                ragflow_image_id=ragflow_ref.image_id,
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
    except TaskCancelled as exc:
        SourceDocument.objects.filter(pk=document.pk).update(
            ragflow_status=RagflowSyncStatus.FAILED,
            ragflow_error=str(exc),
            updated_at=timezone.now(),
        )
        logger.info(
            "Nạp RAGFlow «%s» đã ngưng — đã nạp %s khối",
            document.title,
            uploaded,
            extra={"document_id": str(document.id), "uploaded": uploaded},
        )
        return {
            "document_id": document_id,
            "ok": False,
            "cancelled": True,
            "uploaded": uploaded,
            "error": str(exc),
        }
    finally:
        release_ragflow_lock(str(document.id), task_id)
        clear_ragflow_cancel(str(document.id))


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
        PageImageBase64Cache,
        RAGFlowClient,
        RAGFlowError,
        get_ragflow_config,
        ragflow_ready,
    )

    chunk = SourceChunk.objects.select_related("document", "document__book").get(id=chunk_id)
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

    content = chunk.text_md or ""
    image_base64 = PageImageBase64Cache().for_chunk(chunk)
    t0 = time.perf_counter()
    try:
        image_id = RAGFlowClient(config).update_chunk(
            ragflow_doc_id,
            ragflow_chunk_id,
            content,
            image_base64=image_base64,
        )
    except RAGFlowError as exc:
        logger.warning(
            "Cập nhật RAGFlow đoạn %s «%s» lỗi: %s",
            chunk.position,
            document.title,
            exc,
            extra={"document_id": str(document.id), "chunk_id": str(chunk.id)},
        )
        return {"chunk_id": chunk_id, "ok": False, "error": str(exc)}
    if image_id:
        SourceChunk.objects.filter(pk=chunk.pk).update(
            ragflow_image_id=image_id,
            updated_at=timezone.now(),
        )
    elapsed = time.perf_counter() - t0
    logger.info(
        "Đã cập nhật đoạn %s «%s» trên RAGFlow trong %.1fs",
        chunk.position,
        document.title,
        elapsed,
        extra={"document_id": str(document.id), "chunk_id": str(chunk.id), "duration_s": round(elapsed, 2)},
    )
    return {"chunk_id": chunk_id, "ok": True, "duration_s": round(elapsed, 2)}


_RESIZE_LIMIT = 6 * 3600


@shared_task(
    bind=True,
    name="apps.dataset.tasks.resize_document_chunks",
    time_limit=_RESIZE_LIMIT,
    soft_time_limit=_RESIZE_LIMIT - 60,
)
def resize_document_chunks(self, *, document_id: str) -> dict[str, Any]:
    """Tách lại các phân đoạn vượt hạn token của một văn bản."""
    from apps.dataset.models import SourceDocument
    from apps.dataset.resize import resize_oversized_chunks

    document = SourceDocument.objects.get(id=document_id)
    result = resize_oversized_chunks(document=document)
    logger.info(
        "Giảm kích thước «%s»: tách thêm %s đoạn, bỏ qua %s, lỗi %s (hạn %s token)",
        document.title,
        result.get("split"),
        result.get("skipped"),
        result.get("failed"),
        result.get("limit"),
        extra={"document_id": str(document.id), **result},
    )
    return {"document_id": document_id, **result}


@shared_task(
    bind=True,
    name="apps.dataset.tasks.resize_selected_chunks",
    time_limit=_RESIZE_LIMIT,
    soft_time_limit=_RESIZE_LIMIT - 60,
)
def resize_selected_chunks(self, *, chunk_ids: list[str]) -> dict[str, Any]:
    """Tách lại các phân đoạn đã chọn nếu vượt hạn token."""
    from apps.dataset.resize import resize_oversized_chunks

    result = resize_oversized_chunks(chunk_ids=chunk_ids)
    logger.info(
        "Giảm kích thước %s đoạn đã chọn: tách thêm %s, bỏ qua %s, lỗi %s",
        len(chunk_ids),
        result.get("split"),
        result.get("skipped"),
        result.get("failed"),
        extra=result,
    )
    return result


_IMAGE_EXTRACT_LIMIT = 6 * 3600


@shared_task(
    bind=True,
    name="apps.dataset.tasks.extract_book_images",
    time_limit=_IMAGE_EXTRACT_LIMIT,
    soft_time_limit=_IMAGE_EXTRACT_LIMIT - 60,
)
def extract_book_images_task(self, *, book_id: str) -> dict[str, Any]:
    """Rút trích bbox hình từ sách, Vision, lưu ImageChunk."""
    from apps.dataset.extract_images import extract_book_images
    from apps.dataset.models import ImageExtractStatus, ImageSourceDocument
    from apps.documents.models import Book
    from apps.documents.task_control import (
        TaskCancelled,
        check_image_extract_cancelled,
        clear_image_extract_cancel,
        release_image_extract_lock,
        set_image_extract_task_id,
    )

    book = Book.objects.get(id=book_id)
    task_id = self.request.id or "local"
    set_image_extract_task_id(book_id, task_id)

    def _fail(message: str) -> dict[str, Any]:
        ImageSourceDocument.objects.filter(book_id=book.pk).update(
            extract_status=ImageExtractStatus.FAILED,
            extract_error=message,
            updated_at=timezone.now(),
        )
        release_image_extract_lock(book_id, task_id)
        clear_image_extract_cancel(book_id)
        return {"book_id": book_id, "ok": False, "error": message}

    try:
        check_image_extract_cancelled(book_id)
        result = extract_book_images(
            book,
            check_cancel=lambda: check_image_extract_cancelled(book_id),
        )
        logger.info(
            "Rút trích hình «%s»: tạo %s, bỏ qua %s, khung chữ %s, lỗi %s",
            book.title,
            result.get("created"),
            result.get("skipped"),
            result.get("skipped_text"),
            result.get("errors"),
        )
        return {"book_id": book_id, "ok": True, **result}
    except TaskCancelled as exc:
        ImageSourceDocument.objects.filter(book_id=book.pk).update(
            extract_status=ImageExtractStatus.FAILED,
            extract_error=str(exc),
            updated_at=timezone.now(),
        )
        logger.info("Rút trích hình «%s» đã ngưng.", book.title)
        return {"book_id": book_id, "ok": False, "cancelled": True, "error": str(exc)}
    except Exception as exc:
        logger.exception("Rút trích hình «%s» lỗi.", book.title)
        return _fail(f"{type(exc).__name__}: {exc}")
    finally:
        release_image_extract_lock(book_id, task_id)
        clear_image_extract_cancel(book_id)


@shared_task(
    bind=True,
    name="apps.dataset.tasks.sync_image_source_to_ragflow",
    time_limit=_RAGFLOW_SYNC_LIMIT,
    soft_time_limit=_RAGFLOW_SYNC_LIMIT - 60,
)
def sync_image_source_to_ragflow(self, *, document_id: str) -> dict[str, Any]:
    """Nạp phân đoạn hình (ảnh crop + text) lên knowledge base hình."""
    from apps.dataset.models import ImageChunk, ImageSourceDocument, RagflowSyncStatus
    from apps.dataset.ragflow import (
        RAGFlowClient,
        RAGFlowError,
        forget_image_ragflow_binding,
        get_ragflow_config,
        image_chunk_base64,
        image_document_meta_fields,
        image_ragflow_ready,
        ragflow_document_missing,
        ragflow_document_name,
    )

    document = ImageSourceDocument.objects.select_related(
        "book",
        "class_source",
        "class_field",
        "class_language",
        "class_doc_type",
        "class_era",
    ).get(id=document_id)
    task_id = self.request.id or "local"
    set_ragflow_task_id(str(document.id), task_id)

    def _fail(message: str) -> dict[str, Any]:
        ImageSourceDocument.objects.filter(pk=document.pk).update(
            ragflow_status=RagflowSyncStatus.FAILED,
            ragflow_error=message,
            updated_at=timezone.now(),
        )
        logger.warning("Nạp RAGFlow hình «%s» lỗi: %s", document.title, message)
        release_ragflow_lock(str(document.id), task_id)
        clear_ragflow_cancel(str(document.id))
        return {"document_id": document_id, "ok": False, "error": message}

    try:
        check_ragflow_cancelled(str(document.id))
    except TaskCancelled as exc:
        ImageSourceDocument.objects.filter(pk=document.pk).update(
            ragflow_status=RagflowSyncStatus.FAILED,
            ragflow_error=str(exc),
            updated_at=timezone.now(),
        )
        clear_ragflow_cancel(str(document.id))
        release_ragflow_lock(str(document.id), task_id)
        return {"document_id": document_id, "ok": False, "cancelled": True, "error": str(exc)}

    config = get_ragflow_config()
    ok, detail = image_ragflow_ready(config)
    if not ok:
        release_ragflow_lock(str(document.id), task_id)
        return _fail(detail)
    if not (document.catalog_code or "").strip():
        release_ragflow_lock(str(document.id), task_id)
        return _fail("Chưa có mã văn bản.")

    ImageSourceDocument.objects.filter(pk=document.pk).update(
        ragflow_status=RagflowSyncStatus.PROCESSING,
        ragflow_error="",
        updated_at=timezone.now(),
    )

    client = RAGFlowClient(config, dataset_id=config.image_dataset_id)
    try:
        ragflow_doc_id = (document.ragflow_document_id or "").strip()
        if ragflow_doc_id:
            try:
                client.update_document(ragflow_doc_id, meta_fields=image_document_meta_fields(document))
            except RAGFlowError as exc:
                if not ragflow_document_missing(exc):
                    raise
                forget_image_ragflow_binding(document)
                ragflow_doc_id = ""
        if not ragflow_doc_id:
            ragflow_doc_id = client.create_empty_document(
                ragflow_document_name(f"{document.title} (hình)")
            )
            ImageSourceDocument.objects.filter(pk=document.pk).update(
                ragflow_document_id=ragflow_doc_id,
                updated_at=timezone.now(),
            )
            document.ragflow_document_id = ragflow_doc_id
            client.update_document(ragflow_doc_id, meta_fields=image_document_meta_fields(document))
    except RAGFlowError as exc:
        return _fail(str(exc))

    pending = list(
        ImageChunk.objects.filter(document=document, ragflow_chunk_id="")
        .order_by("position")
    )
    uploaded = 0
    try:
        for chunk in pending:
            check_ragflow_cancelled(str(document.id))
            t0 = time.perf_counter()
            try:
                ragflow_ref = client.add_chunk(
                    ragflow_doc_id,
                    chunk.text_md or "",
                    image_base64=image_chunk_base64(chunk),
                )
            except RAGFlowError as exc:
                return _fail(f"Đoạn {chunk.position}: {exc}")
            ImageChunk.objects.filter(pk=chunk.pk).update(
                ragflow_chunk_id=ragflow_ref.chunk_id,
                ragflow_image_id=ragflow_ref.image_id,
                updated_at=timezone.now(),
            )
            uploaded += 1
            logger.info(
                "Đã nạp hình %s «%s» lên RAGFlow trong %.1fs",
                chunk.position,
                document.title,
                time.perf_counter() - t0,
            )

        ImageSourceDocument.objects.filter(pk=document.pk).update(
            ragflow_status=RagflowSyncStatus.DONE,
            ragflow_error="",
            ragflow_synced_at=timezone.now(),
            updated_at=timezone.now(),
        )
        return {"document_id": document_id, "ok": True, "uploaded": uploaded}
    except TaskCancelled as exc:
        ImageSourceDocument.objects.filter(pk=document.pk).update(
            ragflow_status=RagflowSyncStatus.FAILED,
            ragflow_error=str(exc),
            updated_at=timezone.now(),
        )
        return {
            "document_id": document_id,
            "ok": False,
            "cancelled": True,
            "uploaded": uploaded,
            "error": str(exc),
        }
    finally:
        release_ragflow_lock(str(document.id), task_id)
        clear_ragflow_cancel(str(document.id))


@shared_task(
    bind=True,
    name="apps.dataset.tasks.update_image_ragflow_chunk",
    time_limit=_RAGFLOW_UPDATE_LIMIT,
    soft_time_limit=_RAGFLOW_UPDATE_LIMIT - 30,
)
def update_image_ragflow_chunk(self, *, chunk_id: str) -> dict[str, Any]:
    from apps.dataset.models import ImageChunk
    from apps.dataset.ragflow import (
        RAGFlowClient,
        RAGFlowError,
        get_ragflow_config,
        image_chunk_base64,
        image_ragflow_ready,
    )

    chunk = ImageChunk.objects.select_related("document").get(id=chunk_id)
    document = chunk.document
    ragflow_doc_id = (document.ragflow_document_id or "").strip()
    ragflow_chunk_id = (chunk.ragflow_chunk_id or "").strip()
    if not ragflow_doc_id or not ragflow_chunk_id:
        return {"chunk_id": chunk_id, "ok": False, "skipped": True}

    config = get_ragflow_config()
    ok, detail = image_ragflow_ready(config)
    if not ok:
        return {"chunk_id": chunk_id, "ok": False, "error": detail}

    try:
        image_id = RAGFlowClient(config, dataset_id=config.image_dataset_id).update_chunk(
            ragflow_doc_id,
            ragflow_chunk_id,
            chunk.text_md or "",
            image_base64=image_chunk_base64(chunk),
        )
    except RAGFlowError as exc:
        logger.warning("Cập nhật RAGFlow hình đoạn %s lỗi: %s", chunk.position, exc)
        return {"chunk_id": chunk_id, "ok": False, "error": str(exc)}
    if image_id:
        ImageChunk.objects.filter(pk=chunk.pk).update(
            ragflow_image_id=image_id,
            updated_at=timezone.now(),
        )
    return {"chunk_id": chunk_id, "ok": True}


@shared_task(
    bind=True,
    name="apps.dataset.tasks.filter_text_only_image_chunks",
    time_limit=_IMAGE_EXTRACT_LIMIT,
    soft_time_limit=_IMAGE_EXTRACT_LIMIT - 60,
)
def filter_text_only_image_chunks(self, *, chunk_ids: list[str]) -> dict[str, Any]:
    """Quét crop đã rút trích bằng Vision và xóa khung chữ thuần."""
    from apps.dataset.extract_images import purge_text_only_image_chunks

    result = purge_text_only_image_chunks(chunk_ids=chunk_ids)
    logger.info(
        "Lọc khung chữ: xóa %s, giữ %s, lỗi %s",
        result.get("removed"),
        result.get("kept"),
        result.get("errors"),
        extra=result,
    )
    return result
