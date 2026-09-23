from __future__ import annotations

import os

from django.conf import settings
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from .constants import DEFAULT_API_KEY_ENV, DEFAULT_MODELS
from .models import (
    BookClassifyConfig,
    BookDedupeConfig,
    BookIngestConfig,
    ChunkingConfig,
    ChunkResizeConfig,
    ImageDescribeConfig,
    LLMBackend,
    LLMProvider,
    OCRBackend,
    OCRProvider,
    PageOCRConfig,
    TextCleaningConfig,
    TextCleaningPrompt,
    VLM_BACKENDS,
)
from ocr_llm.prompts import (
    default_book_classify_prompt,
    default_book_dedupe_prompt,
    default_book_ingest_prompt,
    default_image_describe_page_prompt,
    default_image_describe_prompt,
    default_page_chunking_prompt,
    default_page_stitch_prompt,
    default_text_cleaning_prompt,
)


def _ensure_provider(
    *,
    name: str,
    backend: str,
    is_default: bool = False,
    is_active: bool = True,
    **extra,
) -> OCRProvider:
    obj, created = OCRProvider.objects.get_or_create(
        name=name,
        defaults={
            "backend": backend,
            "model": extra.get("model") or DEFAULT_MODELS.get(backend, ""),
            "base_url": extra.get("base_url", ""),
            "api_key_env": extra.get("api_key_env") or DEFAULT_API_KEY_ENV.get(backend, ""),
            "params": extra.get("params") or {},
            "is_active": is_active,
            "is_default": is_default,
        },
    )
    return obj


@receiver(post_migrate)
def ensure_default_ocr_setup(sender, **kwargs):
    if sender.name != "apps.llm":
        return

    vision = _ensure_provider(
        name="Google Cloud Vision",
        backend=OCRBackend.GOOGLE_VISION,
        is_default=True,
        is_active=True,
    )

    vlm = _ensure_provider(
        name="Ollama VLM (bảng/công thức)",
        backend=OCRBackend.VLM_OLLAMA,
        is_active=False,
        base_url=getattr(settings, "OLLAMA_BASE_URL", "http://172.17.193.2:11434"),
        model=getattr(settings, "OLLAMA_MODEL", "qwen2.5vl:32b"),
    )

    gemini = _ensure_provider(
        name="Gemini VLM",
        backend=OCRBackend.VLM_GEMINI,
        is_active=False,
    )

    _ensure_provider(
        name="Document AI · Enterprise OCR",
        backend=OCRBackend.GOOGLE_DOCUMENT_AI_OCR,
        is_active=False,
    )
    docai_layout = _ensure_provider(
        name="Document AI · Layout Parser + OCR",
        backend=OCRBackend.GOOGLE_DOCUMENT_AI_LAYOUT,
        is_active=False,
    )
    _ensure_provider(
        name="Document AI · Tự theo loại trang",
        backend=OCRBackend.GOOGLE_DOCUMENT_AI,
        is_active=False,
    )

    if not gemini.fallback_provider_id:
        gemini.fallback_provider = vlm
        gemini.save(update_fields=["fallback_provider", "updated_at"])
    if not vlm.fallback_provider_id:
        vlm.fallback_provider = docai_layout
        vlm.save(update_fields=["fallback_provider", "updated_at"])

    PageOCRConfig.objects.get_or_create(
        page_mode="standard",
        defaults={"provider": vision, "notes": "OCR cả trang bằng Google Cloud Vision"},
    )
    PageOCRConfig.objects.get_or_create(
        page_mode="layout",
        defaults={"provider": vlm, "notes": "OCR cả trang bằng VLM (có bảng/công thức)"},
    )


def _ensure_llm(
    *,
    name: str,
    backend: str,
    is_default: bool = False,
    is_active: bool = True,
    aliases: tuple[str, ...] = (),
    **extra,
) -> LLMProvider:
    obj = LLMProvider.objects.filter(name=name).first()
    if obj is None and aliases:
        obj = LLMProvider.objects.filter(name__in=aliases).first()
        if obj:
            obj.name = name
            obj.save(update_fields=["name", "updated_at"])
    if obj is None:
        obj = LLMProvider.objects.create(
            name=name,
            backend=backend,
            model=extra.get("model") or DEFAULT_MODELS.get(backend, ""),
            base_url=extra.get("base_url", ""),
            api_key_env=extra.get("api_key_env") or DEFAULT_API_KEY_ENV.get(backend, ""),
            params=extra.get("params") or {},
            is_active=is_active,
            is_default=is_default,
        )
    return obj


@receiver(post_migrate)
def ensure_default_llm_setup(sender, **kwargs):
    if sender.name != "apps.llm":
        return

    gemini_llm = _ensure_llm(
        name="Gemini",
        backend=LLMBackend.GEMINI,
        is_default=True,
        is_active=True,
        aliases=("Gemini (làm sạch văn bản)",),
    )
    _ensure_llm(
        name="ChatGPT",
        backend=LLMBackend.OPENAI,
        is_active=False,
        aliases=("ChatGPT (làm sạch văn bản)",),
    )
    ollama_llm = _ensure_llm(
        name="Ollama",
        backend=LLMBackend.OLLAMA,
        is_active=False,
        aliases=("Ollama (làm sạch văn bản)",),
        base_url=getattr(settings, "OLLAMA_BASE_URL", "http://172.17.193.2:11434"),
        model=getattr(settings, "OLLAMA_TEXT_MODEL", "qwen2.5:32b"),
    )
    _ensure_llm(
        name="DeepSeek",
        backend=LLMBackend.DEEPSEEK,
        is_active=False,
        aliases=("DeepSeek (làm sạch văn bản)",),
    )
    _ensure_llm(
        name="vLLM",
        backend=LLMBackend.VLLM,
        is_active=False,
        base_url=os.getenv("VLLM_BASE_URL", ""),
        model=os.getenv("VLLM_MODEL", ""),
    )

    prompt = default_text_cleaning_prompt()
    TextCleaningPrompt.objects.get_or_create(pk=1, defaults={"prompt": prompt})

    existing_prompt = TextCleaningPrompt.objects.filter(pk=1).values_list("prompt", flat=True).first()
    TextCleaningConfig.objects.get_or_create(
        pk=1,
        defaults={
            "provider": gemini_llm,
            "model": "",
            "prompt": existing_prompt or prompt,
        },
    )

    default_llm = LLMProvider.objects.filter(is_active=True, is_default=True).first() or gemini_llm
    BookClassifyConfig.objects.get_or_create(
        pk=1,
        defaults={
            "provider": default_llm,
            "model": "",
            "max_pages": 20,
            "prompt": default_book_classify_prompt(),
        },
    )
    ChunkingConfig.objects.get_or_create(
        pk=1,
        defaults={
            "provider": default_llm,
            "model": "",
            "window_pages": 2,
            "overlap_pages": 1,
            "prompt": default_page_chunking_prompt(),
            "stitch_prompt": default_page_stitch_prompt(),
        },
    )
    chunking = ChunkingConfig.objects.filter(pk=1).first()
    ChunkResizeConfig.objects.get_or_create(
        pk=1,
        defaults={
            "provider": getattr(chunking, "provider", None) or default_llm,
            "model": getattr(chunking, "model", "") or "",
            "max_tokens": 5000,
            "prompt": (getattr(chunking, "prompt", None) or "").strip() or default_page_chunking_prompt(),
        },
    )
    BookIngestConfig.objects.get_or_create(
        pk=1,
        defaults={
            "provider": default_llm,
            "model": "",
            "prompt": default_book_ingest_prompt(),
        },
    )
    BookDedupeConfig.objects.get_or_create(
        pk=1,
        defaults={
            "provider": default_llm,
            "model": "",
            "batch_size": 30,
            "prompt": default_book_dedupe_prompt(),
        },
    )
    default_vlm = (
        OCRProvider.objects.filter(is_active=True, backend__in=VLM_BACKENDS).order_by("name").first()
        or OCRProvider.objects.filter(name="Gemini VLM").first()
        or OCRProvider.objects.filter(backend__in=VLM_BACKENDS).order_by("name").first()
    )
    ImageDescribeConfig.objects.get_or_create(
        pk=1,
        defaults={
            "provider": default_vlm,
            "model": "",
            "prompt": default_image_describe_prompt(),
            "page_prompt": default_image_describe_page_prompt(),
            "min_description_chars": 40,
        },
    )

    gemini_vlm = OCRProvider.objects.filter(name="Gemini VLM").first()
    if gemini_vlm and not gemini_vlm.llm_id:
        gemini_vlm.llm = gemini_llm
        gemini_vlm.save(update_fields=["llm", "updated_at"])

    ollama_vlm = OCRProvider.objects.filter(name="Ollama VLM (bảng/công thức)").first()
    if ollama_vlm and not ollama_vlm.llm_id:
        ollama_vlm.llm = ollama_llm
        ollama_vlm.save(update_fields=["llm", "updated_at"])
