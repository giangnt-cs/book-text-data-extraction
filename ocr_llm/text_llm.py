"""LLM text-only (không ảnh) — dùng cho làm sạch văn bản OCR."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from ocr_llm.providers import _strip_code_fences, _strip_think_blocks


def _openai_v1_url(base_url: str, default: str) -> str:
    root = (base_url or default).rstrip("/")
    if not root:
        root = default.rstrip("/")
    if root.endswith("/v1"):
        return root
    return root + "/v1"


class TextLLMProvider:
    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        raise NotImplementedError


@dataclass
class OllamaTextConfig:
    host: str = "http://ollama:11434"
    model: str = "qwen2.5:32b"
    disable_thinking: bool = True
    temperature: float = 0.0
    request_timeout_s: int = 300
    max_retries: int = 2
    retry_backoff_s: float = 2.0


class OllamaTextLLM(TextLLMProvider):
    def __init__(self, cfg: OllamaTextConfig):
        self.cfg = cfg

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        import requests

        url = self.cfg.host.rstrip("/") + "/api/chat"
        options: dict = {"temperature": float(self.cfg.temperature)}
        if max_tokens:
            options["num_predict"] = int(max_tokens)
        payload = {
            "model": self.cfg.model,
            "stream": False,
            "think": not bool(self.cfg.disable_thinking),
            "options": options,
            "messages": [{"role": "user", "content": prompt}],
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
class OpenAICompatibleTextConfig:
    api_key: str
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    request_timeout_s: int = 180
    temperature: float = 0.0
    enable_thinking: bool = False
    send_thinking: bool = False


class OpenAICompatibleTextLLM(TextLLMProvider):
    def __init__(self, cfg: OpenAICompatibleTextConfig):
        self.cfg = cfg

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        from openai import OpenAI

        client = OpenAI(
            api_key=self.cfg.api_key or "EMPTY",
            base_url=_openai_v1_url(self.cfg.base_url, "https://api.openai.com/v1"),
            timeout=self.cfg.request_timeout_s,
        )
        kwargs: dict = {
            "model": self.cfg.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": float(self.cfg.temperature),
        }
        if max_tokens:
            kwargs["max_tokens"] = int(max_tokens)
        if self.cfg.send_thinking:
            thinking = bool(self.cfg.enable_thinking)
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": thinking},
            }
        resp = client.chat.completions.create(**kwargs)
        text = (resp.choices[0].message.content or "").strip()
        return _strip_code_fences(_strip_think_blocks(text))


@dataclass
class GeminiTextConfig:
    api_key: str
    model: str = "gemini-2.0-flash"


class GeminiTextLLM(TextLLMProvider):
    def __init__(self, cfg: GeminiTextConfig):
        self.cfg = cfg

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        import google.generativeai as genai

        genai.configure(api_key=self.cfg.api_key)
        model = genai.GenerativeModel(self.cfg.model)
        gen_cfg = {"max_output_tokens": int(max_tokens)} if max_tokens else None

        from ocr_llm.providers import (
            GeminiBlockedError,
            _GEMINI_OCR_RETRY_SUFFIX,
            _gemini_blocked_message,
            _gemini_extract_text,
            _gemini_finish_reason,
        )

        prompts = [prompt, prompt + _GEMINI_OCR_RETRY_SUFFIX]
        last_reason: int | None = None

        last_err: Exception | None = None
        for retry in range(3):
            try:
                for attempt, p in enumerate(prompts):
                    call_kw: dict = {"request_options": {"timeout": 120}}
                    if gen_cfg:
                        call_kw["generation_config"] = gen_cfg
                    resp = model.generate_content(p, **call_kw)
                    text = _gemini_extract_text(resp)
                    if text:
                        return _strip_code_fences(_strip_think_blocks(text))

                    last_reason = _gemini_finish_reason(resp)
                    if last_reason in {3, 4} and attempt < len(prompts) - 1:
                        continue

                raise GeminiBlockedError(_gemini_blocked_message(last_reason), finish_reason=last_reason)
            except GeminiBlockedError:
                raise
            except Exception as e:
                last_err = e
                message = str(e).lower()
                retryable = "429" in message or "timeout" in message or "timed out" in message
                if retryable and retry < 2:
                    time.sleep(2 * (2**retry))
                    continue
                raise
        if last_err:
            raise last_err
        raise RuntimeError("Gemini không trả lời.")


def build_text_llm_provider(
    backend: str,
    *,
    model: str = "",
    base_url: str = "",
    api_key: str | None = None,
    **kwargs,
) -> TextLLMProvider:
    backend = (backend or "").lower()
    if backend in {"llm_ollama", "ollama", "vlm_ollama"}:
        enable_thinking = kwargs.get("enable_thinking")
        if enable_thinking is None:
            disable_thinking = kwargs.get("disable_thinking", True)
        else:
            disable_thinking = not bool(enable_thinking)
        return OllamaTextLLM(
            OllamaTextConfig(
                host=base_url or os.getenv("OLLAMA_BASE_URL", "http://172.17.193.2:11434"),
                model=model or os.getenv("OLLAMA_TEXT_MODEL", os.getenv("OLLAMA_MODEL", "qwen2.5:32b")),
                disable_thinking=disable_thinking,
                temperature=float(kwargs.get("temperature") if kwargs.get("temperature") is not None else 0.0),
                request_timeout_s=int(kwargs.get("request_timeout_s") or 300),
            )
        )
    if backend in {"llm_gemini", "vlm_gemini"}:
        key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError("Thiếu GEMINI_API_KEY")
        return GeminiTextLLM(GeminiTextConfig(api_key=key, model=model or "gemini-2.0-flash"))
    if backend in {"llm_vllm", "vlm_vllm"}:
        enable_thinking = bool(kwargs.get("enable_thinking", False))
        return OpenAICompatibleTextLLM(
            OpenAICompatibleTextConfig(
                api_key=api_key or os.getenv("VLLM_API_KEY", "") or "EMPTY",
                model=model or os.getenv("VLLM_MODEL", ""),
                base_url=_openai_v1_url(
                    base_url or os.getenv("VLLM_BASE_URL", ""),
                    "http://localhost:8000/v1",
                ),
                request_timeout_s=int(kwargs.get("request_timeout_s") or 180),
                temperature=float(kwargs.get("temperature") if kwargs.get("temperature") is not None else 0.0),
                enable_thinking=enable_thinking,
                send_thinking=True,
            )
        )
    if backend in {"llm_openai", "vlm_openai"}:
        key = api_key or os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("Thiếu OPENAI_API_KEY")
        return OpenAICompatibleTextLLM(
            OpenAICompatibleTextConfig(api_key=key, model=model or "gpt-4o", base_url=base_url or "https://api.openai.com/v1")
        )
    if backend in {"llm_deepseek", "vlm_deepseek"}:
        key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            raise RuntimeError("Thiếu DEEPSEEK_API_KEY")
        return OpenAICompatibleTextLLM(
            OpenAICompatibleTextConfig(
                api_key=key,
                model=model or "deepseek-chat",
                base_url=base_url or "https://api.deepseek.com",
            )
        )
    raise ValueError(f"Backend text LLM không hỗ trợ: {backend}")
