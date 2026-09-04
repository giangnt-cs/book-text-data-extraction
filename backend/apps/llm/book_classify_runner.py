"""Phân loại sách và điền metadata bằng LLM từ ~20 trang đầu."""

from __future__ import annotations

import json
import logging
from typing import Any

from apps.documents.classification import BOOK_CLASS_FIELDS, CLASSIFICATION_CATALOG
from apps.documents.markdown import get_active_cleaning_batch, page_display_text
from apps.documents.models import Book, ClassificationTerm
from apps.llm.models import BookClassifyConfig, LLMProvider
from apps.llm.text_cleaning_runner import get_default_llm, get_text_llm_client
from ocr_llm.prompts import build_book_classify_prompt, default_book_classify_prompt

logger = logging.getLogger(__name__)

MAX_FRONT_CHARS = 24000
CLASS_FIELD_BY_SLUG = {slug: field for field, slug in BOOK_CLASS_FIELDS}


def get_classify_config() -> BookClassifyConfig:
    prompt = default_book_classify_prompt()
    obj, _ = BookClassifyConfig.objects.get_or_create(pk=1, defaults={"prompt": prompt, "max_pages": 20})
    if not (obj.prompt or "").strip():
        obj.prompt = prompt
        obj.save(update_fields=["prompt", "updated_at"])
    return obj


def resolve_classify_provider(config: BookClassifyConfig | None = None) -> LLMProvider:
    config = config or get_classify_config()
    if config.provider_id and config.provider and config.provider.is_active:
        return config.provider
    return get_default_llm()


def classification_catalog_text() -> str:
    lines: list[str] = []
    for slug, name, _kind, terms in CLASSIFICATION_CATALOG:
        field = CLASS_FIELD_BY_SLUG.get(slug, slug)
        lines.append(f"{field} ({name}):")
        for code, term_name, desc in terms:
            extra = f" — {desc}" if desc else ""
            lines.append(f"  {code}: {term_name}{extra}")
        lines.append("")
    return "\n".join(lines).strip()


def _extract_pdf_front_text(book: Book, max_pages: int) -> str:
    if not book.pdf_file:
        return ""
    try:
        import fitz
    except ImportError:
        return ""
    try:
        with book.pdf_file.open("rb") as f:
            data = f.read()
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        logger.exception("Không đọc được PDF «%s»", book.title)
        return ""
    parts: list[str] = []
    try:
        n = min(max_pages, doc.page_count)
        for i in range(n):
            text = (doc[i].get_text("text") or "").strip()
            if text:
                parts.append(f"--- Trang {i + 1} ---\n{text}")
    finally:
        doc.close()
    return "\n\n".join(parts)


def collect_front_text(book: Book, *, max_pages: int = 20) -> str:
    pages = list(book.pages.order_by("page_number")[:max_pages])
    batch = get_active_cleaning_batch(book) if pages else None
    parts: list[str] = []
    for page in pages:
        text = page_display_text(page, batch=batch)
        if text:
            parts.append(f"--- Trang {page.page_number} ---\n{text}")
    if parts:
        return "\n\n".join(parts)[:MAX_FRONT_CHARS]
    return _extract_pdf_front_text(book, max_pages)[:MAX_FRONT_CHARS]


def _parse_classify_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = "```".join(parts[1:-1]).strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM không trả JSON hợp lệ.")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM không trả object JSON.")
    return data


def _term_for(slug: str, code: str) -> ClassificationTerm | None:
    code = (code or "").strip()
    if not code:
        return None
    return (
        ClassificationTerm.objects.filter(group__slug=slug, code__iexact=code)
        .select_related("group")
        .first()
    )


def apply_classify_result(book: Book, data: dict[str, Any]) -> list[str]:
    """Ghi metadata/phân loại. Trả về danh sách trường đã điền."""
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
        if value != getattr(book, attr):
            setattr(book, attr, value[: 400 if attr != "published_year" else 32])
            updates.append(attr)
        filled.append(label)

    for field_name, slug in BOOK_CLASS_FIELDS:
        term = _term_for(slug, str(data.get(field_name) or ""))
        if term is None:
            continue
        if getattr(book, f"{field_name}_id") != term.pk:
            setattr(book, field_name, term)
            updates.append(field_name)
        filled.append(term.group.name)

    if updates:
        updates.append("updated_at")
        book.save(update_fields=updates)
    return filled


def classify_book_metadata(book: Book) -> dict[str, Any]:
    config = get_classify_config()
    provider = resolve_classify_provider(config)
    max_pages = max(1, int(config.max_pages or 20))
    front = collect_front_text(book, max_pages=max_pages)
    if not front.strip():
        raise RuntimeError(
            "Chưa có chữ ở các trang đầu. Chạy OCR vài trang đầu hoặc dùng PDF có lớp text."
        )

    prompt = build_book_classify_prompt(
        template=(config.prompt or "").strip() or default_book_classify_prompt(),
        book_title=book.title,
        catalog_text=classification_catalog_text(),
        front_text=front,
    )
    client = get_text_llm_client(provider, config.model)
    raw = client.generate(prompt)
    data = _parse_classify_json(raw)
    filled = apply_classify_result(book, data)
    return {
        "provider": provider.name,
        "model": provider.resolve_model(config.model),
        "pages": max_pages,
        "filled": filled,
        "raw": data,
    }
