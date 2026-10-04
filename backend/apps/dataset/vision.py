"""OCR chữ trên hình minh họa (Google Vision) + mô tả bằng VLM."""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass

from apps.documents.models import Page

logger = logging.getLogger(__name__)

# Crop gần như chỉ có chữ (tiêu đề / chú thích bị YOLO gán nhầm thành hình).
_TEXT_ONLY_WORD_COVERAGE = 0.50
_TEXT_ONLY_HULL = 0.40
_TEXT_ONLY_BANNER_ASPECT = 1.8
_TEXT_ONLY_FEW_WORDS = 16
_TEXT_ONLY_HEADING_WORDS = 8
_TEXT_ONLY_HEADING_HULL = 0.55


def _ensure_credentials() -> None:
    from apps.llm.models import OCRBackend, OCRProvider

    provider = (
        OCRProvider.objects.filter(is_active=True, backend=OCRBackend.GOOGLE_VISION, is_default=True).first()
        or OCRProvider.objects.filter(is_active=True, backend=OCRBackend.GOOGLE_VISION).first()
    )
    if provider is None:
        return
    api_key, _base_url, _model = provider.resolve_llm_credentials()
    path = (api_key or "").strip()
    if path:
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", path)


@dataclass(frozen=True)
class IllustrationInspect:
    text: str
    word_count: int
    word_coverage: float
    hull_coverage: float
    width: float
    height: float
    is_text_only: bool


def _image_size(image_bytes: bytes) -> tuple[float, float]:
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
    return float(width or 0), float(height or 0)


def _is_text_only_crop(
    *,
    text: str,
    word_count: int,
    word_coverage: float,
    hull_coverage: float,
    width: float,
    height: float,
) -> bool:
    if not (text or "").strip() or word_count <= 0:
        return False
    if word_coverage >= _TEXT_ONLY_WORD_COVERAGE:
        return True
    aspect = width / max(height, 1.0)
    if word_count <= _TEXT_ONLY_FEW_WORDS and hull_coverage >= _TEXT_ONLY_HULL and aspect >= _TEXT_ONLY_BANNER_ASPECT:
        return True
    if word_count <= _TEXT_ONLY_HEADING_WORDS and hull_coverage >= _TEXT_ONLY_HEADING_HULL:
        return True
    return False


def inspect_illustration(image_bytes: bytes) -> IllustrationInspect:
    """OCR + ước lượng crop có phải khung chữ thuần."""
    from google.cloud import vision

    from ocr_llm.reading_order import text_from_gcv_response, words_from_gcv_page

    if not image_bytes:
        raise RuntimeError("Ảnh crop trống.")

    _ensure_credentials()
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_bytes)
    context = vision.ImageContext(language_hints=["vi", "zh", "zh-Hant", "en"])
    response = client.annotate_image(
        request={
            "image": image,
            "features": [vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)],
            "image_context": context,
        }
    )
    error = getattr(response, "error", None)
    message = (getattr(error, "message", None) or "").strip()
    if message:
        raise RuntimeError(message)

    text = (text_from_gcv_response(response) or "").strip()
    words = []
    width = 0.0
    height = 0.0
    annotation = getattr(response, "full_text_annotation", None)
    for page in getattr(annotation, "pages", None) or []:
        page_words, page_w, page_h = words_from_gcv_page(page)
        words.extend(page_words)
        width = max(width, float(page_w or 0))
        height = max(height, float(page_h or 0))
    if width <= 0 or height <= 0:
        width, height = _image_size(image_bytes)

    area = width * height
    word_coverage = 0.0
    hull_coverage = 0.0
    if words and area > 0:
        ink = sum(max(0.0, w.xmax - w.xmin) * max(0.0, w.ymax - w.ymin) for w in words)
        word_coverage = min(1.0, ink / area)
        xmin = min(w.xmin for w in words)
        ymin = min(w.ymin for w in words)
        xmax = max(w.xmax for w in words)
        ymax = max(w.ymax for w in words)
        hull_coverage = min(1.0, max(0.0, xmax - xmin) * max(0.0, ymax - ymin) / area)

    return IllustrationInspect(
        text=text,
        word_count=len(words),
        word_coverage=word_coverage,
        hull_coverage=hull_coverage,
        width=width,
        height=height,
        is_text_only=_is_text_only_crop(
            text=text,
            word_count=len(words),
            word_coverage=word_coverage,
            hull_coverage=hull_coverage,
            width=width,
            height=height,
        ),
    )


def extract_illustration_text(image_bytes: bytes) -> str:
    """OCR chữ trên ảnh crop bằng Google Cloud Vision."""
    return inspect_illustration(image_bytes).text


def annotate_illustration(
    image_bytes: bytes,
    *,
    page: Page | None = None,
    inspect: IllustrationInspect | None = None,
) -> str | None:
    """Trả markdown ## Chữ trên hình + ## Mô tả. None nếu crop chỉ là chữ."""
    from apps.documents.markdown import page_display_text
    from apps.llm.image_describe_runner import describe_illustration

    inspected = inspect or inspect_illustration(image_bytes)
    if inspected.is_text_only:
        return None
    ocr_text = inspected.text
    page_text = page_display_text(page) if page is not None else ""
    description = describe_illustration(
        image_bytes,
        ocr_text=ocr_text,
        page_text=page_text,
    )
    lines = [
        "## Chữ trên hình",
        ocr_text or "(không có chữ)",
        "",
        "## Mô tả",
        (description or "").strip() or "(không đủ thông tin để mô tả)",
    ]
    return "\n".join(lines).strip() + "\n"
