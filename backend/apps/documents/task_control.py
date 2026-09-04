"""Điều khiển hủy task Celery (OCR / làm sạch văn bản)."""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class TaskCancelled(Exception):
    """Người dùng yêu cầu hủy task."""


def _redis_client():
    import redis

    return redis.from_url(settings.CELERY_BROKER_URL)


def _redis_get(key: str) -> str | None:
    try:
        raw = _redis_client().get(key)
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)
    except Exception as e:
        logger.warning("Redis GET %s lỗi: %s", key, e)
        return None


def _redis_set(key: str, value: str, *, ttl: int = 7200) -> None:
    try:
        _redis_client().set(key, value, ex=ttl)
    except Exception as e:
        logger.warning("Redis SET %s lỗi: %s", key, e)


def _redis_delete(key: str) -> None:
    try:
        _redis_client().delete(key)
    except Exception as e:
        logger.warning("Redis DEL %s lỗi: %s", key, e)


def revoke_celery_task(task_id: str) -> None:
    if not task_id or task_id == "local":
        return
    try:
        from celery import current_app

        current_app.control.revoke(task_id, terminate=True, signal="TERM")
    except Exception as e:
        logger.warning("Không revoke được task %s: %s", task_id, e)


def _book_cancel_key(book_id: str) -> str:
    return f"ocr:cancel:book:{book_id}"


def _book_lock_key(book_id: str) -> str:
    return f"ocr:lock:book:{book_id}"


def request_cancel_book_ocr(book_id: str) -> None:
    _redis_set(_book_cancel_key(book_id), "1")


def is_book_ocr_cancelled(book_id: str) -> bool:
    return bool(_redis_get(_book_cancel_key(book_id)))


def clear_book_ocr_cancel(book_id: str) -> None:
    _redis_delete(_book_cancel_key(book_id))


def get_book_ocr_task_id(book_id: str) -> str | None:
    return _redis_get(_book_lock_key(book_id))


def check_book_ocr_cancelled(book_id: str) -> None:
    if is_book_ocr_cancelled(book_id):
        raise TaskCancelled("Người dùng đã hủy OCR.")


def cancel_book_ocr(book_id: str) -> str:
    request_cancel_book_ocr(book_id)
    task_id = get_book_ocr_task_id(book_id)
    if task_id:
        revoke_celery_task(task_id)
        return f"Đã gửi yêu cầu hủy OCR (task {task_id[:8]}…)."
    return "Đã đặt cờ hủy OCR — task sẽ dừng ở bước kế tiếp."


def _cleaning_cancel_key(batch_id: str) -> str:
    return f"cleaning:cancel:batch:{batch_id}"


def _cleaning_lock_key(batch_id: str) -> str:
    return f"cleaning:lock:batch:{batch_id}"


def acquire_cleaning_lock(batch_id: str, task_id: str, *, ttl: int = 7200) -> bool:
    try:
        return bool(_redis_client().set(_cleaning_lock_key(batch_id), task_id, nx=True, ex=ttl))
    except Exception as e:
        logger.warning("Không tạo lock làm sạch %s: %s", batch_id, e)
        return True


def release_cleaning_lock(batch_id: str, task_id: str) -> None:
    try:
        client = _redis_client()
        key = _cleaning_lock_key(batch_id)
        if client.get(key) == task_id.encode():
            client.delete(key)
    except Exception as e:
        logger.warning("Không giải phóng lock làm sạch %s: %s", batch_id, e)


def request_cancel_cleaning(batch_id: str) -> None:
    _redis_set(_cleaning_cancel_key(batch_id), "1")


def is_cleaning_cancelled(batch_id: str) -> bool:
    return bool(_redis_get(_cleaning_cancel_key(batch_id)))


def clear_cleaning_cancel(batch_id: str) -> None:
    _redis_delete(_cleaning_cancel_key(batch_id))


def check_cleaning_cancelled(batch_id: str) -> None:
    if is_cleaning_cancelled(batch_id):
        raise TaskCancelled("Người dùng đã hủy làm sạch văn bản.")


def get_cleaning_task_id(batch_id: str) -> str | None:
    return _redis_get(_cleaning_lock_key(batch_id))


def cancel_cleaning_batch(batch_id: str) -> str:
    request_cancel_cleaning(batch_id)
    task_id = get_cleaning_task_id(batch_id)
    if task_id:
        revoke_celery_task(task_id)
        return f"Đã gửi yêu cầu hủy làm sạch (task {task_id[:8]}…)."
    return "Đã đặt cờ hủy làm sạch — task sẽ dừng ở bước kế tiếp."


def _chunking_cancel_key(batch_id: str) -> str:
    return f"chunking:cancel:batch:{batch_id}"


def _chunking_lock_key(batch_id: str) -> str:
    return f"chunking:lock:batch:{batch_id}"


def acquire_chunking_lock(batch_id: str, task_id: str, *, ttl: int = 7200) -> bool:
    try:
        return bool(_redis_client().set(_chunking_lock_key(batch_id), task_id, nx=True, ex=ttl))
    except Exception as e:
        logger.warning("Không tạo lock phân đoạn %s: %s", batch_id, e)
        return True


def release_chunking_lock(batch_id: str, task_id: str) -> None:
    try:
        client = _redis_client()
        key = _chunking_lock_key(batch_id)
        if client.get(key) == task_id.encode():
            client.delete(key)
    except Exception as e:
        logger.warning("Không giải phóng lock phân đoạn %s: %s", batch_id, e)


def request_cancel_chunking(batch_id: str) -> None:
    _redis_set(_chunking_cancel_key(batch_id), "1")


def is_chunking_cancelled(batch_id: str) -> bool:
    return bool(_redis_get(_chunking_cancel_key(batch_id)))


def clear_chunking_cancel(batch_id: str) -> None:
    _redis_delete(_chunking_cancel_key(batch_id))


def check_chunking_cancelled(batch_id: str) -> None:
    if is_chunking_cancelled(batch_id):
        raise TaskCancelled("Người dùng đã hủy phân đoạn.")


def get_chunking_task_id(batch_id: str) -> str | None:
    return _redis_get(_chunking_lock_key(batch_id))


def cancel_chunking_batch(batch_id: str) -> str:
    request_cancel_chunking(batch_id)
    task_id = get_chunking_task_id(batch_id)
    if task_id:
        revoke_celery_task(task_id)
        return f"Đã gửi yêu cầu hủy phân đoạn (task {task_id[:8]}…)."
    return "Đã đặt cờ hủy phân đoạn — task sẽ dừng ở bước kế tiếp."


def _ingest_cancel_key() -> str:
    return "ingest:cancel"


def _ingest_lock_key() -> str:
    return "ingest:lock"


def acquire_ingest_lock(task_id: str, *, ttl: int = 86400) -> bool:
    try:
        return bool(_redis_client().set(_ingest_lock_key(), task_id, nx=True, ex=ttl))
    except Exception as e:
        logger.warning("Không tạo lock nhập sách: %s", e)
        return True


def refresh_ingest_lock(task_id: str, *, ttl: int = 86400) -> None:
    try:
        client = _redis_client()
        key = _ingest_lock_key()
        current = client.get(key)
        if current == task_id.encode() or current == task_id:
            client.expire(key, ttl)
    except Exception as e:
        logger.warning("Không gia hạn lock nhập sách: %s", e)


def release_ingest_lock(task_id: str) -> None:
    try:
        client = _redis_client()
        key = _ingest_lock_key()
        if client.get(key) == task_id.encode():
            client.delete(key)
    except Exception as e:
        logger.warning("Không giải phóng lock nhập sách: %s", e)


def request_cancel_ingest() -> None:
    _redis_set(_ingest_cancel_key(), "1")


def is_ingest_cancelled() -> bool:
    return bool(_redis_get(_ingest_cancel_key()))


def clear_ingest_cancel() -> None:
    _redis_delete(_ingest_cancel_key())


def check_ingest_cancelled() -> None:
    if is_ingest_cancelled():
        raise TaskCancelled("Người dùng đã hủy nhập sách.")


def get_ingest_task_id() -> str | None:
    return _redis_get(_ingest_lock_key())


def cancel_ingest() -> str:
    request_cancel_ingest()
    task_id = get_ingest_task_id()
    if task_id:
        revoke_celery_task(task_id)
        return f"Đã gửi yêu cầu hủy nhập sách (task {task_id[:8]}…)."
    return "Đã đặt cờ hủy nhập sách — task sẽ dừng ở mục kế tiếp."


def _dedupe_lock_key() -> str:
    return "dedupe:lock"


def acquire_dedupe_lock(task_id: str, *, ttl: int = 7200) -> bool:
    try:
        return bool(_redis_client().set(_dedupe_lock_key(), task_id, nx=True, ex=ttl))
    except Exception as e:
        logger.warning("Không tạo lock kiểm tra trùng: %s", e)
        return True


def release_dedupe_lock(task_id: str) -> None:
    try:
        client = _redis_client()
        key = _dedupe_lock_key()
        if client.get(key) == task_id.encode():
            client.delete(key)
    except Exception as e:
        logger.warning("Không giải phóng lock kiểm tra trùng: %s", e)
