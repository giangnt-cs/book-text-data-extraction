"""Client embedding Ollama / vLLM — port từ knowledge-extraction/src/ollama_client.py."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Optional


class OllamaEmbedClient:
    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        model: str = "jina-embeddings-v4-text-retrieval-F16:latest",
        timeout: int = 120,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._use_legacy_embeddings = False

    def embed(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        if not texts:
            return []
        if self._use_legacy_embeddings:
            return [self._embed_legacy(text) for text in texts]
        embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            try:
                embeddings.extend(self._embed_batch(batch))
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 405) and i == 0:
                    self._use_legacy_embeddings = True
                    return [self._embed_legacy(text) for text in texts]
                raise
        return embeddings

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        data = self._post("/api/embed", {"model": self.model, "input": texts})
        vectors = data.get("embeddings")
        if not vectors:
            single = data.get("embedding")
            if single:
                vectors = [single]
        if not vectors or len(vectors) != len(texts):
            raise ValueError(
                f"Ollama /api/embed trả về {0 if not vectors else len(vectors)} "
                f"vector, kỳ vọng {len(texts)}"
            )
        return vectors

    def _embed_legacy(self, text: str) -> list[float]:
        data = self._post("/api/embeddings", {"model": self.model, "prompt": text})
        vector = data.get("embedding")
        if not vector:
            embeddings = data.get("embeddings")
            if embeddings:
                vector = embeddings[0]
        if not vector:
            raise ValueError("Ollama /api/embeddings không trả về embedding")
        return vector

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.host}{path}"
        body = json.dumps(payload).encode("utf-8")
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            request = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}, method="POST"
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 405):
                    raise
                last_error = exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < self.max_retries:
                time.sleep(self.retry_delay * attempt)
        raise RuntimeError(f"Lỗi khi gọi Ollama {url} sau {self.max_retries} lần: {last_error}")


class OpenAICompatEmbedClient:
    def __init__(
        self,
        host: str = "http://127.0.0.1:8000/v1",
        model: str = "",
        timeout: int = 300,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        api_key: Optional[str] = None,
    ):
        base = host.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        self.host = base
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.api_key = api_key

    def embed(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        if not texts:
            return []
        embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            embeddings.extend(self._embed_batch(texts[i : i + batch_size]))
        return embeddings

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": self.model, "input": [text if text else " " for text in texts]}
        data = self._post("/embeddings", payload)
        items = data.get("data")
        if not items:
            raise ValueError("vLLM /v1/embeddings không trả về data")
        items = sorted(items, key=lambda item: int(item.get("index", 0)))
        vectors = [item.get("embedding") for item in items]
        if not vectors or any(not vec for vec in vectors) or len(vectors) != len(texts):
            raise ValueError(
                f"vLLM /v1/embeddings trả về {0 if not vectors else len(vectors)} "
                f"vector, kỳ vọng {len(texts)}"
            )
        return vectors

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.host}{path}"
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if isinstance(exc, urllib.error.HTTPError):
                    try:
                        detail = exc.read().decode("utf-8", errors="replace")
                        if detail:
                            last_error = RuntimeError(f"HTTP {exc.code}: {detail[:500]}")
                    except Exception:
                        pass
            if attempt < self.max_retries:
                time.sleep(self.retry_delay * attempt)
        raise RuntimeError(f"Lỗi khi gọi vLLM {url} sau {self.max_retries} lần: {last_error}")


def create_embed_client(*, provider: str, host: str, model: str, api_key: str | None = None):
    name = (provider or "ollama").strip().lower()
    if name in {"vllm", "openai"}:
        return OpenAICompatEmbedClient(host=host, model=model, api_key=api_key or None)
    return OllamaEmbedClient(host=host, model=model)
