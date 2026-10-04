"""Chạy phân đoạn Poincaré trên toàn bộ Markdown văn bản nguồn."""

from __future__ import annotations

import logging
import re
from typing import Callable

from apps.dataset.embed_client import create_embed_client
from apps.dataset.markdown import read_source_file, split_source_markdown
from apps.dataset.models import SourceDocument
from apps.dataset.poincare_chunker import SemanticChunker
from apps.llm.chunking_runner import count_breaks
from apps.llm.models import ChunkingConfig, ChunkingMethod
from apps.llm.page_stitch_runner import (
    inject_page_comments,
    stitch_page_texts_heuristic,
    strip_page_comments,
)

logger = logging.getLogger(__name__)

_PAGE_COMMENT_RE = re.compile(r"<!--\s*Trang\s+(\d+)\s*-->", re.IGNORECASE)


def _page_texts_from_document(document: SourceDocument) -> tuple[list[int], list[str]]:
    segments = list(document.segments.order_by("page_number"))
    if segments:
        numbers = [int(segment.page_number or 1) for segment in segments]
        texts = [strip_page_comments(segment.text_md or "") for segment in segments]
        return numbers, texts
    body = read_source_file(document).replace("\r\n", "\n").replace("\r", "\n")
    parts = split_source_markdown(body)
    if not parts:
        return [1], [strip_page_comments(body)]
    return [int(number) for number, _text in parts], [strip_page_comments(text) for _number, text in parts]


_HEADING_PROBE: SemanticChunker | None = None


def _heading_line(line: str) -> bool:
    global _HEADING_PROBE
    if _HEADING_PROBE is None:
        _HEADING_PROBE = SemanticChunker(embed_client=None)
    return _HEADING_PROBE.is_page_chrome(line) or _HEADING_PROBE.is_heading_line(line)


def stitch_and_mark_pages(
    page_numbers: list[int],
    texts: list[str],
    config: ChunkingConfig | None = None,
    *,
    on_check: Callable[[], None] | None = None,
) -> tuple[list[str], set[int], int]:
    """Nối câu bị ngắt qua trang, rồi đặt <!-- Trang N --> ngay sau đoạn vừa khép."""
    stitched, joined_into_prev, joined = stitch_page_texts_heuristic(
        texts,
        page_numbers=page_numbers,
        is_heading=_heading_line,
        on_check=on_check,
    )
    if joined:
        logger.info("Poincaré nối %s đoạn bị ngắt qua trang", joined)
    marked = inject_page_comments(
        stitched,
        page_numbers=page_numbers,
        joined_into_prev=joined_into_prev,
    )
    return marked, joined_into_prev, joined


def assemble_poincare_source(
    document: SourceDocument,
    config: ChunkingConfig | None = None,
    *,
    on_check: Callable[[], None] | None = None,
) -> tuple[str, int]:
    page_numbers, texts = _page_texts_from_document(document)
    if config is not None and page_numbers:
        texts, _joined_into_prev, joined = stitch_and_mark_pages(
            page_numbers,
            texts,
            config,
            on_check=on_check,
        )
    else:
        joined = 0
        texts = [
            f"<!-- Trang {number} -->\n{text}".strip() if text.strip() else f"<!-- Trang {number} -->"
            for number, text in zip(page_numbers, texts)
        ]
    body = "\n\n".join(part.strip() for part in texts if (part or "").strip())
    return body.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n", joined


def split_poincare_pages(text: str, page_numbers: list[int]) -> dict[int, str]:
    """Tách lại theo <!-- Trang N -->; giữ marker dính ngay sau đoạn phía trước."""
    result = {int(n): "" for n in page_numbers}
    matches = list(_PAGE_COMMENT_RE.finditer(text or ""))
    if not matches:
        if page_numbers:
            result[int(page_numbers[0])] = (text or "").strip()
        return result
    buckets: dict[int, list[str]] = {int(n): [] for n in page_numbers}
    first_page = int(matches[0].group(1))
    preamble = text[: matches[0].start()]
    current = first_page if first_page in buckets else int(page_numbers[0])
    if preamble.strip():
        buckets.setdefault(current, []).append(preamble.rstrip())
    for i, match in enumerate(matches):
        page = int(match.group(1))
        marker = text[match.start() : match.end()]
        owner = current if current in buckets else page
        if owner not in buckets:
            buckets[owner] = []
        if buckets[owner] and not buckets[owner][-1].endswith("\n"):
            buckets[owner][-1] += "\n"
        buckets[owner].append(marker)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        following = text[match.end() : end]
        current = page if page in buckets else current
        if following.strip() or following:
            buckets.setdefault(current, []).append(following)
    for page, pieces in buckets.items():
        result[page] = "".join(pieces).strip()
    return result


def poincare_settings_summary(config: ChunkingConfig) -> str:
    return (
        f"Poincaré window={config.poincare_window} step={config.poincare_step} "
        f"c={config.poincare_c:g} smooth_s={config.smooth_s} smooth_n={config.smooth_n} "
        f"min_tokens={config.min_tokens} max_tokens={config.max_tokens} "
        f"embed={config.embed_backend} {config.embed_host} / {config.embed_model}"
    )


def run_poincare_chunking(
    document: SourceDocument,
    config: ChunkingConfig,
    *,
    on_check: Callable[[], None] | None = None,
) -> dict:
    if (config.method or ChunkingMethod.LLM) != ChunkingMethod.POINCARE:
        raise RuntimeError("Cấu hình không phải phương pháp Poincaré.")
    host = (config.embed_host or "").strip()
    model = (config.embed_model or "").strip()
    if not host or not model:
        raise RuntimeError("Chưa cấu hình host/model embedding Poincaré.")
    source, joined = assemble_poincare_source(document, config, on_check=on_check)
    if not source.strip():
        raise RuntimeError("Văn bản chưa có nội dung Markdown.")
    client = create_embed_client(
        provider=config.embed_backend or "ollama",
        host=host,
        model=model,
        api_key=(config.embed_api_key or "").strip() or None,
    )
    chunker = SemanticChunker(
        client,
        window_size=int(config.poincare_window or 20),
        step_size=int(config.poincare_step or 3),
        batch_size=int(config.embed_batch_size or 32),
        poincare_c=float(config.poincare_c or 1.0),
        smooth_s=int(config.smooth_s or 2),
        smooth_n=int(config.smooth_n or 2),
        max_segment_tokens=int(config.max_tokens or 200),
        min_segment_tokens=int(config.min_tokens or 200),
        on_check=on_check,
    )
    result = chunker.chunk_text(source)
    return {
        "text": result.text,
        "breaks": count_breaks(result.text),
        "windows": result.window_count,
        "joined_pages": joined,
        "summary": poincare_settings_summary(config),
    }
