"""Đếm token phân đoạn — tiktoken cl100k_base, fallback khi chưa cài."""

from __future__ import annotations

import re

_CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

_encoding = None
_encoding_failed = False


def count_tokens(text: str) -> int:
    body = text or ""
    if not body.strip():
        return 0
    encoded = _tiktoken_count(body)
    if encoded is not None:
        return encoded
    return _fallback_count(body)


def _tiktoken_count(text: str) -> int | None:
    global _encoding, _encoding_failed
    if _encoding_failed:
        return None
    try:
        if _encoding is None:
            import tiktoken

            _encoding = tiktoken.get_encoding("cl100k_base")
        return len(_encoding.encode(text))
    except Exception:
        _encoding_failed = True
        return None


def _fallback_count(text: str) -> int:
    cjk = len(_CJK_RE.findall(text))
    words = len(_WORD_RE.findall(_CJK_RE.sub(" ", text)))
    return max(1, cjk + words)
