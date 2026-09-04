from __future__ import annotations

import base64
import os
import re
import time
from dataclasses import dataclass
from typing import Optional


class VisionLLMProvider:
    def generate(self, prompt: str, image_bytes: bytes) -> str:
        raise NotImplementedError


class OCRProviderBase:
    def ocr(self, image_bytes: bytes) -> str:
        raise NotImplementedError


def _strip_code_fences(s: str) -> str:
    t = (s or "").strip()
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 3:
            t = "```".join(parts[1:-1]).strip()
            lines = t.splitlines()
            if lines and lines[0].strip().lower() in {"markdown", "md", "text", "plain", "latex"}:
                t = "\n".join(lines[1:]).strip()
    return t.strip()


def _strip_think_blocks(s: str) -> str:
    t = s or ""
    marker = "think"
    if ("<" + marker) not in t.lower():
        return t.strip()
    pattern = r"<" + marker + r"[^>]*>[\s\S]*?</" + marker + r">"
    return re.sub(pattern, "", t, flags=re.IGNORECASE).strip()


class GeminiBlockedError(RuntimeError):
    """Gemini không trả text (RECITATION, SAFETY, …)."""

    def __init__(self, message: str, *, finish_reason: int | None = None):
        super().__init__(message)
        self.finish_reason = finish_reason


def _gemini_finish_reason(resp) -> int | None:
    try:
        for cand in getattr(resp, "candidates", None) or []:
            fr = getattr(cand, "finish_reason", None)
            if fr is None:
                continue
            name = getattr(fr, "name", None) or str(fr)
            if name.isdigit():
                return int(name)
            mapping = {
                "STOP": 1,
                "MAX_TOKENS": 2,
                "SAFETY": 3,
                "RECITATION": 4,
                "FINISH_REASON_RECITATION": 4,
                "FINISH_REASON_SAFETY": 3,
            }
            if name in mapping:
                return mapping[name]
            if "RECITATION" in name:
                return 4
            if "SAFETY" in name:
                return 3
    except Exception:
        pass
    return None


def _gemini_extract_text(resp) -> str:
    """Lấy text từ response — không dùng resp.text (dễ lỗi khi không có Part)."""
    chunks: list[str] = []
    for cand in getattr(resp, "candidates", None) or []:
        content = getattr(cand, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            t = getattr(part, "text", None)
            if t:
                chunks.append(t)
    if chunks:
        return "".join(chunks).strip()
    try:
        return (getattr(resp, "text", None) or "").strip()
    except Exception:
        return ""


_GEMINI_OCR_RETRY_SUFFIX = (
    "\n\n[OCR ảnh scan do người dùng upload — chỉ chép nguyên văn chữ nhìn thấy trên ảnh, "
    "không tóm tắt, không diễn giải, không tái bản từ trí nhớ.]"
)


def _gemini_blocked_message(finish_reason: int | None) -> str:
    if finish_reason == 4:
        return (
            "Gemini chặn OCR vì phát hiện nội dung có thể vi phạm bản quyền (RECITATION). "
            "Hệ thống sẽ thử Google Cloud Vision thay thế."
        )
    if finish_reason == 3:
        return "Gemini chặn OCR vì bộ lọc an toàn (SAFETY)."
    return "Gemini không trả về nội dung OCR."


@dataclass
class OllamaVisionConfig:
    host: str = "http://ollama:11434"
    model: str = "qwen2.5vl:32b"
    disable_thinking: bool = True
    temperature: float = 0.0
    request_timeout_s: int = 300
    max_retries: int = 2
    retry_backoff_s: float = 2.0


class OllamaVisionLLM(VisionLLMProvider):
    def __init__(self, cfg: OllamaVisionConfig):
        self.cfg = cfg

    def generate(self, prompt: str, image_bytes: bytes) -> str:
        import requests

        b64 = base64.b64encode(image_bytes).decode("utf-8")
        url = self.cfg.host.rstrip("/") + "/api/chat"
        payload = {
            "model": self.cfg.model,
            "stream": False,
            "think": not bool(self.cfg.disable_thinking),
            "options": {"temperature": float(self.cfg.temperature)},
            "messages": [{"role": "user", "content": prompt, "images": [b64]}],
        }
        timeout = (10, int(self.cfg.request_timeout_s))
        last_err: Exception | None = None
        for attempt in range(int(self.cfg.max_retries) + 1):
            try:
                r = requests.post(url, json=payload, timeout=timeout)
                r.raise_for_status()
                data = r.json()
                text = ((data.get("message") or {}).get("content")) or ""
                return _strip_code_fences(_strip_think_blocks(text))
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
                last_err = e
                if attempt < int(self.cfg.max_retries):
                    time.sleep(float(self.cfg.retry_backoff_s) * (2**attempt))
                else:
                    raise
        if last_err:
            raise last_err
        return ""


@dataclass
class OpenAICompatibleVisionConfig:
    api_key: str
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    request_timeout_s: int = 180
    temperature: float = 0.0
    enable_thinking: bool = False
    send_thinking: bool = False


class OpenAICompatibleVisionLLM(VisionLLMProvider):
    """ChatGPT, DeepSeek, vLLM (API tương thích OpenAI)."""

    def __init__(self, cfg: OpenAICompatibleVisionConfig):
        self.cfg = cfg

    def generate(self, prompt: str, image_bytes: bytes) -> str:
        from openai import OpenAI

        b64 = base64.b64encode(image_bytes).decode("utf-8")
        client = OpenAI(
            api_key=self.cfg.api_key or "EMPTY",
            base_url=self.cfg.base_url.rstrip("/"),
            timeout=self.cfg.request_timeout_s,
        )
        kwargs = {
            "model": self.cfg.model,
            "temperature": float(self.cfg.temperature),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ],
                }
            ],
        }
        if self.cfg.send_thinking:
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": bool(self.cfg.enable_thinking)},
            }
        resp = client.chat.completions.create(**kwargs)
        text = (resp.choices[0].message.content or "").strip()
        return _strip_code_fences(_strip_think_blocks(text))


@dataclass
class GeminiVisionConfig:
    api_key: str
    model: str = "gemini-2.0-flash"


class GeminiVisionLLM(VisionLLMProvider):
    def __init__(self, cfg: GeminiVisionConfig):
        self.cfg = cfg

    def generate(self, prompt: str, image_bytes: bytes) -> str:
        import google.generativeai as genai

        genai.configure(api_key=self.cfg.api_key)
        model = genai.GenerativeModel(self.cfg.model)

        prompts = [prompt, prompt + _GEMINI_OCR_RETRY_SUFFIX]
        last_reason: int | None = None

        for attempt, p in enumerate(prompts):
            resp = model.generate_content(
                [
                    p,
                    {"mime_type": "image/png", "data": image_bytes},
                ]
            )
            text = _gemini_extract_text(resp)
            if text:
                return _strip_code_fences(_strip_think_blocks(text))

            last_reason = _gemini_finish_reason(resp)
            if last_reason in {3, 4} and attempt < len(prompts) - 1:
                continue

        raise GeminiBlockedError(_gemini_blocked_message(last_reason), finish_reason=last_reason)


# Sách YHCT: tên Việt (Latin) + tên Hán phồn thể trên cùng trang.
OCR_LANGUAGE_HINTS = ("vi", "zh", "zh-Hant")


@dataclass
class GoogleVisionConfig:
    credentials_path: Optional[str] = None
    language_hints: tuple[str, ...] = OCR_LANGUAGE_HINTS


class GoogleVisionOCR(OCRProviderBase):
    def __init__(self, cfg: GoogleVisionConfig):
        self.cfg = cfg

    def ocr(self, image_bytes: bytes, exclude_rects=None, box_space_size=None) -> str:
        from google.cloud import vision

        if self.cfg.credentials_path:
            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", self.cfg.credentials_path)

        from ocr_llm.reading_order import text_from_gcv_response

        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_bytes)
        hints = list(self.cfg.language_hints or OCR_LANGUAGE_HINTS)
        context = vision.ImageContext(language_hints=hints)
        response = client.document_text_detection(image=image, image_context=context)
        if response.error.message:
            raise RuntimeError(response.error.message)
        return text_from_gcv_response(
            response, exclude_rects=exclude_rects, box_space_size=box_space_size
        )


@dataclass
class PaddleOCRConfig:
    lang: str = "vi"
    use_gpu: bool = False


class PaddleOCRProvider(OCRProviderBase):
    def __init__(self, cfg: PaddleOCRConfig):
        self.cfg = cfg
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from paddleocr import PaddleOCR

            self._engine = PaddleOCR(use_angle_cls=True, lang=self.cfg.lang, use_gpu=self.cfg.use_gpu, show_log=False)
        return self._engine

    def ocr(self, image_bytes: bytes, exclude_rects=None, box_space_size=None) -> str:
        import io

        import numpy as np
        from PIL import Image

        from ocr_llm.reading_order import reconstruct_text, words_from_paddle_result, words_in_box_space

        pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img = np.array(pil)
        result = self._get_engine().ocr(img, cls=True)
        words = words_from_paddle_result(result)
        if words:
            width, height = float(pil.size[0]), float(pil.size[1])
            words, width, height = words_in_box_space(words, width, height, box_space_size)
            return reconstruct_text(words, width, height, exclude_rects=exclude_rects)
        return ""


@dataclass
class MinerUConfig:
    base_url: str
    request_timeout_s: int = 300


class MinerUOCR(OCRProviderBase):
    def __init__(self, cfg: MinerUConfig):
        self.cfg = cfg

    def ocr(self, image_bytes: bytes) -> str:
        import requests

        if not self.cfg.base_url:
            raise RuntimeError("MinerU: thiếu base_url trong cấu hình provider.")
        url = self.cfg.base_url.rstrip("/") + "/ocr"
        r = requests.post(
            url,
            files={"file": ("crop.png", image_bytes, "image/png")},
            timeout=self.cfg.request_timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        return (data.get("text") or data.get("markdown") or "").strip()


def build_ocr_provider(
    backend: str,
    *,
    model: str = "",
    base_url: str = "",
    credentials_path: str | None = None,
    api_key: str | None = None,
    **kwargs,
):
    backend = (backend or "").lower()
    if backend in {
        "google_document_ai",
        "google_document_ai_ocr",
        "google_document_ai_layout",
    }:
        from ocr_llm.document_ai import DocumentAIConfig, DocumentAIOCR
        import json
        from pathlib import Path

        project_id = kwargs.get("project_id") or os.getenv("DOCUMENT_AI_PROJECT_ID", "")
        if not project_id and credentials_path and Path(credentials_path).is_file():
            project_id = json.loads(Path(credentials_path).read_text(encoding="utf-8")).get("project_id", "")
        return DocumentAIOCR(
            DocumentAIConfig(
                project_id=project_id,
                location=kwargs.get("location") or os.getenv("DOCUMENT_AI_LOCATION", "us"),
                ocr_processor_id=kwargs.get("ocr_processor_id") or os.getenv("DOCUMENT_AI_OCR_PROCESSOR_ID", ""),
                layout_processor_id=kwargs.get("layout_processor_id") or os.getenv("DOCUMENT_AI_LAYOUT_PROCESSOR_ID", ""),
                credentials_path=credentials_path,
            )
        )
    if backend == "google_vision":
        return GoogleVisionOCR(GoogleVisionConfig(credentials_path=credentials_path))
    if backend in {"ollama", "vlm_ollama"}:
        enable_thinking = kwargs.get("enable_thinking")
        if enable_thinking is None:
            disable_thinking = kwargs.get("disable_thinking", True)
        else:
            disable_thinking = not bool(enable_thinking)
        return OllamaVisionLLM(
            OllamaVisionConfig(
                host=base_url or os.getenv("OLLAMA_BASE_URL", "http://172.17.193.2:11434"),
                model=model or os.getenv("OLLAMA_MODEL", "qwen2.5vl:32b"),
                disable_thinking=disable_thinking,
                temperature=float(kwargs.get("temperature") if kwargs.get("temperature") is not None else 0.0),
                request_timeout_s=int(kwargs.get("request_timeout_s") or 300),
            )
        )
    if backend == "vlm_gemini":
        key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError("Thiếu GEMINI_API_KEY")
        return GeminiVisionLLM(GeminiVisionConfig(api_key=key, model=model or "gemini-2.0-flash"))
    if backend in {"vlm_vllm"}:
        root = (base_url or os.getenv("VLLM_BASE_URL", "") or "http://localhost:8000/v1").rstrip("/")
        if not root.endswith("/v1"):
            root += "/v1"
        return OpenAICompatibleVisionLLM(
            OpenAICompatibleVisionConfig(
                api_key=api_key or os.getenv("VLLM_API_KEY", "") or "EMPTY",
                model=model or os.getenv("VLLM_MODEL", ""),
                base_url=root,
                request_timeout_s=int(kwargs.get("request_timeout_s") or 180),
                temperature=float(kwargs.get("temperature") if kwargs.get("temperature") is not None else 0.0),
                enable_thinking=bool(kwargs.get("enable_thinking", False)),
                send_thinking=True,
            )
        )
    if backend == "vlm_openai":
        key = api_key or os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("Thiếu OPENAI_API_KEY")
        return OpenAICompatibleVisionLLM(
            OpenAICompatibleVisionConfig(api_key=key, model=model or "gpt-4o", base_url=base_url or "https://api.openai.com/v1")
        )
    if backend == "vlm_deepseek":
        key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            raise RuntimeError("Thiếu DEEPSEEK_API_KEY")
        return OpenAICompatibleVisionLLM(
            OpenAICompatibleVisionConfig(
                api_key=key,
                model=model or "deepseek-chat",
                base_url=base_url or "https://api.deepseek.com",
            )
        )
    if backend == "paddle_ocr":
        return PaddleOCRProvider(
            PaddleOCRConfig(lang=kwargs.get("lang", "vi"), use_gpu=bool(kwargs.get("use_gpu", False)))
        )
    if backend == "mineru":
        return MinerUOCR(MinerUConfig(base_url=base_url or kwargs.get("endpoint_url", "")))
    raise ValueError(f"Backend OCR không hỗ trợ: {backend}")
