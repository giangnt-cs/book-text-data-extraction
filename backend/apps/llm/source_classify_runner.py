"""Phân loại văn bản nguồn Dataset bằng LLM từ các đoạn Markdown đầu."""

from __future__ import annotations

from typing import Any

from apps.dataset.catalog import assign_catalog_code
from apps.dataset.markdown import read_source_file, split_source_markdown
from apps.dataset.models import SourceDocument
from apps.documents.classification import BOOK_CLASS_FIELDS
from apps.llm.book_classify_runner import (
    MAX_FRONT_CHARS,
    _parse_classify_json,
    _term_for,
    classification_catalog_text,
    get_classify_config,
    resolve_classify_provider,
)
from apps.llm.text_cleaning_runner import get_text_llm_client
from ocr_llm.prompts import build_book_classify_prompt, default_book_classify_prompt


def collect_source_front_text(document: SourceDocument, *, max_pages: int = 20) -> str:
    segments = list(document.segments.order_by("page_number")[:max_pages])
    parts: list[str] = []
    for segment in segments:
        text = (segment.text_md or "").strip()
        if text:
            parts.append(f"--- Trang {segment.page_number} ---\n{text}")
    if parts:
        return "\n\n".join(parts)[:MAX_FRONT_CHARS]

    content = read_source_file(document)
    for number, body in split_source_markdown(content)[:max_pages]:
        text = (body or "").strip()
        if text:
            parts.append(f"--- Trang {number} ---\n{text}")
    return "\n\n".join(parts)[:MAX_FRONT_CHARS]


def apply_source_classify_result(document: SourceDocument, data: dict[str, Any]) -> list[str]:
    """Ghi metadata xuất bản và 5 nhóm phân loại, rồi gán mã DSG_fff."""
    filled: list[str] = []
    updates: list[str] = []
    mapping = (
        ("author", "author", "Tên tác giả"),
        ("publisher", "publisher", "Nhà xuất bản"),
        ("published_year", "published_year", "Năm xuất bản"),
    )
    for key, attr, label in mapping:
        value = str(data.get(key) or "").strip()
        if not value:
            continue
        if value != getattr(document, attr):
            setattr(document, attr, value[: 400 if attr != "published_year" else 32])
            updates.append(attr)
        filled.append(label)
    for field_name, slug in BOOK_CLASS_FIELDS:
        term = _term_for(slug, str(data.get(field_name) or ""))
        if term is None:
            continue
        if getattr(document, f"{field_name}_id") != term.pk:
            setattr(document, field_name, term)
            updates.append(field_name)
        filled.append(term.group.name)
    if updates:
        updates.append("updated_at")
        document.save(update_fields=updates)
    assign_catalog_code(document)
    return filled


def classify_source_metadata(document: SourceDocument) -> dict[str, Any]:
    config = get_classify_config()
    provider = resolve_classify_provider(config)
    max_pages = max(1, int(config.max_pages or 20))
    front = collect_source_front_text(document, max_pages=max_pages)
    if not front.strip():
        raise RuntimeError("Văn bản chưa có nội dung Markdown để phân loại.")

    prompt = build_book_classify_prompt(
        template=(config.prompt or "").strip() or default_book_classify_prompt(),
        book_title=document.title,
        catalog_text=classification_catalog_text(),
        front_text=front,
    )
    client = get_text_llm_client(provider, config.model)
    raw = client.generate(prompt)
    data = _parse_classify_json(raw)
    filled = apply_source_classify_result(document, data)
    return {
        "provider": provider.name,
        "model": provider.resolve_model(config.model),
        "pages": max_pages,
        "filled": filled,
        "raw": data,
        "catalog_code": document.catalog_code,
    }
