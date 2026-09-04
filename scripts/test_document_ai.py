#!/usr/bin/env python3
"""Kiểm tra Google Document AI: Enterprise OCR và Layout Parser."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ocr_llm.document_ai import (  # noqa: E402
    DocumentAIConfig,
    ensure_processors,
    list_processors,
    process_image,
    process_page_pdf,
)


def _project_id_from_credentials(creds_path: Path) -> str:
    data = json.loads(creds_path.read_text(encoding="utf-8"))
    return data["project_id"]


def _render_page_png(pdf_path: Path, page_index: int, dpi: int) -> bytes:
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_index)
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes("png")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Document AI OCR + Layout Parser")
    parser.add_argument("pdf", type=Path, help="File PDF mẫu")
    parser.add_argument("--pages", type=str, default="1,6", help="Trang test, vd: 1,6,14")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--location", default=os.getenv("DOCUMENT_AI_LOCATION", "us"))
    parser.add_argument("--project", default=os.getenv("DOCUMENT_AI_PROJECT_ID", ""))
    parser.add_argument("--out-dir", type=Path, default=Path("samples/docai_test"))
    args = parser.parse_args()

    creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds or not Path(creds).is_file():
        sys.exit("Thiếu GOOGLE_APPLICATION_CREDENTIALS trỏ tới file JSON hợp lệ")

    creds_path = Path(creds)
    project_id = args.project or _project_id_from_credentials(creds_path)

    cfg = DocumentAIConfig(
        project_id=project_id,
        location=args.location,
        ocr_processor_id=os.getenv("DOCUMENT_AI_OCR_PROCESSOR_ID", ""),
        layout_processor_id=os.getenv("DOCUMENT_AI_LAYOUT_PROCESSOR_ID", ""),
        credentials_path=str(creds_path),
    )

    print(f"Project: {project_id}  Location: {args.location}")
    print(f"Credentials: {creds_path}")

    try:
        procs = list_processors(cfg)
        print(f"\nProcessors hiện có ({len(procs)}):")
        for p in procs:
            print(f"  - {p.display_name!r}  type={p.type_}  id={p.name.split('/')[-1]}  state={p.state}")
    except Exception as e:
        sys.exit(f"\n[LỖI] Không liệt kê được processor: {e}\n"
                 f"Bật API tại: https://console.cloud.google.com/apis/library/documentai.googleapis.com?project={project_id}")

    try:
        ocr_id, layout_id = ensure_processors(cfg)
        print(f"\nDùng OCR processor: {ocr_id}")
        print(f"Dùng Layout processor: {layout_id}")
    except Exception as e:
        sys.exit(f"\n[LỖI] Không tạo/tìm được processor: {e}")

    page_numbers = [int(x.strip()) for x in args.pages.split(",") if x.strip()]
    args.out_dir.mkdir(parents=True, exist_ok=True)

    results: list[str] = [
        f"# Document AI test – {args.pdf.name}",
        f"Project: {project_id}",
        f"OCR processor: {ocr_id}",
        f"Layout processor: {layout_id}",
        "",
    ]

    pdf_bytes = args.pdf.read_bytes()

    for page_no in page_numbers:
        print(f"\nTrang {page_no}...")
        png = _render_page_png(args.pdf, page_no - 1, args.dpi)

        for mode, label in [("standard", "Enterprise OCR"), ("layout", "Layout Parser")]:
            t0 = time.perf_counter()
            try:
                if mode == "layout":
                    text = process_page_pdf(cfg, pdf_bytes, page_index=page_no - 1, mode="layout")
                else:
                    text = process_image(cfg, png, mode="standard")
                status = "OK"
            except Exception as e:
                text = f"[LỖI: {e}]"
                status = "FAIL"
            elapsed = round(time.perf_counter() - t0, 2)
            print(f"  {label}: {status} ({elapsed}s, {len(text)} chars)")

            results.append(f"## Trang {page_no} – {label}")
            results.append(f"Trạng thái: {status} · {elapsed}s · {len(text)} ký tự")
            results.append("")
            results.append(text[:4000] + ("..." if len(text) > 4000 else ""))
            results.append("")

    out_file = args.out_dir / f"{args.pdf.stem}_docai_test.md"
    out_file.write_text("\n".join(results).strip() + "\n", encoding="utf-8")
    print(f"\nKết quả: {out_file}")


if __name__ == "__main__":
    main()
