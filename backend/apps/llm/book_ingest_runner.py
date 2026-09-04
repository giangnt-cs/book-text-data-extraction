"""LLM rút trích tên sách từ trang đầu và kiểm tra trùng tên theo lô."""

from __future__ import annotations

import json
import logging
from typing import Any

from apps.llm.models import BookDedupeConfig, BookIngestConfig, LLMProvider
from apps.llm.text_cleaning_runner import get_default_llm, get_text_llm_client
from ocr_llm.prompts import (
    build_book_dedupe_prompt,
    build_book_ingest_match_prompt,
    build_book_ingest_prompt,
    default_book_dedupe_prompt,
    default_book_ingest_match_prompt,
    default_book_ingest_prompt,
)

logger = logging.getLogger(__name__)


def get_ingest_config() -> BookIngestConfig:
    obj, _ = BookIngestConfig.objects.get_or_create(
        pk=1,
        defaults={"prompt": default_book_ingest_prompt()},
    )
    if not (obj.prompt or "").strip():
        obj.prompt = default_book_ingest_prompt()
        obj.save(update_fields=["prompt", "updated_at"])
    return obj


def get_dedupe_config() -> BookDedupeConfig:
    obj, _ = BookDedupeConfig.objects.get_or_create(
        pk=1,
        defaults={"prompt": default_book_dedupe_prompt(), "batch_size": 30},
    )
    if not (obj.prompt or "").strip():
        obj.prompt = default_book_dedupe_prompt()
        obj.save(update_fields=["prompt", "updated_at"])
    return obj


def resolve_feature_provider(config) -> LLMProvider:
    if getattr(config, "provider_id", None) and config.provider and config.provider.is_active:
        return config.provider
    return get_default_llm()


def _parse_json_object(raw: str) -> dict[str, Any]:
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


def extract_title_from_page(
    *,
    page_text: str,
    original_name: str = "",
) -> dict[str, Any]:
    """Chỉ rút tên từ trang đầu — không gửi cả catalog sách."""
    config = get_ingest_config()
    provider = resolve_feature_provider(config)
    text = (page_text or "").strip()
    if len(text) > 6000:
        text = text[:6000]
    prompt = build_book_ingest_prompt(
        template=(config.prompt or "").strip() or default_book_ingest_prompt(),
        page_text=text,
        original_name=original_name,
    )
    raw = get_text_llm_client(provider, config.model).generate(prompt)
    data = _parse_json_object(raw)
    title = str(data.get("title") or "").strip()[:255]
    return {"title": title, "provider": provider.name}


def confirm_title_duplicate(*, title: str, candidates: list[str]) -> str:
    """Hỏi LLM tên vừa rút có trùng một trong vài ứng viên gần giống không."""
    cleaned = [str(t).strip() for t in candidates if str(t).strip()]
    if not title.strip() or len(cleaned) < 1:
        return ""
    prompt = build_book_ingest_match_prompt(
        template=default_book_ingest_match_prompt(),
        title=title.strip(),
        candidates_text="\n".join(f"- {t}" for t in cleaned),
    )
    config = get_ingest_config()
    provider = resolve_feature_provider(config)
    raw = get_text_llm_client(provider, config.model).generate(prompt)
    data = _parse_json_object(raw)
    matched = str(data.get("matched_title") or "").strip()
    known = {t.casefold(): t for t in cleaned}
    if bool(data.get("duplicate")) and matched:
        return known.get(matched.casefold(), "") or next(
            (t for t in cleaned if t.casefold() == matched.casefold()),
            "",
        )
    return ""


def extract_title_and_match(
    *,
    page_text: str,
    existing_titles: list[str] | None = None,
    original_name: str = "",
) -> dict[str, Any]:
    extracted = extract_title_from_page(page_text=page_text, original_name=original_name)
    title = extracted["title"]
    return {
        "title": title,
        "duplicate": False,
        "matched_title": "",
        "provider": extracted["provider"],
    }


def find_duplicate_title_groups(titles: list[str]) -> list[dict[str, Any]]:
    """Gọi LLM trên một lô tên. Trả về [{canonical, titles}]."""
    config = get_dedupe_config()
    provider = resolve_feature_provider(config)
    cleaned = [str(t).strip() for t in titles if str(t).strip()]
    if len(cleaned) < 2:
        return []
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(cleaned, start=1))
    prompt = build_book_dedupe_prompt(
        template=(config.prompt or "").strip() or default_book_dedupe_prompt(),
        titles_text=numbered,
    )
    raw = get_text_llm_client(provider, config.model).generate(prompt)
    data = _parse_json_object(raw)
    groups = data.get("groups") or []
    known = set(cleaned)
    result: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        members = [str(t).strip() for t in (group.get("titles") or []) if str(t).strip() in known]
        # khử trùng trong nhóm, giữ thứ tự
        uniq: list[str] = []
        for t in members:
            if t not in uniq:
                uniq.append(t)
        if len(uniq) < 2:
            continue
        canonical = str(group.get("canonical") or uniq[0]).strip() or uniq[0]
        result.append({"canonical": canonical, "titles": uniq})
    return result
