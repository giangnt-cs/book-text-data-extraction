"""Client HTTP RAGFlow và helper nạp / cập nhật phân đoạn."""

from __future__ import annotations

import logging
import re
from typing import Any

import requests
from celery import current_app

from apps.dataset.models import RAGFlowConfig, RagflowSyncStatus, SourceChunk, SourceDocument

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


def get_ragflow_config() -> RAGFlowConfig:
    obj, _created = RAGFlowConfig.objects.get_or_create(pk=1)
    return obj


def ragflow_ready(config: RAGFlowConfig | None = None) -> tuple[bool, str]:
    config = config or get_ragflow_config()
    if not (config.base_url or "").strip():
        return False, "Chưa nhập Base URL RAGFlow."
    if not (config.api_key or "").strip():
        return False, "Chưa nhập API Key RAGFlow."
    if not (config.dataset_id or "").strip():
        return False, "Chưa nhập Dataset ID RAGFlow."
    return True, ""


def ragflow_document_name(title: str) -> str:
    name = re.sub(r"[/\\]+", " ", title or "").strip() or "van-ban"
    if not name.lower().endswith(".md"):
        name = f"{name}.md"
    return name


def format_ragflow_chunk_content(text: str, prefix: str = "Passage: ") -> str:
    body = text or ""
    prefix = prefix or ""
    if not prefix:
        return body
    stripped = body.lstrip()
    if stripped.lower().startswith(prefix.lower()):
        return body
    return f"{prefix}{body}"


def document_meta_fields(document: SourceDocument) -> dict[str, str]:
    fields = {
        "book_name": document.title or "",
        "book_id": (document.catalog_code or "").strip(),
    }
    for key, attr in _CLASS_META:
        term = getattr(document, attr, None)
        fields[key] = (term.code if term else "") or ""
    return fields


class RAGFlowClient:
    def __init__(self, config: RAGFlowConfig):
        self.base_url = (config.base_url or "").strip().rstrip("/")
        self.api_key = (config.api_key or "").strip()
        self.dataset_id = (config.dataset_id or "").strip()
        self.meta_timeout = max(1, int(config.meta_timeout or 30))
        self.chunk_timeout = max(1, int(config.chunk_timeout or 300))
        self.embed_prefix = config.embed_prefix if config.embed_prefix is not None else "Passage: "

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

    def add_chunk(self, document_id: str, content: str) -> str:
        data = self._request(
            "POST",
            f"/api/v1/datasets/{self.dataset_id}/documents/{document_id}/chunks",
            json_body={"content": content},
            timeout=self.chunk_timeout,
        )
        chunk = data.get("chunk") if isinstance(data, dict) else None
        if not isinstance(chunk, dict) or not chunk.get("id"):
            raise RAGFlowError("RAGFlow không trả chunk id khi Add chunk.")
        return str(chunk["id"])

    def update_chunk(self, document_id: str, chunk_id: str, content: str) -> None:
        self._request(
            "PATCH",
            f"/api/v1/datasets/{self.dataset_id}/documents/{document_id}/chunks/{chunk_id}",
            json_body={"content": content},
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
        .update(ragflow_status=RagflowSyncStatus.PROCESSING, ragflow_error="")
    )
    if not updated:
        return False, "Đang nạp RAGFlow."
    try:
        current_app.send_task(
            "apps.dataset.tasks.sync_source_to_ragflow",
            kwargs={"document_id": str(document.pk)},
            queue="cpu",
        )
    except Exception as exc:
        SourceDocument.objects.filter(pk=document.pk).update(
            ragflow_status=previous,
            ragflow_error=str(exc),
        )
        logger.exception("Không gửi được task nạp RAGFlow cho «%s»", document.title)
        return False, str(exc)
    return True, ""


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
