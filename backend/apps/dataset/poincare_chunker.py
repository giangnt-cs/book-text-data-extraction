"""Phân đoạn regex đề mục + Poincaré / Depth Score — port từ knowledge-extraction."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Sequence

from apps.dataset.poincare import PoincareMobiusOps, otsu_1d_threshold

logger = logging.getLogger(__name__)

TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]|\n")
BREAK_MARKER = "</break>"
_ROMAN = r"[IVXLCDM]+"
_NUM = r"\d+"
HEADING_PHAN = re.compile(
    rf"^Phần\s+(?:thứ\s+)?(?:{_ROMAN}|{_NUM})\b",
    re.IGNORECASE,
)
HEADING_QUYEN = re.compile(
    rf"^Quyển\s+(?:thứ\s+)?(?:đầu|{_ROMAN}|{_NUM})[:.]?(?:\s+\S.*)?$",
    re.IGNORECASE,
)
HEADING_TAP = re.compile(
    rf"^Tập\s+(?:thứ\s+)?(?:{_ROMAN}|{_NUM})[:.]?(?:\s+\S.*)?$",
    re.IGNORECASE,
)
HEADING_CHUONG = re.compile(
    rf"^Chương\s+(?:thứ\s+)?(?:{_ROMAN}|{_NUM})[:.]?(?:\s+\S.*)?$",
    re.IGNORECASE,
)
HEADING_BAI = re.compile(
    rf"^Bài\s+(?:{_ROMAN}|{_NUM})\s*[:.]?(?:\s+\S.*)?$",
    re.IGNORECASE,
)
HEADING_FRONT_MATTER = re.compile(
    r"^(?:Lời nói đầu|Lời tựa|Lời mở đầu|Mở đầu|Dẫn nhập|"
    r"Mục lục|Phụ lục|Tài liệu tham khảo)\s*[:.]?\s*"
    rf"(?:[A-Z0-9]|{_ROMAN}){{0,6}}\s*$",
    re.IGNORECASE,
)
HEADING_ROMAN = re.compile(rf"^(?:#{{1,6}}\s+)?{_ROMAN}\.\s+\S")
HEADING_NUMBERED_NESTED = re.compile(r"^\d+(?:\.\d+)+\.?\s+\S")
HEADING_NUMBERED_TOP = re.compile(r"^\d+\.\s+\S")
HEADING_CATALOG_ITEM = re.compile(r"^\d+\.\s+[^:\n]{1,60}:\s+\S")
PAGE_MARKER = re.compile(r"^#{1,6}\s+Trang\s+\d+", re.IGNORECASE)
HEADING_TOP_LEVEL = (HEADING_PHAN, HEADING_QUYEN, HEADING_TAP)
HEADING_CHAPTER = (HEADING_CHUONG, HEADING_BAI)


@dataclass
class Token:
    text: str
    start: int
    end: int


@dataclass
class ChunkResult:
    text: str
    window_count: int
    break_count: int
    scores: list[float] = field(default_factory=list)
    depth_scores: list[float] = field(default_factory=list)


class SemanticChunker:
    def __init__(
        self,
        embed_client,
        *,
        window_size: int = 20,
        step_size: int = 3,
        batch_size: int = 32,
        poincare_c: float = 1.0,
        smooth_s: int = 2,
        smooth_n: int = 2,
        max_segment_tokens: int = 200,
        min_segment_tokens: int = 200,
        on_check: Callable[[], None] | None = None,
    ):
        if window_size < 1 or step_size < 1:
            raise ValueError("window và step phải >= 1")
        if smooth_s < 0 or smooth_s % 2 != 0:
            raise ValueError("smooth_s phải là số chẵn >= 0")
        if smooth_n < 0:
            raise ValueError("smooth_n phải >= 0")
        if max_segment_tokens < 0 or min_segment_tokens < 0:
            raise ValueError("min/max tokens phải >= 0")
        self.embed_client = embed_client
        self.window_size = window_size
        self.step_size = step_size
        self.batch_size = batch_size
        self.poincare_ops = PoincareMobiusOps(c=poincare_c)
        self.smooth_s = smooth_s
        self.smooth_n = smooth_n
        self.max_segment_tokens = max_segment_tokens
        self.min_segment_tokens = min_segment_tokens
        self.on_check = on_check
        self._tiktoken_encoder = None

    def tokenize(self, text: str) -> list[Token]:
        return [
            Token(text=match.group(), start=match.start(), end=match.end())
            for match in TOKEN_PATTERN.finditer(text)
        ]

    def build_windows(self, tokens: Sequence[Token]) -> list[tuple[int, int]]:
        n = len(tokens)
        if n == 0:
            return []
        if n <= self.window_size:
            return [(0, n)]
        windows: list[tuple[int, int]] = []
        last_full_start = n - self.window_size
        for start in range(0, last_full_start + 1, self.step_size):
            windows.append((start, start + self.window_size))
        _, last_end = windows[-1]
        if last_end < n:
            windows.append((last_full_start, n))
        return windows

    def window_text(self, text: str, tokens: Sequence[Token], start: int, end: int) -> str:
        return text[tokens[start].start : tokens[end - 1].end]

    def pair_score(self, vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
        return self.poincare_ops.project_and_distance(vec_a, vec_b)

    def compute_similarities(
        self,
        vectors: Sequence[Sequence[float]],
        windows: Sequence[tuple[int, int]],
    ) -> list[tuple[float, int]]:
        similarities: list[tuple[float, int]] = []
        for i in range(len(vectors) - 1):
            score = self.pair_score(vectors[i], vectors[i + 1])
            end_token_index = windows[i + 1][1] - 1
            similarities.append((score, end_token_index))
        return similarities

    def laplace_similarities(self, distances: Sequence[float], lambda_opt: float) -> list[float]:
        return [math.exp(-lambda_opt * d) for d in distances]

    def to_poincare_similarities(
        self, distances: Sequence[float]
    ) -> tuple[list[float], float, float, float]:
        d_star, eta = otsu_1d_threshold(distances)
        lambda_opt = (1.0 / d_star) if abs(d_star) > 1e-12 else 0.0
        return self.laplace_similarities(distances, lambda_opt), d_star, eta, lambda_opt

    def smooth_scores(self, scores: Sequence[float]) -> list[float]:
        s = self.smooth_s
        n_iter = self.smooth_n
        current = list(scores)
        if s == 0 or n_iter == 0 or not current:
            return current
        half = s // 2
        length = len(current)
        for _ in range(n_iter):
            nxt = [0.0] * length
            for i in range(length):
                left = max(0, i - half)
                right = min(length, i + half + 1)
                window = current[left:right]
                nxt[i] = sum(window) / len(window)
            current = nxt
        return current

    def valley_peaks(self, scores: Sequence[float], index: int) -> tuple[int, int]:
        left = index
        while left > 0 and scores[left - 1] >= scores[left]:
            left -= 1
        right = index
        last = len(scores) - 1
        while right < last and scores[right + 1] >= scores[right]:
            right += 1
        return left, right

    def depth_score(self, scores: Sequence[float], index: int) -> float:
        left, right = self.valley_peaks(scores, index)
        return (scores[left] - scores[index]) + (scores[right] - scores[index])

    def find_valleys(self, scores: Sequence[float]) -> list[int]:
        valleys: list[int] = []
        for index in range(len(scores)):
            left, right = self.valley_peaks(scores, index)
            if left < index < right:
                valleys.append(index)
        return valleys

    def select_breaks_by_depth(
        self, scores: Sequence[float]
    ) -> tuple[list[float], list[float], list[int]]:
        smoothed = self.smooth_scores(scores)
        minima = self.find_valleys(smoothed)
        depths = [0.0] * len(smoothed)
        for index in minima:
            depths[index] = self.depth_score(smoothed, index)
        positive = [value for value in depths if value > 0]
        if not positive:
            return smoothed, depths, []
        threshold, _eta = otsu_1d_threshold(positive)
        selected = [index for index in minima if depths[index] > threshold]
        return smoothed, depths, selected

    def _uppercase_ratio(self, text: str) -> float:
        letters = [ch for ch in text if ch.isalpha()]
        if len(letters) < 2:
            return 0.0
        return sum(1 for ch in letters if ch.isupper()) / len(letters)

    def is_page_chrome(self, line: str) -> bool:
        stripped = line.strip()
        if stripped.startswith("<!--") or stripped.startswith("-->"):
            return True
        return bool(PAGE_MARKER.match(stripped))

    def _matches_heading(self, text: str, patterns: tuple) -> bool:
        return any(pattern.match(text) for pattern in patterns)

    def is_heading_line(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped or self.is_page_chrome(line):
            return False
        if stripped.startswith("#"):
            return True
        if self._matches_heading(stripped, HEADING_TOP_LEVEL + HEADING_CHAPTER):
            return True
        if HEADING_FRONT_MATTER.match(stripped) or HEADING_ROMAN.match(stripped):
            return True
        if self._uppercase_ratio(stripped) >= 0.85:
            return True
        if HEADING_NUMBERED_NESTED.match(stripped):
            return True
        if HEADING_NUMBERED_TOP.match(stripped):
            if HEADING_CATALOG_ITEM.match(stripped):
                return False
            if stripped.endswith(".") and len(stripped) > 40:
                return False
            return True
        return False

    def heading_level(self, line: str) -> int:
        if not self.is_heading_line(line):
            return -1
        stripped = line.strip()
        hashes = 0
        body = stripped
        if stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            body = stripped[hashes:].strip()
        if self._matches_heading(body, HEADING_TOP_LEVEL) or self._matches_heading(
            stripped, HEADING_TOP_LEVEL
        ):
            return 1
        if self._matches_heading(body, HEADING_CHAPTER) or HEADING_BAI.match(stripped):
            return 2
        if HEADING_ROMAN.match(body) or HEADING_ROMAN.match(stripped):
            return 4
        if HEADING_NUMBERED_NESTED.match(body) or HEADING_NUMBERED_TOP.match(body):
            if HEADING_CATALOG_ITEM.match(body):
                return -1
            return 5
        if HEADING_FRONT_MATTER.match(body) or HEADING_FRONT_MATTER.match(stripped):
            return 3
        if hashes:
            return hashes
        if self._uppercase_ratio(body) >= 0.85:
            return 3
        return 3

    def _iter_lines(self, text: str) -> list[tuple[int, int, str]]:
        lines: list[tuple[int, int, str]] = []
        pos = 0
        n = len(text)
        while pos <= n:
            nl = text.find("\n", pos)
            if nl < 0:
                lines.append((pos, n, text[pos:]))
                break
            lines.append((pos, nl, text[pos:nl]))
            pos = nl + 1
        return lines

    def heading_break_offsets(self, text: str) -> list[int]:
        lines = self._iter_lines(text)
        offsets: list[int] = []
        i = 0
        n = len(lines)
        while i < n:
            _start, _end, line = lines[i]
            if self.heading_level(line) < 0:
                i += 1
                continue
            cluster = [i]
            j = i + 1
            while j < n:
                _s, _e, nxt = lines[j]
                stripped = nxt.strip()
                if not stripped or self.is_page_chrome(nxt):
                    j += 1
                    continue
                if self.heading_level(nxt) < 0:
                    break
                cluster.append(j)
                j += 1
            best = min(cluster, key=lambda idx: (self.heading_level(lines[idx][2]), lines[idx][0]))
            insert_at = lines[best][0]
            if insert_at > 0:
                offsets.append(insert_at)
            i = j
        return offsets

    def insert_heading_breaks(self, text: str) -> str:
        return self.insert_breaks(text, self.heading_break_offsets(text))

    def split_break_parts(self, text: str) -> list[str]:
        parts: list[str] = []
        pos = 0
        while True:
            idx = text.find(BREAK_MARKER, pos)
            if idx < 0:
                parts.append(text[pos:])
                break
            parts.append(text[pos:idx])
            pos = idx + len(BREAK_MARKER)
            if pos < len(text) and text[pos] == "\n":
                pos += 1
        return parts

    def _concat_parts(self, pieces: Sequence[str]) -> str:
        return "\n\n".join(piece.strip("\n") for piece in pieces)

    def merge_heading_parts(self, parts: Sequence[str]) -> list[str]:
        min_tokens = self.min_segment_tokens
        if min_tokens <= 0:
            return list(parts)
        groups: list[str] = []
        buffer: list[str] = []
        buffer_tokens = 0

        def flush() -> None:
            nonlocal buffer, buffer_tokens
            if not buffer:
                return
            groups.append(self._concat_parts(buffer))
            buffer = []
            buffer_tokens = 0

        for part in parts:
            count = self.count_tiktoken(part) if part.strip() else 0
            buffer.append(part)
            buffer_tokens += count
            if buffer_tokens > min_tokens:
                flush()
        if buffer:
            leftover = self._concat_parts(buffer)
            if groups and buffer_tokens <= min_tokens:
                groups[-1] = self._concat_parts([groups[-1], leftover])
            else:
                groups.append(leftover)
        return groups

    def normalize_break_markers(self, text: str) -> str:
        if BREAK_MARKER not in text:
            return text
        pieces: list[str] = []
        pos = 0
        marker_len = len(BREAK_MARKER)
        while True:
            idx = text.find(BREAK_MARKER, pos)
            if idx < 0:
                pieces.append(text[pos:])
                break
            before = text[pos:idx]
            if before and not before.endswith("\n"):
                before += "\n"
            pieces.append(before)
            pieces.append(BREAK_MARKER)
            pos = idx + marker_len
            if pos >= len(text):
                pieces.append("\n")
                break
            if text[pos] != "\n":
                pieces.append("\n")
        return "".join(pieces)

    def join_break_parts(self, parts: Sequence[str]) -> str:
        cleaned: list[str] = []
        for part in parts:
            piece = part.strip("\n")
            while piece.startswith(BREAK_MARKER):
                piece = piece[len(BREAK_MARKER) :].lstrip("\n")
            while piece.endswith(BREAK_MARKER):
                piece = piece[: -len(BREAK_MARKER)].rstrip("\n")
            if piece:
                cleaned.append(piece)
        joined = f"\n{BREAK_MARKER}\n".join(cleaned)
        double = f"\n{BREAK_MARKER}\n{BREAK_MARKER}\n"
        while double in joined:
            joined = joined.replace(double, f"\n{BREAK_MARKER}\n")
        return self.normalize_break_markers(joined)

    def count_tiktoken(self, text: str) -> int:
        if self._tiktoken_encoder is None:
            try:
                import tiktoken
            except ImportError as exc:
                raise ImportError("Cần cài tiktoken: pip install tiktoken") from exc
            self._tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
        return len(self._tiktoken_encoder.encode(text))

    def shift_break_before_headings(self, text: str, offset: int) -> int:
        insert_pos = offset
        pos = offset
        while pos > 0 and text[pos - 1] == "\n":
            prev_nl = text.rfind("\n", 0, pos - 1)
            start = prev_nl + 1
            line = text[start : pos - 1]
            stripped = line.strip()
            if stripped == BREAK_MARKER or stripped.startswith(BREAK_MARKER):
                break
            if not stripped or self.is_page_chrome(line):
                pos = start
                continue
            if not self.is_heading_line(line):
                break
            insert_pos = start
            pos = start
        return insert_pos

    def relocate_breaks(self, text: str) -> str:
        marker = BREAK_MARKER + "\n"
        if BREAK_MARKER not in text:
            return text
        anchors: list[int] = []
        parts: list[str] = []
        pos = 0
        clean_len = 0
        while True:
            idx = text.find(marker, pos)
            if idx < 0:
                parts.append(text[pos:])
                break
            chunk = text[pos:idx]
            parts.append(chunk)
            clean_len += len(chunk)
            anchors.append(clean_len)
            pos = idx + len(marker)
        clean = "".join(parts)
        new_offsets = [self.shift_break_before_headings(clean, anchor) for anchor in anchors]
        return self.insert_breaks(clean, new_offsets)

    def find_break_offset(self, text: str, tokens: Sequence[Token], end_token_index: int) -> int:
        search_end = tokens[end_token_index].end
        for i in range(min(search_end, len(text)) - 1, -1, -1):
            if text[i] != "\n":
                continue
            if i + 1 >= len(text):
                continue
            if text[i + 1].isupper():
                return self.shift_break_before_headings(text, i + 1)
        return -1

    def insert_breaks(self, text: str, offsets: Sequence[int]) -> str:
        unique_offsets = sorted({offset for offset in offsets if offset >= 0}, reverse=True)
        result = text
        marker = BREAK_MARKER + "\n"
        for offset in unique_offsets:
            result = result[:offset] + marker + result[offset:]
        return result

    def _segment_with_metric(
        self,
        text: str,
        tokens: Sequence[Token],
        windows: Sequence[tuple[int, int]],
        vectors: Sequence[Sequence[float]],
    ) -> ChunkResult:
        similarities = self.compute_similarities(vectors, windows)
        distances = [item[0] for item in similarities]
        scores, _d_star, _eta, _lam = self.to_poincare_similarities(distances)
        smoothed, depths, selected = self.select_breaks_by_depth(scores)
        break_offsets: list[int] = []
        for idx in selected:
            _score, end_token_index = similarities[idx]
            offset = self.find_break_offset(text, tokens, end_token_index)
            if offset >= 0:
                break_offsets.append(offset)
        chunked = self.normalize_break_markers(
            self.relocate_breaks(self.insert_breaks(text, break_offsets))
        )
        return ChunkResult(
            text=chunked,
            window_count=len(windows),
            break_count=chunked.count(BREAK_MARKER),
            scores=smoothed,
            depth_scores=depths,
        )

    def _embed_texts(self, window_texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        if not window_texts:
            return vectors
        for i in range(0, len(window_texts), self.batch_size):
            if self.on_check:
                self.on_check()
            batch = list(window_texts[i : i + self.batch_size])
            vectors.extend(self.embed_client.embed(batch, batch_size=self.batch_size))
        return vectors

    def chunk_text(self, text: str) -> ChunkResult:
        headed = self.normalize_break_markers(self.insert_heading_breaks(text))
        raw_parts = self.split_break_parts(headed)
        logger.info("Cắt đề mục regex: %s </break>", headed.count(BREAK_MARKER))
        parts = self.merge_heading_parts(raw_parts)
        logger.info(
            "Gom đoạn ngắn đến khi > %s token: %s → %s đoạn",
            self.min_segment_tokens,
            len(raw_parts),
            len(parts),
        )
        processed: list[str] = []
        window_count = 0
        scores: list[float] = []
        depths: list[float] = []
        for index, part in enumerate(parts, start=1):
            if self.on_check:
                self.on_check()
            token_count = self.count_tiktoken(part) if part.strip() else 0
            apply_depth = self.max_segment_tokens == 0 or token_count > self.max_segment_tokens
            if not apply_depth or not part.strip():
                processed.append(part)
                continue
            tokens = self.tokenize(part)
            windows = self.build_windows(tokens)
            if len(windows) < 4:
                processed.append(part)
                continue
            logger.info(
                "Đoạn %s/%s: %s token, cắt Poincaré (%s cửa sổ)",
                index,
                len(parts),
                token_count,
                len(windows),
            )
            window_texts = [self.window_text(part, tokens, start, end) for start, end in windows]
            vectors = self._embed_texts(window_texts)
            result = self._segment_with_metric(part, tokens, windows, vectors)
            processed.append(result.text)
            window_count += result.window_count
            scores.extend(result.scores)
            depths.extend(result.depth_scores)
        joined = self.join_break_parts(processed)
        return ChunkResult(
            text=joined,
            window_count=window_count,
            break_count=joined.count(BREAK_MARKER),
            scores=scores,
            depth_scores=depths,
        )
