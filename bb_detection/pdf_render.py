"""Render trang PDF → PNG (dùng chung cho detect, OCR, Admin)."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Tuple

import numpy as np
from pdf2image import convert_from_bytes, convert_from_path
from PIL import Image

from bb_detection.pdf_validate import InvalidPdfError, raise_if_invalid_pdf, validate_pdf

logger = logging.getLogger(__name__)

_raise_if_invalid_pdf = raise_if_invalid_pdf

__all__ = ["InvalidPdfError", "validate_pdf", "render_page_png", "render_all_pages", "trim_scan_margins"]


def trim_scan_margins(
    img: Image.Image,
    *,
    threshold: int = 190,
    min_coverage: float = 0.06,
    min_trim_ratio: float = 0.03,
) -> Image.Image:
    """
    Cắt nền tối / tay / bàn quét quanh trang sách (PDF chụp ảnh).

    Tìm vùng sáng (giấy trang) và crop — loại phần ngoài không thuộc sách.

    Cảnh báo: sách ố vàng / lề tối dễ bị cắt mất chữ — chỉ bật khi PDF chụp có nền bàn rõ.
    """
    gray = np.array(img.convert("L"))
    h, w = gray.shape
    if h < 32 or w < 32:
        return img

    bright = gray >= threshold
    row_cov = bright.mean(axis=1)
    col_cov = bright.mean(axis=0)

    row_idx = np.where(row_cov >= min_coverage)[0]
    col_idx = np.where(col_cov >= min_coverage)[0]
    if len(row_idx) == 0 or len(col_idx) == 0:
        return img

    top, bottom = int(row_idx[0]), int(row_idx[-1])
    left, right = int(col_idx[0]), int(col_idx[-1])

    crop_w = right - left + 1
    crop_h = bottom - top + 1
    if crop_w * crop_h >= (1.0 - min_trim_ratio) * w * h:
        return img

    pad = max(4, int(min(w, h) * 0.005))
    top = max(0, top - pad)
    left = max(0, left - pad)
    bottom = min(h - 1, bottom + pad)
    right = min(w - 1, right + pad)

    if right <= left or bottom <= top:
        return img

    trimmed = img.crop((left, top, right + 1, bottom + 1))
    if crop_w * crop_h < 0.9 * w * h:
        logger.info(
            "trim_scan: %sx%s → %sx%s",
            w,
            h,
            trimmed.width,
            trimmed.height,
        )
    return trimmed


def _finalize_image(img: Image.Image, *, trim_scan: bool) -> Tuple[bytes, int, int]:
    if trim_scan:
        img = trim_scan_margins(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), img.width, img.height


def render_page_png(
    *,
    page_number: int,
    dpi: int = 150,
    pdf_path: str | Path | None = None,
    pdf_bytes: bytes | None = None,
    use_cropbox: bool = True,
    trim_scan: bool = False,
) -> Tuple[bytes, int, int]:
    """
    Render một trang PDF (1-based) thành PNG bytes + (width, height).

    use_cropbox: cắt theo CropBox PDF nếu có.
    trim_scan: cắt nền quét (tắt mặc định — dễ cắt mất lề sách ố vàng).
    """
    if page_number < 1:
        raise ValueError("page_number phải >= 1")
    if not pdf_path and not pdf_bytes:
        raise ValueError("Cần pdf_path hoặc pdf_bytes")

    _raise_if_invalid_pdf(pdf_path=pdf_path, pdf_bytes=pdf_bytes)

    kwargs = {
        "dpi": dpi,
        "first_page": page_number,
        "last_page": page_number,
        "use_cropbox": use_cropbox,
    }

    if pdf_bytes is not None:
        images = convert_from_bytes(pdf_bytes, **kwargs)
    else:
        images = convert_from_path(str(pdf_path), **kwargs)

    if not images:
        raise RuntimeError(f"Không render được trang {page_number}")

    return _finalize_image(images[0], trim_scan=trim_scan)


def render_all_pages(
    pdf_path: str | Path,
    *,
    dpi: int = 150,
    use_cropbox: bool = True,
    trim_scan: bool = False,
) -> list[Image.Image]:
    """Render toàn bộ PDF → list PIL Image (dùng cho doclayout detect)."""
    _raise_if_invalid_pdf(pdf_path=pdf_path)
    try:
        images = convert_from_path(
            str(pdf_path),
            dpi=dpi,
            use_cropbox=use_cropbox,
        )
    except Exception as e:
        err_name = type(e).__name__
        if err_name == "PDFPageCountError" or "page count" in str(e).lower() or "xref" in str(e).lower():
            raise InvalidPdfError(
                "PDF hỏng hoặc không phải file PDF hợp lệ. "
                "Hãy mở file bằng phần mềm đọc PDF, chọn «Lưu thành PDF mới» rồi upload lại."
            ) from e
        raise
    if trim_scan:
        images = [trim_scan_margins(im) for im in images]
    return images
