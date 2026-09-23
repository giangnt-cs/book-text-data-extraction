"""Client HTTP RAGFlow và helper nạp / cập nhật phân đoạn."""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from typing import Any

import requests
from celery import current_app

from apps.dataset.models import (
    ChunkContentType,
    ImageChunk,
    ImageSourceDocument,
    RAGFlowConfig,
    RagflowMatchStatus,
    RagflowSyncStatus,
    SourceChunk,
    SourceDocument,
)

logger = logging.getLogger(__name__)

_CLASS_META = (
    ("source", "class_source"),
    ("field", "class_field"),
    ("language", "class_language"),
    ("doc_type", "class_doc_type"),
    ("era", "class_era"),
)


class RAGFlowError(Exception):
    """Lỗi gọi API RAGFlow (không retry Add chunk)."""


@dataclass(frozen=True)
class RagflowChunkRef:
    chunk_id: str
    image_id: str = ""


def parse_ragflow_chunk_ref(data: Any) -> RagflowChunkRef:
    row = data
    if isinstance(data, dict) and isinstance(data.get("chunk"), dict):
        row = data["chunk"]
    if not isinstance(row, dict):
        raise RAGFlowError("RAGFlow không trả chunk id khi Add chunk.")
    chunk_id = str(row.get("id") or "").strip()
    if not chunk_id:
        raise RAGFlowError("RAGFlow không trả chunk id khi Add chunk.")
    image_id = str(row.get("image_id") or row.get("img_id") or "").strip()
    return RagflowChunkRef(chunk_id=chunk_id, image_id=image_id)


def citation_image_url(image_id: str) -> str:
    """URL công khai xem ảnh trang sách theo image_id RAGFlow."""
    from django.conf import settings
    from django.urls import reverse

    image_id = (image_id or "").strip()
    if not image_id:
        return ""
    path = reverse("ragflow_page_image", kwargs={"image_id": image_id})
    base = (getattr(settings, "PUBLIC_BASE_URL", None) or "").strip().rstrip("/")
    return f"{base}{path}" if base else path


def ragflow_document_missing(exc: BaseException) -> bool:
    """Document ID local trỏ tới bản ghi RAGFlow đã xóa / không thuộc dataset."""
    message = str(exc).casefold()
    return any(
        needle in message
        for needle in (
            "doesn't own the document",
            "does not own the document",
            "document not found",
            "doesn't exist",
            "does not exist",
        )
    )


def forget_ragflow_binding(document: SourceDocument) -> None:
    """Xóa document/chunk ID RAGFlow local để lần nạp sau tạo document mới."""
    from django.utils import timezone

    SourceChunk.objects.filter(document=document).update(
        ragflow_chunk_id="",
        ragflow_image_id="",
        updated_at=timezone.now(),
    )
    SourceDocument.objects.filter(pk=document.pk).update(
        ragflow_document_id="",
        ragflow_error="",
        ragflow_synced_at=None,
        ragflow_match_status="",
        ragflow_match_detail="",
        updated_at=timezone.now(),
    )
    document.ragflow_document_id = ""
    document.ragflow_error = ""
    document.ragflow_synced_at = None
    document.ragflow_match_status = ""
    document.ragflow_match_detail = ""


def get_ragflow_config() -> RAGFlowConfig:
    obj, _created = RAGFlowConfig.objects.get_or_create(pk=1)
    return obj


def ragflow_connection_ready(config: RAGFlowConfig | None = None) -> tuple[bool, str]:
    config = config or get_ragflow_config()
    if not (config.base_url or "").strip():
        return False, "Chưa nhập Base URL RAGFlow."
    if not (config.api_key or "").strip():
        return False, "Chưa nhập API Key RAGFlow."
    return True, ""


def ragflow_ready(config: RAGFlowConfig | None = None) -> tuple[bool, str]:
    config = config or get_ragflow_config()
    ok, detail = ragflow_connection_ready(config)
    if not ok:
        return False, detail
    if not (config.dataset_id or "").strip():
        return False, "Chưa nhập Dataset ID RAGFlow."
    return True, ""


def image_ragflow_ready(config: RAGFlowConfig | None = None) -> tuple[bool, str]:
    config = config or get_ragflow_config()
    ok, detail = ragflow_connection_ready(config)
    if not ok:
        return False, detail
    if not (config.image_dataset_id or "").strip():
        return False, "Chưa nhập Dataset ID hình trên RAGFlow."
    return True, ""


def ragflow_document_name(title: str) -> str:
    name = re.sub(r"[/\\]+", " ", title or "").strip() or "van-ban"
    if not name.lower().endswith(".md"):
        name = f"{name}.md"
    return name


def _publication_meta(document: SourceDocument, attr: str) -> str:
    value = (getattr(document, attr, None) or "").strip()
    if value:
        return value
    book = getattr(document, "book", None)
    if book is not None:
        return (getattr(book, attr, None) or "").strip()
    return ""


def document_content_type(document: SourceDocument) -> str:
    types = {item for item in document.chunks.values_list("content_type", flat=True) if item}
    if types == {ChunkContentType.IMAGE}:
        return ChunkContentType.IMAGE
    return ChunkContentType.TEXT


def _render_book_page_png(book, page_number: int, *, pdf_bytes: bytes) -> bytes:
    from apps.layout.models import DetectorConfig
    from bb_detection.pdf_render import render_page_png

    det = (
        DetectorConfig.objects.filter(is_active=True, is_default=True).first()
        or DetectorConfig.objects.filter(is_active=True).order_by("id").first()
    )
    png, _width, _height = render_page_png(
        pdf_bytes=pdf_bytes,
        page_number=page_number,
        dpi=int(getattr(det, "dpi", None) or 150),
        use_cropbox=bool(getattr(det, "use_cropbox", True)),
        trim_scan=bool(getattr(det, "trim_scan", False)),
    )
    return png


class PageImageBase64Cache:
    """Đọc PNG trang sách (storage, không thì render PDF) và cache Base64 theo trang."""

    def __init__(self) -> None:
        self._encoded: dict[tuple[str, int], str] = {}
        self._pdf_bytes: dict[str, bytes] = {}

    def png_for_chunk(self, chunk: SourceChunk) -> bytes:
        document = chunk.document
        book = getattr(document, "book", None)
        if book is None or not getattr(document, "book_id", None):
            return b""
        page_no = int(chunk.page_number or 0)
        if page_no < 1:
            return b""
        try:
            return self._load_png(book, page_no)
        except Exception:
            logger.warning(
                "Không lấy được ảnh trang %s của «%s»",
                page_no,
                getattr(book, "title", book.pk),
                exc_info=True,
            )
            return b""

    def for_chunk(self, chunk: SourceChunk) -> str:
        document = chunk.document
        book = getattr(document, "book", None)
        if book is None or not getattr(document, "book_id", None):
            return ""
        page_no = int(chunk.page_number or 0)
        if page_no < 1:
            return ""
        key = (str(book.pk), page_no)
        if key in self._encoded:
            return self._encoded[key]
        raw = self.png_for_chunk(chunk)
        encoded = base64.b64encode(raw).decode("ascii") if raw else ""
        self._encoded[key] = encoded
        return encoded

    def _load_png(self, book, page_number: int) -> bytes:
        from apps.documents.models import Page

        page = (
            Page.objects.filter(book_id=book.pk, page_number=page_number)
            .only("id", "page_image")
            .first()
        )
        if page and page.page_image:
            with page.page_image.open("rb") as fh:
                data = fh.read()
            if data:
                return data
        return self._png_from_pdf(book, page_number)

    def _png_from_pdf(self, book, page_number: int) -> bytes:
        book_id = str(book.pk)
        if book_id not in self._pdf_bytes:
            if not book.pdf_file:
                self._pdf_bytes[book_id] = b""
            else:
                with book.pdf_file.open("rb") as fh:
                    self._pdf_bytes[book_id] = fh.read()
        pdf_bytes = self._pdf_bytes[book_id]
        if not pdf_bytes:
            return b""
        return _render_book_page_png(book, page_number, pdf_bytes=pdf_bytes)


def document_meta_fields(document: SourceDocument) -> dict[str, str]:
    fields = {
        "book_name": document.title or "",
        "book_id": (document.catalog_code or "").strip(),
        "author": _publication_meta(document, "author"),
        "publisher": _publication_meta(document, "publisher"),
        "published_year": _publication_meta(document, "published_year"),
        "content_type": document_content_type(document),
    }
    for key, attr in _CLASS_META:
        term = getattr(document, attr, None)
        fields[key] = (term.code if term else "") or ""
    return fields


class RAGFlowClient:
    def __init__(self, config: RAGFlowConfig, *, dataset_id: str | None = None):
        self.base_url = (config.base_url or "").strip().rstrip("/")
        self.api_key = (config.api_key or "").strip()
        self.dataset_id = (dataset_id if dataset_id is not None else config.dataset_id or "").strip()
        self.meta_timeout = max(1, int(config.meta_timeout or 30))
        self.chunk_timeout = max(1, int(config.chunk_timeout or 300))

    def _headers(self, *, json_body: bool = True) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: int,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(json_body=json_body is not None),
                json=json_body,
                params=params,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise RAGFlowError(f"Hết thời gian chờ RAGFlow ({timeout}s).") from exc
        except requests.RequestException as exc:
            raise RAGFlowError(f"Không kết nối được RAGFlow: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise RAGFlowError(f"RAGFlow trả về không phải JSON (HTTP {response.status_code}).") from exc

        if response.status_code >= 400 and not isinstance(payload, dict):
            raise RAGFlowError(f"RAGFlow HTTP {response.status_code}.")

        if isinstance(payload, dict) and payload.get("code", 0) not in (0, None):
            message = payload.get("message") or payload.get("msg") or str(payload)
            raise RAGFlowError(str(message))
        if response.status_code >= 400:
            message = payload.get("message") if isinstance(payload, dict) else response.text
            raise RAGFlowError(f"RAGFlow HTTP {response.status_code}: {message}")
        return payload.get("data") if isinstance(payload, dict) else payload

    def create_empty_document(self, name: str) -> str:
        data = self._request(
            "POST",
            f"/api/v1/datasets/{self.dataset_id}/documents",
            params={"type": "empty"},
            json_body={"name": name},
            timeout=self.meta_timeout,
        )
        row = data[0] if isinstance(data, list) and data else data
        if not isinstance(row, dict) or not row.get("id"):
            raise RAGFlowError("RAGFlow không trả document id khi tạo document rỗng.")
        return str(row["id"])

    def update_document(self, document_id: str, *, meta_fields: dict, chunk_method: str = "manual") -> None:
        self._request(
            "PATCH",
            f"/api/v1/datasets/{self.dataset_id}/documents/{document_id}",
            json_body={"meta_fields": meta_fields, "chunk_method": chunk_method},
            timeout=self.meta_timeout,
        )

    def add_chunk(self, document_id: str, content: str, *, image_base64: str = "") -> RagflowChunkRef:
        body: dict[str, Any] = {"content": content}
        if image_base64:
            body["image_base64"] = image_base64
        data = self._request(
            "POST",
            f"/api/v1/datasets/{self.dataset_id}/documents/{document_id}/chunks",
            json_body=body,
            timeout=self.chunk_timeout,
        )
        ref = parse_ragflow_chunk_ref(data)
        if image_base64 and not ref.image_id:
            try:
                fetched = self.get_chunk(document_id, ref.chunk_id)
            except RAGFlowError:
                return ref
            if fetched.image_id:
                return fetched
        return ref

    def get_chunk(self, document_id: str, chunk_id: str) -> RagflowChunkRef:
        data = self._request(
            "GET",
            f"/api/v1/datasets/{self.dataset_id}/documents/{document_id}/chunks/{chunk_id}",
            timeout=self.meta_timeout,
        )
        return parse_ragflow_chunk_ref(data)

    def update_chunk(
        self,
        document_id: str,
        chunk_id: str,
        content: str,
        *,
        image_base64: str = "",
    ) -> str:
        body: dict[str, Any] = {"content": content}
        if image_base64:
            body["image_base64"] = image_base64
        data = self._request(
            "PATCH",
            f"/api/v1/datasets/{self.dataset_id}/documents/{document_id}/chunks/{chunk_id}",
            json_body=body,
            timeout=self.chunk_timeout,
        )
        try:
            return parse_ragflow_chunk_ref(data).image_id
        except RAGFlowError:
            if image_base64:
                return self.get_chunk(document_id, chunk_id).image_id
            return ""

    def get_document(self, document_id: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            f"/api/v1/datasets/{self.dataset_id}/documents",
            params={"id": document_id, "page": 1, "page_size": 1},
            timeout=self.meta_timeout,
        )
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = data.get("docs") or data.get("documents") or []
            if not rows and str(data.get("id") or "").strip() == document_id:
                return data
        else:
            rows = []
        if not isinstance(rows, list) or not rows:
            raise RAGFlowError("Document ID không tồn tại trên RAGFlow.")
        row = rows[0]
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            raise RAGFlowError("RAGFlow không trả document khi kiểm tra.")
        return row

    def list_chunk_ids(self, document_id: str) -> tuple[str, set[str]]:
        """Lấy ID document từ payload + toàn bộ chunk ID (phân trang)."""
        page = 1
        page_size = 100
        ids: set[str] = set()
        remote_doc_id = ""
        while True:
            data = self._request(
                "GET",
                f"/api/v1/datasets/{self.dataset_id}/documents/{document_id}/chunks",
                params={"page": page, "page_size": page_size},
                timeout=self.chunk_timeout,
            )
            payload = data if isinstance(data, dict) else {}
            doc = payload.get("doc") if isinstance(payload.get("doc"), dict) else {}
            if doc.get("id"):
                remote_doc_id = str(doc["id"]).strip()
            chunks = payload.get("chunks") if isinstance(payload.get("chunks"), list) else []
            total = int(payload.get("total") or 0)
            for row in chunks:
                if not isinstance(row, dict):
                    continue
                chunk_id = str(row.get("id") or "").strip()
                if chunk_id:
                    ids.add(chunk_id)
            if not chunks:
                break
            if total and len(ids) >= total:
                break
            if len(chunks) < page_size:
                break
            page += 1
        return remote_doc_id, ids

    def delete_all_chunks(self, document_id: str) -> None:
        doc_id = (document_id or "").strip()
        if not doc_id:
            raise RAGFlowError("Thiếu document id khi xóa chunk RAGFlow.")
        self._request(
            "DELETE",
            f"/api/v1/datasets/{self.dataset_id}/documents/{doc_id}/chunks",
            json_body={"delete_all": True},
            timeout=self.chunk_timeout,
        )


def can_enqueue_ragflow_sync(document: SourceDocument) -> tuple[bool, str]:
    if not (document.catalog_code or "").strip():
        return False, "Chưa có mã văn bản."
    if (document.ragflow_status or "") == RagflowSyncStatus.PROCESSING:
        return False, "Đang nạp RAGFlow."
    if not document.chunks.exists():
        return False, "Chưa có phân đoạn."
    return ragflow_ready()


def enqueue_ragflow_sync(document: SourceDocument) -> tuple[bool, str]:
    ok, detail = can_enqueue_ragflow_sync(document)
    if not ok:
        return False, detail
    previous = document.ragflow_status or ""
    updated = (
        SourceDocument.objects.filter(pk=document.pk)
        .exclude(ragflow_status=RagflowSyncStatus.PROCESSING)
        .update(
            ragflow_status=RagflowSyncStatus.PROCESSING,
            ragflow_error="",
            ragflow_match_status="",
            ragflow_match_detail="",
        )
    )
    if not updated:
        return False, "Đang nạp RAGFlow."
    try:
        from apps.documents.task_control import set_ragflow_task_id

        async_result = current_app.send_task(
            "apps.dataset.tasks.sync_source_to_ragflow",
            kwargs={"document_id": str(document.pk)},
            queue="cpu",
        )
        set_ragflow_task_id(str(document.pk), async_result.id)
    except Exception as exc:
        SourceDocument.objects.filter(pk=document.pk).update(
            ragflow_status=previous,
            ragflow_error=str(exc),
        )
        logger.exception("Không gửi được task nạp RAGFlow cho «%s»", document.title)
        return False, str(exc)
    return True, ""


def cancel_ragflow_sync(document: SourceDocument) -> tuple[bool, str]:
    from apps.documents.task_control import cancel_ragflow_sync_task

    if (document.ragflow_status or "") != RagflowSyncStatus.PROCESSING:
        return False, "Không đang nạp RAGFlow."
    detail = cancel_ragflow_sync_task(str(document.pk))
    SourceDocument.objects.filter(pk=document.pk, ragflow_status=RagflowSyncStatus.PROCESSING).update(
        ragflow_status=RagflowSyncStatus.FAILED,
        ragflow_error="Người dùng đã ngưng nạp RAGFlow.",
    )
    document.ragflow_status = RagflowSyncStatus.FAILED
    document.ragflow_error = "Người dùng đã ngưng nạp RAGFlow."
    return True, detail


def invalidate_ragflow_chunks(document: SourceDocument) -> None:
    """Xóa chunk trên RAGFlow và reset trạng thái nạp sau khi tách lại đoạn local."""
    from django.db.utils import OperationalError, ProgrammingError

    ragflow_doc_id = (getattr(document, "ragflow_document_id", None) or "").strip()
    keep_doc_id = ragflow_doc_id
    if ragflow_doc_id:
        try:
            config = get_ragflow_config()
            ok, detail = ragflow_ready(config)
            if not ok:
                raise RAGFlowError(detail)
            RAGFlowClient(config).delete_all_chunks(ragflow_doc_id)
        except Exception as exc:
            logger.warning(
                "Không xóa được chunk RAGFlow của «%s»: %s — lần nạp sau sẽ tạo document mới",
                document.title,
                exc,
            )
            keep_doc_id = ""

    try:
        SourceDocument.objects.filter(pk=document.pk).update(
            ragflow_document_id=keep_doc_id,
            ragflow_status="",
            ragflow_error="",
            ragflow_synced_at=None,
            ragflow_match_status="",
            ragflow_match_detail="",
        )
    except (OperationalError, ProgrammingError):
        return
    document.ragflow_document_id = keep_doc_id
    document.ragflow_status = ""
    document.ragflow_error = ""
    document.ragflow_synced_at = None
    document.ragflow_match_status = ""
    document.ragflow_match_detail = ""


def forget_image_ragflow_binding(document: ImageSourceDocument) -> None:
    from django.utils import timezone

    ImageChunk.objects.filter(document=document).update(
        ragflow_chunk_id="",
        ragflow_image_id="",
        updated_at=timezone.now(),
    )
    ImageSourceDocument.objects.filter(pk=document.pk).update(
        ragflow_document_id="",
        ragflow_error="",
        ragflow_synced_at=None,
        ragflow_match_status="",
        ragflow_match_detail="",
        updated_at=timezone.now(),
    )
    document.ragflow_document_id = ""
    document.ragflow_error = ""
    document.ragflow_synced_at = None
    document.ragflow_match_status = ""
    document.ragflow_match_detail = ""


def image_chunk_png(chunk: ImageChunk) -> bytes:
    if not chunk.image:
        return b""
    try:
        with chunk.image.open("rb") as fh:
            return fh.read() or b""
    except Exception:
        logger.warning("Không đọc được ảnh crop đoạn %s", chunk.pk, exc_info=True)
        return b""


def image_chunk_base64(chunk: ImageChunk) -> str:
    raw = image_chunk_png(chunk)
    return base64.b64encode(raw).decode("ascii") if raw else ""


def image_document_meta_fields(document: ImageSourceDocument) -> dict[str, str]:
    fields = {
        "book_name": document.title or "",
        "book_id": (document.catalog_code or "").strip(),
        "author": _publication_meta(document, "author"),
        "publisher": _publication_meta(document, "publisher"),
        "published_year": _publication_meta(document, "published_year"),
        "content_type": ChunkContentType.IMAGE,
    }
    for key, attr in _CLASS_META:
        term = getattr(document, attr, None)
        fields[key] = (term.code if term else "") or ""
    return fields


def can_enqueue_image_ragflow_sync(document: ImageSourceDocument) -> tuple[bool, str]:
    if not (document.catalog_code or "").strip():
        return False, "Chưa có mã văn bản."
    if (document.ragflow_status or "") == RagflowSyncStatus.PROCESSING:
        return False, "Đang nạp RAGFlow."
    if not document.chunks.exists():
        return False, "Chưa có phân đoạn hình."
    return image_ragflow_ready()


def enqueue_image_ragflow_sync(document: ImageSourceDocument) -> tuple[bool, str]:
    ok, detail = can_enqueue_image_ragflow_sync(document)
    if not ok:
        return False, detail
    previous = document.ragflow_status or ""
    updated = (
        ImageSourceDocument.objects.filter(pk=document.pk)
        .exclude(ragflow_status=RagflowSyncStatus.PROCESSING)
        .update(
            ragflow_status=RagflowSyncStatus.PROCESSING,
            ragflow_error="",
            ragflow_match_status="",
            ragflow_match_detail="",
        )
    )
    if not updated:
        return False, "Đang nạp RAGFlow."
    try:
        from apps.documents.task_control import set_ragflow_task_id

        async_result = current_app.send_task(
            "apps.dataset.tasks.sync_image_source_to_ragflow",
            kwargs={"document_id": str(document.pk)},
            queue="cpu",
        )
        set_ragflow_task_id(str(document.pk), async_result.id)
    except Exception as exc:
        ImageSourceDocument.objects.filter(pk=document.pk).update(
            ragflow_status=previous,
            ragflow_error=str(exc),
        )
        logger.exception("Không gửi được task nạp RAGFlow hình cho «%s»", document.title)
        return False, str(exc)
    return True, ""


def cancel_image_ragflow_sync(document: ImageSourceDocument) -> tuple[bool, str]:
    from apps.documents.task_control import cancel_ragflow_sync_task

    if (document.ragflow_status or "") != RagflowSyncStatus.PROCESSING:
        return False, "Không đang nạp RAGFlow."
    detail = cancel_ragflow_sync_task(str(document.pk))
    ImageSourceDocument.objects.filter(pk=document.pk, ragflow_status=RagflowSyncStatus.PROCESSING).update(
        ragflow_status=RagflowSyncStatus.FAILED,
        ragflow_error="Người dùng đã ngưng nạp RAGFlow.",
    )
    document.ragflow_status = RagflowSyncStatus.FAILED
    document.ragflow_error = "Người dùng đã ngưng nạp RAGFlow."
    return True, detail


def enqueue_image_ragflow_chunk_update(chunk: ImageChunk) -> bool:
    document = chunk.document
    if not (chunk.ragflow_chunk_id or "").strip():
        return False
    if not (document.ragflow_document_id or "").strip():
        return False
    ok, _detail = image_ragflow_ready()
    if not ok:
        return False
    try:
        current_app.send_task(
            "apps.dataset.tasks.update_image_ragflow_chunk",
            kwargs={"chunk_id": str(chunk.pk)},
            queue="cpu",
        )
    except Exception:
        logger.exception("Không gửi được task cập nhật RAGFlow hình cho đoạn %s", chunk.pk)
        return False
    return True


def enqueue_ragflow_chunk_update(chunk: SourceChunk) -> bool:
    document = chunk.document
    if not (chunk.ragflow_chunk_id or "").strip():
        return False
    if not (document.ragflow_document_id or "").strip():
        return False
    ok, _detail = ragflow_ready()
    if not ok:
        return False
    try:
        current_app.send_task(
            "apps.dataset.tasks.update_ragflow_chunk",
            kwargs={"chunk_id": str(chunk.pk)},
            queue="cpu",
        )
    except Exception:
        logger.exception("Không gửi được task cập nhật RAGFlow cho đoạn %s", chunk.pk)
        return False
    return True


def _persist_ragflow_match(document, status: str, detail: str) -> None:
    from django.utils import timezone

    model = document.__class__
    if isinstance(document, ImageSourceDocument) or model.__name__ in {"ImageSourceDocument", "ImageChunkedDocument"}:
        model = ImageSourceDocument
    elif isinstance(document, SourceDocument) or model.__name__ in {"SourceDocument", "ChunkedDocument"}:
        model = SourceDocument
    model.objects.filter(pk=document.pk).update(
        ragflow_match_status=status,
        ragflow_match_detail=detail,
        updated_at=timezone.now(),
    )
    document.ragflow_match_status = status
    document.ragflow_match_detail = detail


def _compare_ragflow_ids(*, local_doc_id: str, local_chunk_ids: list[str], client: RAGFlowClient) -> tuple[str, str]:
    if not local_chunk_ids:
        return RagflowMatchStatus.MISMATCHED, "Chưa có phân đoạn."
    if not local_doc_id:
        return RagflowMatchStatus.MISMATCHED, "Chưa có document ID trên RAGFlow."
    if any(not (chunk_id or "").strip() for chunk_id in local_chunk_ids):
        missing = sum(1 for chunk_id in local_chunk_ids if not (chunk_id or "").strip())
        return RagflowMatchStatus.MISMATCHED, f"{missing} phân đoạn chưa có chunk ID."
    try:
        remote_doc = client.get_document(local_doc_id)
    except RAGFlowError as exc:
        if ragflow_document_missing(exc):
            return RagflowMatchStatus.MISMATCHED, "Document ID không tồn tại trên RAGFlow."
        return RagflowMatchStatus.MISMATCHED, str(exc)
    remote_doc_id = str(remote_doc.get("id") or "").strip()
    if remote_doc_id and remote_doc_id != local_doc_id:
        return RagflowMatchStatus.MISMATCHED, "Document ID local không khớp RAGFlow."
    try:
        listed_doc_id, remote_chunk_ids = client.list_chunk_ids(local_doc_id)
    except RAGFlowError as exc:
        if ragflow_document_missing(exc):
            return RagflowMatchStatus.MISMATCHED, "Document ID không tồn tại trên RAGFlow."
        return RagflowMatchStatus.MISMATCHED, str(exc)
    if listed_doc_id and listed_doc_id != local_doc_id:
        return RagflowMatchStatus.MISMATCHED, "Document ID trong danh sách chunk không khớp."
    local_set = {chunk_id.strip() for chunk_id in local_chunk_ids}
    if local_set != remote_chunk_ids:
        only_local = local_set - remote_chunk_ids
        only_remote = remote_chunk_ids - local_set
        parts = [f"local {len(local_set)} chunk, RAGFlow {len(remote_chunk_ids)} chunk"]
        if only_local:
            parts.append(f"thiếu trên RAGFlow: {len(only_local)}")
        if only_remote:
            parts.append(f"thừa trên RAGFlow: {len(only_remote)}")
        return RagflowMatchStatus.MISMATCHED, "; ".join(parts)
    return RagflowMatchStatus.MATCHED, ""


def verify_text_ragflow_match(document: SourceDocument) -> tuple[str, str]:
    config = get_ragflow_config()
    ok, detail = ragflow_ready(config)
    if not ok:
        _persist_ragflow_match(document, RagflowMatchStatus.MISMATCHED, detail)
        return RagflowMatchStatus.MISMATCHED, detail
    local_ids = list(document.chunks.order_by("position").values_list("ragflow_chunk_id", flat=True))
    status, message = _compare_ragflow_ids(
        local_doc_id=(document.ragflow_document_id or "").strip(),
        local_chunk_ids=local_ids,
        client=RAGFlowClient(config),
    )
    _persist_ragflow_match(document, status, message)
    return status, message


def verify_image_ragflow_match(document: ImageSourceDocument) -> tuple[str, str]:
    config = get_ragflow_config()
    ok, detail = image_ragflow_ready(config)
    if not ok:
        _persist_ragflow_match(document, RagflowMatchStatus.MISMATCHED, detail)
        return RagflowMatchStatus.MISMATCHED, detail
    local_ids = list(document.chunks.order_by("position").values_list("ragflow_chunk_id", flat=True))
    status, message = _compare_ragflow_ids(
        local_doc_id=(document.ragflow_document_id or "").strip(),
        local_chunk_ids=local_ids,
        client=RAGFlowClient(config, dataset_id=config.image_dataset_id),
    )
    _persist_ragflow_match(document, status, message)
    return status, message
