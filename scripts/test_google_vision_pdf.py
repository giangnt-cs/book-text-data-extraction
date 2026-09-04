#!/usr/bin/env python3
"""OCR N trang đầu của PDF bằng Google Cloud Vision (standalone)."""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
from pathlib import Path

import fitz
from google.cloud import vision


def ocr_png_bytes(client: vision.ImageAnnotatorClient, png_bytes: bytes) -> str:
    image = vision.Image(content=png_bytes)
    response = client.document_text_detection(image=image)
    if response.error.message:
        raise RuntimeError(response.error.message)
    if response.full_text_annotation and response.full_text_annotation.text:
        return response.full_text_annotation.text.strip()
    if response.text_annotations:
        return response.text_annotations[0].description.strip()
    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", type=int, default=50, help="Số trang đầu cần OCR")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds or not Path(creds).is_file():
        sys.exit("Thiếu GOOGLE_APPLICATION_CREDENTIALS trỏ tới file JSON hợp lệ")

    doc = fitz.open(args.pdf)
    total = min(args.pages, doc.page_count)
    zoom = args.dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)

    client = vision.ImageAnnotatorClient()
    lines: list[str] = [f"# OCR Google Vision – {args.pdf.name}", f"Số trang: {total}/{doc.page_count}", ""]

    t0 = time.perf_counter()
    for i in range(total):
        page_no = i + 1
        print(f"Trang {page_no}/{total}...", flush=True)
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
        try:
            text = ocr_png_bytes(client, png_bytes)
        except Exception as e:
            text = f"[LỖI: {e}]"
        lines.append(f"## Trang {page_no}")
        lines.append("")
        lines.append(text)
        lines.append("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    elapsed = round(time.perf_counter() - t0, 1)
    print(f"\nXong {total} trang trong {elapsed}s")
    print(f"Kết quả: {args.out}")


if __name__ == "__main__":
    main()
