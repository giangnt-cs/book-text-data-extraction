"""VLM mô tả hình minh họa khi rút trích ảnh sách."""

from __future__ import annotations

import logging
import re

from apps.llm.models import OCRProvider, VLM_BACKENDS, ImageDescribeConfig
from apps.llm.ocr_runner import get_ocr_client
from ocr_llm.prompts import (
    build_image_describe_page_prompt,
    build_image_describe_prompt,
    default_image_describe_page_prompt,
    default_image_describe_prompt,
)
from ocr_llm.text_llm import build_text_llm_provider

logger = logging.getLogger(__name__)

_INSUFFICIENT_MARKERS = (
    "[không_đủ_thông_tin]",
    "không đủ thông tin",
    "không thể mô tả",
    "không mô tả được",
    "không nhìn thấy",
    "insufficient",
    "cannot describe",
    "can't describe",
    "i don't know",
)


def get_image_describe_config() -> ImageDescribeConfig:
    obj, created = ImageDescribeConfig.objects.get_or_create(
        pk=1,
        defaults={
            "prompt": default_image_describe_prompt(),
            "page_prompt": default_image_describe_page_prompt(),
            "min_description_chars": 40,
        },
    )
    fields: list[str] = []
    if not (obj.prompt or "").strip():
        obj.prompt = default_image_describe_prompt()
        fields.append("prompt")
    if not (obj.page_prompt or "").strip():
        obj.page_prompt = default_image_describe_page_prompt()
        fields.append("page_prompt")
    if int(obj.min_description_chars or 0) < 1:
        obj.min_description_chars = 40
        fields.append("min_description_chars")
    if fields:
        obj.save(update_fields=[*fields, "updated_at"])
    return obj


def resolve_image_describe_vlm(config: ImageDescribeConfig | None = None) -> OCRProvider:
    config = config or get_image_describe_config()
    provider = config.provider
    if provider is not None:
        if provider.backend not in VLM_BACKENDS:
            raise RuntimeError(
                f"«{provider.name}» không phải công cụ VLM. "
                "Vào Admin → LLM → Mô tả hình minh họa và chọn Gemini / Ollama / vLLM / ChatGPT."
            )
        if not provider.is_active:
            raise RuntimeError(
                f"VLM «{provider.name}» chưa kích hoạt. "
                "Bật công cụ đó ở Admin → OCR → Công cụ OCR."
            )
        return provider
    fallback = (
        OCRProvider.objects.filter(is_active=True, backend__in=VLM_BACKENDS)
        .order_by("name")
        .first()
    )
    if fallback is None:
        raise RuntimeError(
            "Chưa cấu hình VLM mô tả hình. Vào Admin → LLM → Mô tả hình minh họa "
            "và chọn một công cụ VLM đang kích hoạt."
        )
    return fallback


def get_image_describe_text_client(provider: OCRProvider, *, model_override: str = ""):
    api_key, base_url, model = provider.resolve_llm_credentials()
    if (model_override or "").strip():
        model = model_override.strip()
    params = dict(provider.params or {})
    if provider.llm_id and provider.llm:
        params.update(provider.llm.generation_kwargs())
    return build_text_llm_provider(
        provider.backend,
        model=model,
        base_url=base_url,
        api_key=api_key,
        **params,
    )


def description_is_insufficient(text: str, *, min_chars: int = 40) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    compact = re.sub(r"\s+", " ", cleaned)
    lower = compact.casefold()
    if any(marker in lower for marker in _INSUFFICIENT_MARKERS):
        return True
    return len(compact) < max(1, int(min_chars or 40))


def describe_illustration(
    image_bytes: bytes,
    *,
    ocr_text: str,
    page_text: str = "",
    config: ImageDescribeConfig | None = None,
) -> str:
    """Lần 1: chỉ chữ OCR (không ảnh). Lần 2: ảnh + nội dung trang nếu chưa đủ."""
    if not image_bytes:
        raise RuntimeError("Ảnh crop trống.")
    config = config or get_image_describe_config()
    provider = resolve_image_describe_vlm(config)
    min_chars = int(config.min_description_chars or 40)
    ocr = (ocr_text or "").strip()

    first = ""
    if ocr:
        text_client = get_image_describe_text_client(provider, model_override=config.model or "")
        first_prompt = build_image_describe_prompt(
            template=config.prompt or default_image_describe_prompt(),
            ocr_text=ocr,
        )
        first = (text_client.generate(first_prompt) or "").strip()
        if not description_is_insufficient(first, min_chars=min_chars):
            return first
    else:
        logger.info("Hình không có chữ OCR — bỏ qua lần mô tả 1, dùng nội dung trang.")

    page = (page_text or "").strip()
    if not page:
        logger.info("VLM mô tả hình chưa đủ thông tin và không có nội dung trang — giữ lần 1.")
        return first or "(không đủ thông tin để mô tả)"

    if len(page) > 8000:
        page = page[:8000].rstrip() + "\n…"

    vision_client = get_ocr_client(provider, model_override=config.model or "")
    if not hasattr(vision_client, "generate"):
        raise RuntimeError(f"Công cụ «{provider.name}» không hỗ trợ mô tả hình (VLM).")
    second_prompt = build_image_describe_page_prompt(
        template=config.page_prompt or default_image_describe_page_prompt(),
        ocr_text=ocr,
        page_text=page,
    )
    second = (vision_client.generate(second_prompt, image_bytes) or "").strip()
    if second:
        return second
    return first or "(không đủ thông tin để mô tả)"
