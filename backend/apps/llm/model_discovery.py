"""Gọi API nhà cung cấp để lấy danh sách vision model."""

from __future__ import annotations

import logging
from typing import Callable

import requests

from .models import OCRBackend

logger = logging.getLogger(__name__)

_VISION_HINTS = (
    "vl",
    "vision",
    "llava",
    "moondream",
    "minicpm",
    "qwen2",
    "qwen3",
    "gemma",
    "bakllava",
    "cogvlm",
    "internvl",
    "pixtral",
    "gpt-4",
    "gpt-4o",
    "gpt-5",
    "o1",
    "o3",
    "o4",
    "claude",
    "gemini",
    "deepseek",
)


def _prefer_vision_models(names: list[str]) -> list[str]:
    cleaned = sorted({n.strip() for n in names if n and n.strip()})
    if not cleaned:
        return []
    vision = [n for n in cleaned if any(h in n.lower() for h in _VISION_HINTS)]
    return vision or cleaned


def _ollama_models(base_url: str) -> list[str]:
    url = (base_url or "http://localhost:11434").rstrip("/") + "/api/tags"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    names = [m.get("name", "") for m in data.get("models") or [] if isinstance(m, dict)]
    return _prefer_vision_models(names)


def _openai_v1_url(base_url: str, default: str) -> str:
    root = (base_url or default).rstrip("/")
    if not root:
        root = default.rstrip("/")
    if root.endswith("/v1"):
        return root
    return root + "/v1"


def _openai_compatible_models(base_url: str, api_key: str, *, default_url: str = "https://api.openai.com/v1") -> list[str]:
    root = _openai_v1_url(base_url, default_url)
    url = root + "/models"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    names = [m.get("id", "") for m in data.get("data") or [] if isinstance(m, dict)]
    return _prefer_vision_models(names)


def _gemini_models(api_key: str) -> list[str]:
    if not api_key:
        raise ValueError("Cần API Key để tải danh sách model.")
    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        names: list[str] = []
        for model in genai.list_models():
            methods = getattr(model, "supported_generation_methods", []) or []
            if "generateContent" not in methods:
                continue
            name = getattr(model, "name", "") or ""
            if name.startswith("models/"):
                name = name.split("/", 1)[1]
            if name:
                names.append(name)
        return _prefer_vision_models(names)
    except Exception:
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        resp = requests.get(url, params={"key": api_key}, timeout=20)
        resp.raise_for_status()
        names = []
        for item in resp.json().get("models") or []:
            name = item.get("name", "")
            if name.startswith("models/"):
                name = name.split("/", 1)[1]
            methods = item.get("supportedGenerationMethods") or []
            if "generateContent" in methods and name:
                names.append(name)
        return _prefer_vision_models(names)


_BACKENDS_WITHOUT_MODELS = {
    OCRBackend.GOOGLE_VISION,
    OCRBackend.GOOGLE_DOCUMENT_AI,
    OCRBackend.GOOGLE_DOCUMENT_AI_OCR,
    OCRBackend.GOOGLE_DOCUMENT_AI_LAYOUT,
    OCRBackend.PADDLE_OCR,
    OCRBackend.MINERU,
}


def list_vision_models(*, backend: str, base_url: str = "", api_key: str = "") -> list[str]:
    backend = (backend or "").lower()
    if backend in _BACKENDS_WITHOUT_MODELS:
        return []

    handlers: dict[str, Callable[[], list[str]]] = {
        OCRBackend.VLM_OLLAMA: lambda: _ollama_models(base_url),
        OCRBackend.OLLAMA: lambda: _ollama_models(base_url),
        OCRBackend.VLM_OPENAI: lambda: _openai_compatible_models(base_url, api_key),
        OCRBackend.VLM_DEEPSEEK: lambda: _openai_compatible_models(base_url, api_key),
        OCRBackend.VLM_VLLM: lambda: _openai_compatible_models(
            base_url, api_key or "EMPTY", default_url="http://localhost:8000/v1"
        ),
        OCRBackend.VLM_GEMINI: lambda: _gemini_models(api_key),
    }
    handler = handlers.get(backend)
    if not handler:
        raise ValueError(f"Loại công cụ không hỗ trợ tải model: {backend}")
    return handler()


def _ollama_all_models(base_url: str) -> list[str]:
    url = (base_url or "http://localhost:11434").rstrip("/") + "/api/tags"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    names = [m.get("name", "") for m in data.get("models") or [] if isinstance(m, dict)]
    return sorted({n.strip() for n in names if n and n.strip()})


def _openai_all_models(base_url: str, api_key: str, *, default_url: str = "https://api.openai.com/v1") -> list[str]:
    root = _openai_v1_url(base_url, default_url)
    url = root + "/models"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    names = [m.get("id", "") for m in data.get("data") or [] if isinstance(m, dict)]
    return sorted({n.strip() for n in names if n and n.strip()})


def _gemini_all_models(api_key: str) -> list[str]:
    if not api_key:
        raise ValueError("Cần API Key để tải danh sách model.")
    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        names: list[str] = []
        for model in genai.list_models():
            methods = getattr(model, "supported_generation_methods", []) or []
            if "generateContent" not in methods:
                continue
            name = getattr(model, "name", "") or ""
            if name.startswith("models/"):
                name = name.split("/", 1)[1]
            if name:
                names.append(name)
        return sorted({n.strip() for n in names if n and n.strip()})
    except Exception:
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        resp = requests.get(url, params={"key": api_key}, timeout=20)
        resp.raise_for_status()
        names = []
        for item in resp.json().get("models") or []:
            name = item.get("name", "")
            if name.startswith("models/"):
                name = name.split("/", 1)[1]
            methods = item.get("supportedGenerationMethods") or []
            if "generateContent" in methods and name:
                names.append(name)
        return sorted({n.strip() for n in names if n and n.strip()})


def list_text_models(*, backend: str, base_url: str = "", api_key: str = "") -> list[str]:
    """Danh sách model text-only của LLM đã khai báo."""
    backend = (backend or "").lower()
    if backend.startswith("llm_"):
        backend = "vlm_" + backend[4:]

    handlers: dict[str, Callable[[], list[str]]] = {
        "vlm_ollama": lambda: _ollama_all_models(base_url),
        "ollama": lambda: _ollama_all_models(base_url),
        "vlm_openai": lambda: _openai_all_models(base_url, api_key),
        "vlm_deepseek": lambda: _openai_all_models(base_url, api_key),
        "vlm_vllm": lambda: _openai_all_models(
            base_url, api_key or "EMPTY", default_url="http://localhost:8000/v1"
        ),
        "vlm_gemini": lambda: _gemini_all_models(api_key),
    }
    handler = handlers.get(backend)
    if not handler:
        raise ValueError(f"Loại LLM không hỗ trợ tải model: {backend}")
    return handler()
