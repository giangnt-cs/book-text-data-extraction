#!/usr/bin/env python3
"""
Chạy thử Google Cloud Vision OCR trên một ảnh (độc lập, không qua Django).

Chuẩn bị:
  1. Tạo Service Account trên Google Cloud Console
  2. Bật API "Cloud Vision API"
  3. Tải file JSON key (service account key)
  4. export GOOGLE_APPLICATION_CREDENTIALS=/đường/dẫn/tới/file.json

Cài thư viện:
  pip install google-cloud-vision

Chạy:
  python scripts/test_google_vision.py path/to/image.png
  python scripts/test_google_vision.py path/to/image.png --out result.txt
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def ocr_image(image_path: Path) -> str:
    from google.cloud import vision

    creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds:
        raise SystemExit(
            "Thiếu biến GOOGLE_APPLICATION_CREDENTIALS.\n"
            "Ví dụ: export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json"
        )
    if not Path(creds).is_file():
        raise SystemExit(f"Không tìm thấy file credentials: {creds}")

    content = image_path.read_bytes()
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=content)
    response = client.document_text_detection(image=image)

    if response.error.message:
        raise RuntimeError(response.error.message)

    if response.full_text_annotation and response.full_text_annotation.text:
        return response.full_text_annotation.text.strip()
    if response.text_annotations:
        return response.text_annotations[0].description.strip()
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Thử OCR bằng Google Cloud Vision")
    parser.add_argument("image", type=Path, help="Đường dẫn ảnh PNG/JPG")
    parser.add_argument("--out", type=Path, help="Ghi kết quả ra file text (tùy chọn)")
    args = parser.parse_args()

    if not args.image.is_file():
        raise SystemExit(f"Không tìm thấy ảnh: {args.image}")

    print(f"Ảnh: {args.image}")
    print(f"Credentials: {os.getenv('GOOGLE_APPLICATION_CREDENTIALS')}")
    print("Đang gọi Google Cloud Vision API...\n")

    text = ocr_image(args.image)

    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"Đã ghi kết quả vào: {args.out}")

    print("=" * 60)
    print(text or "(Không nhận dạng được text)")
    print("=" * 60)


if __name__ == "__main__":
    main()
