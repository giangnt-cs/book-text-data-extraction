"""Chạy OCR cả trang theo loại trang (standard / layout)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from apps.documents.models import Book, PageOcrMode
from apps.llm.models import OCRBackend, OCRProvider, PageOCRConfig, VLM_BACKENDS, DOCUMENT_AI_BACKENDS
from ocr_llm.prompts import prompt_for_full_page

logger = logging.getLogger(__name__)

_client_cache: dict[str, Any] = {}


def get_fallback_provider() -> OCRProvider:
    p = OCRProvider.objects.filter(is_active=True, is_default=True).first()
    if p:
        return p
    p = OCRProvider.objects.filter(is_active=True, backend=OCRBackend.GOOGLE_VISION).first()
    if p:
        return p
    raise RuntimeError("Chưa có công cụ OCR mặc định. Vào Admin → OCR → Công cụ OCR.")


def load_global_page_ocr_providers() -> dict[str, OCRProvider]:
    """Cấu hình OCR hệ thống (Admin → OCR theo loại trang)."""
    fallback = get_fallback_provider()
    mapping = {
        PageOcrMode.STANDARD: fallback,
        PageOcrMode.LAYOUT: fallback,
    }
    for cfg in PageOCRConfig.objects.select_related("provider").all():
        provider = cfg.provider if cfg.provider.is_active else fallback
        if not provider.is_active:
            provider = fallback
        mapping[cfg.page_mode] = provider
    return mapping


def _resolve_provider(override: OCRProvider | None, default: OCRProvider, fallback: OCRProvider) -> OCRProvider:
    if override and override.is_active:
        return override
    return default if default.is_active else fallback


def load_page_ocr_providers(book: Book | None = None) -> dict[str, OCRProvider]:
    """
    Cấu hình OCR theo loại trang.
    Sách có thể ghi đè từng loại trang; phần để trống dùng cấu hình hệ thống.
    """
    global_mapping = load_global_page_ocr_providers()
    fallback = get_fallback_provider()

    if not book:
        return global_mapping

    standard = _resolve_provider(
        book.ocr_standard_provider,
        global_mapping[PageOcrMode.STANDARD],
        fallback,
    )
    layout = _resolve_provider(
        book.ocr_layout_provider,
        global_mapping[PageOcrMode.LAYOUT],
        fallback,
    )
    return {
        PageOcrMode.STANDARD: standard,
        PageOcrMode.LAYOUT: layout,
    }


def describe_page_ocr_providers(book: Book | None = None) -> dict[str, str]:
    """Mô tả ngắn provider đang dùng — cho Admin."""
    mapping = load_page_ocr_providers(book)
    return {
        PageOcrMode.STANDARD: str(mapping[PageOcrMode.STANDARD]),
        PageOcrMode.LAYOUT: str(mapping[PageOcrMode.LAYOUT]),
    }


def _cache_key(provider: OCRProvider, *, model_override: str = "") -> str:
    api_key, base_url, model = provider.resolve_llm_credentials()
    if (model_override or "").strip():
        model = model_override.strip()
    gen = ""
    if provider.llm_id and provider.llm:
        kw = provider.llm.generation_kwargs()
        gen = f":{kw['temperature']}:{int(kw['enable_thinking'])}"
    return f"{provider.pk}:{provider.llm_id}:{model}:{base_url}:{bool(api_key)}{gen}"


def get_ocr_client(provider: OCRProvider, *, model_override: str = ""):
    from ocr_llm.providers import build_ocr_provider

    key = _cache_key(provider, model_override=model_override)
    if key in _client_cache:
        return _client_cache[key]

    api_key, base_url, model = provider.resolve_llm_credentials()
    if (model_override or "").strip():
        model = model_override.strip()
    params = dict(provider.params or {})
    if provider.llm_id and provider.llm:
        params.update(provider.llm.generation_kwargs())
    google_backends = {OCRBackend.GOOGLE_VISION, *DOCUMENT_AI_BACKENDS}
    client = build_ocr_provider(
        provider.backend,
        model=model,
        base_url=base_url,
        credentials_path=api_key if provider.backend in google_backends else None,
        api_key=api_key if provider.backend not in {
            *google_backends,
            OCRBackend.VLM_OLLAMA,
            OCRBackend.VLM_VLLM,
            OCRBackend.OLLAMA,
        } else None,
        **params,
    )
    _client_cache[key] = client
    return client


def _run_ocr_once(
    *,
    provider: OCRProvider,
    page_image_bytes: bytes,
    ocr_mode: str,
    lang: str,
    pdf_bytes: bytes | None,
    page_index: int | None,
    exclude_rects=None,
    box_space_size: tuple[float, float] | None = None,
) -> str:
    client = get_ocr_client(provider)
    backend = provider.backend

    if backend in {OCRBackend.GOOGLE_VISION, OCRBackend.PADDLE_OCR}:
        return client.ocr(
            page_image_bytes,
            exclude_rects=exclude_rects,
            box_space_size=box_space_size,
        ).strip()

    if backend == OCRBackend.MINERU:
        return client.ocr(page_image_bytes).strip()

    if backend in DOCUMENT_AI_BACKENDS:
        if hasattr(client, "set_source_pdf") and pdf_bytes is not None:
            client.set_source_pdf(pdf_bytes)
        if backend == OCRBackend.GOOGLE_DOCUMENT_AI_OCR:
            docai_mode = PageOcrMode.STANDARD
        elif backend == OCRBackend.GOOGLE_DOCUMENT_AI_LAYOUT:
            docai_mode = PageOcrMode.LAYOUT
        else:
            docai_mode = ocr_mode
        text, used_mode = client.ocr_page_with_fallback(
            page_image_bytes,
            mode=docai_mode,
            page_index=page_index,
            exclude_rects=exclude_rects,
            box_space_size=box_space_size,
        )
        text = text.strip()
        if docai_mode == PageOcrMode.LAYOUT and used_mode != "layout":
            logger.warning(
                "Trang bảng/công thức không dùng Layout Parser (mode=%s)",
                used_mode,
            )
        return text

    if backend in VLM_BACKENDS:
        prompt = prompt_for_full_page(lang=lang, has_table_or_formula=(ocr_mode == PageOcrMode.LAYOUT))
        if hasattr(client, "generate"):
            return client.generate(prompt, page_image_bytes).strip()
        raise RuntimeError(f"Backend {backend} không hỗ trợ OCR trang.")

    raise RuntimeError(f"Backend {backend} không hỗ trợ OCR trang.")


def _fallback_reason(exc: BaseException) -> str:
    from ocr_llm.providers import GeminiBlockedError

    if isinstance(exc, GeminiBlockedError):
        return f"bị chặn (finish_reason={getattr(exc, 'finish_reason', None)})"
    return f"lỗi: {exc}"


@dataclass(frozen=True)
class OcrPageResult:
    text: str
    provider: OCRProvider
    requested: OCRProvider

    @property
    def used_fallback(self) -> bool:
        return self.provider.pk != self.requested.pk


def ocr_full_page(
    *,
    provider: OCRProvider,
    page_image_bytes: bytes,
    ocr_mode: str,
    lang: str = "Việt",
    pdf_bytes: bytes | None = None,
    page_index: int | None = None,
    layout_boxes=None,
    page_width: float | None = None,
    page_height: float | None = None,
) -> OcrPageResult:
    """OCR toàn bộ ảnh trang. Theo chuỗi fallback đã cấu hình khi công cụ lỗi."""
    from ocr_llm.reading_order import header_footer_rects, layout_rect_from_obj

    exclude_rects = []
    box_space_size = None
    if page_width and page_height:
        box_space_size = (float(page_width), float(page_height))
        exclude_rects = header_footer_rects(
            [layout_rect_from_obj(b) for b in (layout_boxes or [])],
            float(page_width),
            float(page_height),
        )

    current = provider
    tried: set = set()

    while current is not None and current.pk not in tried:
        tried.add(current.pk)
        try:
            text = _run_ocr_once(
                provider=current,
                page_image_bytes=page_image_bytes,
                ocr_mode=ocr_mode,
                lang=lang,
                pdf_bytes=pdf_bytes,
                page_index=page_index,
                exclude_rects=exclude_rects,
                box_space_size=box_space_size,
            )
        except Exception as exc:
            nxt = current.next_active_fallback(tried)
            if nxt is None:
                raise
            logger.warning(
                "OCR «%s» %s — fallback «%s»",
                current.name,
                _fallback_reason(exc),
                nxt.name,
            )
            current = nxt
            continue

        if not text:
            nxt = current.next_active_fallback(tried)
            if nxt is not None:
                logger.warning(
                    "OCR «%s» trả về rỗng — fallback «%s»",
                    current.name,
                    nxt.name,
                )
                current = nxt
                continue

        if text and current.backend in VLM_BACKENDS:
            from ocr_llm.document_ai import postprocess_ocr_text

            text = postprocess_ocr_text(text)
        return OcrPageResult(text=text, provider=current, requested=provider)

    raise RuntimeError(f"Không OCR được trang bằng {provider.name}.")
