"""Nối đoạn dang dở qua ranh giới trang — LLM chỉ trả true/false."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable

from apps.llm.models import LLMProvider
from apps.llm.text_cleaning_runner import get_text_llm_client
from ocr_llm.prompts import build_page_stitch_prompt, default_page_stitch_prompt

logger = logging.getLogger(__name__)

STITCH_MAX_TOKENS = 24
PAGE_COMMENT_TEMPLATE = "<!-- Trang {n} -->"
_PAGE_COMMENT_RE = re.compile(r"^<!--\s*Trang\s+(\d+)\s*-->\s*$", re.IGNORECASE)
_BOOL_RE = re.compile(r"\b(true|false)\b", re.IGNORECASE)
_JOIN_KEYS = (
    "is_continuation",
    "continuation",
    "should_join",
    "join",
    "connect",
    "merge",
    "result",
    "value",
    "ok",
)


def split_paragraphs(text: str) -> list[str]:
    body = (text or "").replace("\r\n", "\n").strip()
    if not body:
        return []
    return [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]


def join_paragraphs(paragraphs: list[str]) -> str:
    return "\n\n".join(p.strip() for p in paragraphs if (p or "").strip())


def merge_paragraphs(left: str, right: str) -> str:
    a = (left or "").rstrip()
    b = (right or "").lstrip()
    if not a:
        return b
    if not b:
        return a
    if a.endswith("-"):
        return a[:-1] + b
    if a[-1].isspace():
        return a + b
    return a + " " + b


def _bool_from_value(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        lower = {str(key).lower(): item for key, item in value.items()}
        for key in _JOIN_KEYS:
            if key in lower:
                found = _bool_from_value(lower[key])
                if found is not None:
                    return found
        for item in value.values():
            found = _bool_from_value(item)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _bool_from_value(item)
            if found is not None:
                return found
    return None


def parse_join_bool(raw: str) -> bool | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = "```".join(parts[1:-1]).strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
    try:
        found = _bool_from_value(json.loads(text))
        if found is not None:
            return found
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            found = _bool_from_value(json.loads(text[start : end + 1]))
            if found is not None:
                return found
        except json.JSONDecodeError:
            pass
    match = _BOOL_RE.search(text)
    if match:
        return match.group(1).lower() == "true"
    return None


def get_stitch_prompt() -> str:
    from apps.llm.chunking_runner import get_chunking_config

    config = get_chunking_config()
    text = (getattr(config, "stitch_prompt", None) or "").strip()
    return text or default_page_stitch_prompt()


def should_join_paragraphs(
    *,
    provider: LLMProvider,
    model: str,
    left: str,
    right: str,
    prompt_template: str = "",
) -> bool:
    if not (left or "").strip() or not (right or "").strip():
        return False
    prompt = build_page_stitch_prompt(
        template=(prompt_template or "").strip() or default_page_stitch_prompt(),
        left=left,
        right=right,
    )
    raw = ""
    try:
        client = get_text_llm_client(provider, model)
        raw = client.generate(prompt, max_tokens=STITCH_MAX_TOKENS)
        decision = parse_join_bool(raw)
    except Exception:
        logger.exception("LLM nối trang lỗi — giữ tách.")
        return False
    if decision is None:
        logger.warning("LLM nối trang trả lời không parse được: %s", (raw or "")[:80])
        return False
    return decision


def stitch_page_texts(
    texts: list[str],
    *,
    page_numbers: list[int],
    provider: LLMProvider,
    model: str = "",
    prompt_template: str = "",
    on_check: Callable[[], None] | None = None,
) -> tuple[list[str], set[int], int]:
    """Nối đoạn cuối/đầu hai trang liên tiếp. Trả (texts, trang đã nối về trước, số cặp nối)."""
    template = (prompt_template or "").strip() or get_stitch_prompt()
    return _stitch_page_texts(
        texts,
        page_numbers=page_numbers,
        on_check=on_check,
        should_join=lambda left, right: should_join_paragraphs(
            provider=provider,
            model=model,
            left=left,
            right=right,
            prompt_template=template,
        ),
    )


_SENTENCE_END_RE = re.compile(r"[.!?…。！？][»”\"'’)\]\s]*$")
_PAGE_COMMENT_LINE_RE = re.compile(r"(?m)^\s*<!--\s*Trang\s+\d+\s*-->\s*\n?")


def strip_page_comments(text: str) -> str:
    return _PAGE_COMMENT_LINE_RE.sub("", text or "").strip()


def is_incomplete_paragraph(text: str) -> bool:
    """Đoạn cuối trang bị ngắt: gạch nối, dấu phẩy/chấm phẩy, hoặc chưa kết câu."""
    body = (text or "").rstrip()
    if not body:
        return False
    if body.endswith("-") or body.endswith("\u00ad"):
        return True
    if body.endswith(",") or body.endswith(";"):
        return True
    return _SENTENCE_END_RE.search(body) is None


def _right_continues_sentence(text: str) -> bool:
    first = (text or "").lstrip()[:1]
    return bool(first) and first.islower()


def _is_noise_paragraph(text: str) -> bool:
    """Dòng trống, comment trang/OCR — không phải nội dung để nối câu."""
    line = (text or "").strip().split("\n", 1)[0].strip()
    if not line:
        return True
    if line.startswith("<!--") or line.startswith("-->"):
        return True
    return bool(_PAGE_COMMENT_RE.match(line))


def _strip_leading_noise_lines(text: str) -> str:
    lines = (text or "").replace("\r\n", "\n").split("\n")
    while lines:
        stripped = lines[0].strip()
        if not stripped or stripped.startswith("<!--") or stripped.startswith("-->"):
            lines.pop(0)
            continue
        if _PAGE_COMMENT_RE.match(stripped):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


def _right_starts_new_block(text: str, *, is_heading: Callable[[str], bool] | None) -> bool:
    first = _strip_leading_noise_lines(text)
    if not first:
        return True
    line = first.split("\n", 1)[0].strip()
    if is_heading and is_heading(line):
        return True
    return False


def _content_paragraphs(text: str) -> list[str]:
    """Tách đoạn, bỏ đoạn rỗng / comment trang ở đầu và cuối."""
    paras = [_strip_leading_noise_lines(part) for part in split_paragraphs(text)]
    paras = [part for part in paras if part and not _is_noise_paragraph(part)]
    return paras


def _stitch_page_texts(
    texts: list[str],
    *,
    page_numbers: list[int],
    on_check: Callable[[], None] | None = None,
    should_join: Callable[[str, str], bool],
) -> tuple[list[str], set[int], int]:
    pages = [strip_page_comments(text) for text in texts]
    joined_into_prev: set[int] = set()
    joined = 0
    if len(pages) < 2:
        return pages, joined_into_prev, joined
    for i in range(len(pages) - 1):
        if on_check:
            on_check()
        left_paras = _content_paragraphs(pages[i])
        right_paras = _content_paragraphs(pages[i + 1])
        if not left_paras or not right_paras:
            continue
        if not should_join(left_paras[-1], right_paras[0]):
            continue
        left_paras[-1] = merge_paragraphs(left_paras[-1], right_paras[0])
        right_paras = right_paras[1:]
        pages[i] = join_paragraphs(left_paras)
        pages[i + 1] = join_paragraphs(right_paras)
        joined_into_prev.add(int(page_numbers[i + 1]))
        joined += 1
    return pages, joined_into_prev, joined


def stitch_page_texts_heuristic(
    texts: list[str],
    *,
    page_numbers: list[int],
    is_heading: Callable[[str], bool] | None = None,
    on_check: Callable[[], None] | None = None,
) -> tuple[list[str], set[int], int]:
    """Nối đoạn cuối trang nếu câu chưa kết; bỏ qua dòng trống / comment trang."""

    def should_join(left: str, right: str) -> bool:
        right = _strip_leading_noise_lines(right)
        if _right_starts_new_block(right, is_heading=is_heading):
            return False
        body = (left or "").rstrip()
        if body.endswith("-") or body.endswith("\u00ad") or body.endswith(",") or body.endswith(";"):
            return True
        if _SENTENCE_END_RE.search(body):
            return False
        return _right_continues_sentence(right)

    return _stitch_page_texts(
        texts,
        page_numbers=page_numbers,
        on_check=on_check,
        should_join=should_join,
    )


def _append_page_comment(paragraph: str, page_number: int) -> str:
    body = (paragraph or "").rstrip()
    marker = PAGE_COMMENT_TEMPLATE.format(n=page_number)
    last_line = body.splitlines()[-1].strip() if body else ""
    if _PAGE_COMMENT_RE.match(last_line):
        return body
    return f"{body}\n{marker}" if body else marker


def inject_page_comments(
    texts: list[str],
    *,
    page_numbers: list[int],
    joined_into_prev: set[int] | None = None,
) -> list[str]:
    """Chèn `<!-- Trang N -->` sau đoạn đầu mỗi trang (hoặc sau đoạn đã nối về trang trước)."""
    pages = list(texts)
    joined = joined_into_prev or set()
    for i, number in enumerate(page_numbers):
        paras = split_paragraphs(pages[i])
        if not paras:
            continue
        if int(number) in joined:
            continue
        paras[0] = _append_page_comment(paras[0], int(number))
        pages[i] = join_paragraphs(paras)
    for i, number in enumerate(page_numbers):
        if int(number) not in joined or i == 0:
            continue
        paras = split_paragraphs(pages[i - 1])
        if not paras:
            continue
        paras[-1] = _append_page_comment(paras[-1], int(number))
        pages[i - 1] = join_paragraphs(paras)
    return pages
