"""Chạy làm sạch văn bản OCR bằng LLM."""

from __future__ import annotations

import logging
from typing import Any

from apps.llm.models import LLMProvider, TextCleaningConfig
from ocr_llm.prompts import build_page_cleaning_prompt, default_text_cleaning_prompt
from ocr_llm.text_llm import build_text_llm_provider

logger = logging.getLogger(__name__)

_client_cache: dict[str, Any] = {}


def get_cleaning_config() -> TextCleaningConfig:
    obj, _ = TextCleaningConfig.objects.get_or_create(
        pk=1,
        defaults={"prompt": default_text_cleaning_prompt()},
    )
    if not (obj.prompt or "").strip():
        obj.prompt = default_text_cleaning_prompt()
        obj.save(update_fields=["prompt", "updated_at"])
    return obj


def get_default_llm() -> LLMProvider:
    p = LLMProvider.objects.filter(is_active=True, is_default=True).first()
    if p:
        return p
    p = LLMProvider.objects.filter(is_active=True).order_by("id").first()
    if p:
        return p
    raise RuntimeError("Chưa có LLM. Vào Admin → LLM để khai báo.")


get_default_cleaning_provider = get_default_llm


def resolve_cleaning_provider(config: TextCleaningConfig | None = None) -> LLMProvider:
    config = config or get_cleaning_config()
    if config.provider_id and config.provider and config.provider.is_active:
        return config.provider
    return get_default_llm()


def save_cleaning_prompt(prompt: str) -> str:
    text = (prompt or "").strip()
    if not text:
        raise ValueError("Prompt trống.")
    config = get_cleaning_config()
    config.prompt = text
    config.save(update_fields=["prompt", "updated_at"])
    return text


def get_text_llm_client(provider: LLMProvider, model: str = ""):
    resolved = provider.resolve_model(model)
    gen = provider.generation_kwargs()
    key = (
        f"{provider.pk}:{resolved}:{(provider.base_url or '').strip()}:"
        f"{gen['temperature']}:{int(gen['enable_thinking'])}"
    )
    if key in _client_cache:
        return _client_cache[key]

    params = dict(provider.params or {})
    params.update(gen)
    client = build_text_llm_provider(
        provider.backend,
        model=resolved,
        base_url=provider.base_url,
        api_key=provider.resolve_api_key() if provider.backend not in {"llm_ollama", "llm_vllm"} else (
            provider.resolve_api_key() or None
        ),
        **params,
    )
    _client_cache[key] = client
    return client


def clean_page_text(
    *,
    provider: LLMProvider,
    prompt_template: str,
    book_title: str,
    page_number: int,
    ocr_text: str,
    model: str = "",
) -> str:
    client = get_text_llm_client(provider, model)
    prompt = build_page_cleaning_prompt(
        template=prompt_template,
        book_title=book_title,
        page_number=page_number,
        ocr_text=ocr_text,
    )
    return client.generate(prompt).strip()
