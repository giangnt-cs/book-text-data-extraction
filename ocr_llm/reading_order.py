"""Dựng lại text OCR theo toạ độ: trên → dưới, trái → phải, tách cột nếu có."""

from __future__ import annotations

from dataclasses import dataclass

EDGE_TOP_RATIO = 0.15
EDGE_BOTTOM_RATIO = 0.85
EDGE_MAX_HEIGHT_RATIO = 0.12
WORD_IN_BOX_PAD_RATIO = 0.004
UNKNOWN_TYPES = frozenset({"unknown"})
COLUMN_MID_LO = 0.22
COLUMN_MID_HI = 0.78
COLUMN_MIN_WORDS = 8
COLUMN_MIN_SHARE = 0.12
COLUMN_MIN_WIDTH_RATIO = 0.18
COLUMN_MIN_CONTENT_RATIO = 0.12
COLUMN_COVERAGE_BINS = 40
COLUMN_CENTER_BIN_PX = 20
LINE_Y_TOL_RATIO = 0.38
LINE_Y_TOL_MIN_RATIO = 0.004
MAX_LINE_SLOPE = 0.08

_NO_SPACE_BEFORE = {",", ".", ":", ";", ")", "!", "?", "…", "%", "”", "’"}
_NO_SPACE_AFTER = {"(", "-", "–", "“", "‘"}


@dataclass(frozen=True)
class WordBox:
    text: str
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @property
    def cx(self) -> float:
        return (self.xmin + self.xmax) / 2.0

    @property
    def cy(self) -> float:
        return (self.ymin + self.ymax) / 2.0

    @property
    def h(self) -> float:
        return max(self.ymax - self.ymin, 0.0)


@dataclass(frozen=True)
class LayoutRect:
    x: float
    y: float
    width: float
    height: float
    content_type: str = ""

    @property
    def x2(self) -> float:
        return self.x + self.width

    @property
    def y2(self) -> float:
        return self.y + self.height


def _obj_get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def layout_rect_from_obj(obj) -> LayoutRect:
    ct = _obj_get(obj, "content_type", "")
    if hasattr(ct, "value"):
        ct = ct.value
    return LayoutRect(
        x=float(_obj_get(obj, "x", 0) or 0),
        y=float(_obj_get(obj, "y", 0) or 0),
        width=float(_obj_get(obj, "width", 0) or 0),
        height=float(_obj_get(obj, "height", 0) or 0),
        content_type=str(ct or "").strip().lower(),
    )


def header_footer_rects(
    boxes: list[LayoutRect],
    page_width: float,
    page_height: float,
) -> list[LayoutRect]:
    """Box unknown thấp ở mép trên/dưới — header/footer theo doclayout-yolo."""
    if page_width <= 0 or page_height <= 0:
        return []
    out: list[LayoutRect] = []
    for box in boxes:
        if box.content_type not in UNKNOWN_TYPES:
            continue
        if box.height > page_height * EDGE_MAX_HEIGHT_RATIO:
            continue
        if box.y < page_height * EDGE_TOP_RATIO or box.y2 > page_height * EDGE_BOTTOM_RATIO:
            out.append(box)
    return out


def scale_words(
    words: list[WordBox],
    src_width: float,
    src_height: float,
    dst_width: float,
    dst_height: float,
) -> list[WordBox]:
    if not words or src_width <= 0 or src_height <= 0:
        return list(words)
    if abs(src_width - dst_width) < 1e-3 and abs(src_height - dst_height) < 1e-3:
        return list(words)
    sx = dst_width / src_width
    sy = dst_height / src_height
    return [
        WordBox(w.text, w.xmin * sx, w.ymin * sy, w.xmax * sx, w.ymax * sy)
        for w in words
    ]


def words_in_box_space(
    words: list[WordBox],
    src_width: float,
    src_height: float,
    box_space_size: tuple[float, float] | None,
) -> tuple[list[WordBox], float, float]:
    """Đưa chữ về cùng hệ pixel với bbox YOLO khi biết kích thước ảnh detect."""
    if box_space_size and box_space_size[0] > 0 and box_space_size[1] > 0:
        dst_w, dst_h = float(box_space_size[0]), float(box_space_size[1])
        return scale_words(words, src_width, src_height, dst_w, dst_h), dst_w, dst_h
    return words, src_width, src_height


def scale_rects(
    rects: list[LayoutRect],
    src_width: float,
    src_height: float,
    dst_width: float,
    dst_height: float,
) -> list[LayoutRect]:
    if not rects or src_width <= 0 or src_height <= 0:
        return list(rects)
    if abs(src_width - dst_width) < 1e-3 and abs(src_height - dst_height) < 1e-3:
        return list(rects)
    sx = dst_width / src_width
    sy = dst_height / src_height
    return [
        LayoutRect(r.x * sx, r.y * sy, r.width * sx, r.height * sy, r.content_type)
        for r in rects
    ]


def drop_words_in_rects(
    words: list[WordBox],
    rects: list[LayoutRect],
    page_width: float,
    page_height: float,
) -> list[WordBox]:
    if not words or not rects:
        return words
    pad_x = page_width * WORD_IN_BOX_PAD_RATIO
    pad_y = page_height * WORD_IN_BOX_PAD_RATIO

    def inside(word: WordBox, rect: LayoutRect) -> bool:
        return (
            rect.x - pad_x <= word.cx <= rect.x2 + pad_x
            and rect.y - pad_y <= word.cy <= rect.y2 + pad_y
        )

    return [w for w in words if not any(inside(w, rect) for rect in rects)]


def _join_line(words: list[WordBox]) -> str:
    parts: list[str] = []
    last = ""
    for word in words:
        text = word.text
        if not text:
            continue
        if not parts:
            parts.append(text)
        elif text in _NO_SPACE_BEFORE or last in _NO_SPACE_AFTER:
            parts.append(text)
        else:
            parts.append(" " + text)
        last = text
    return "".join(parts)


def _line_y_at(line: list[WordBox], x: float) -> float:
    """Ước lượng y của dòng tại hoành độ x (bù trang scan lệch / cong gáy)."""
    if len(line) == 1:
        return line[0].cy
    n = min(len(line), 8)
    tail = line[-n:]
    mean_x = sum(w.cx for w in tail) / n
    mean_y = sum(w.cy for w in tail) / n
    denom = sum((w.cx - mean_x) ** 2 for w in tail)
    if denom < 1.0:
        return mean_y
    slope = sum((w.cx - mean_x) * (w.cy - mean_y) for w in tail) / denom
    slope = max(-MAX_LINE_SLOPE, min(MAX_LINE_SLOPE, slope))
    return mean_y + slope * (x - mean_x)


def group_lines(items: list[WordBox], page_height: float) -> list[list[WordBox]]:
    """Gom dòng trái → phải để chịu được trang scan nghiêng hoặc cong ở gáy."""
    if not items:
        return []
    remaining = sorted(items, key=lambda w: (w.xmin, w.ymin))
    min_tol = max(page_height * LINE_Y_TOL_MIN_RATIO, 0.0)
    lines: list[list[WordBox]] = []
    while remaining:
        line = [remaining.pop(0)]
        idx = 0
        while idx < len(remaining):
            last = line[-1]
            word = remaining[idx]
            if word.xmin + 1.0 < last.xmin:
                idx += 1
                continue
            med_h = sum(w.h for w in line) / len(line) or 16.0
            tol = max(med_h * LINE_Y_TOL_RATIO, min_tol)
            if abs(word.cy - _line_y_at(line, word.cx)) <= tol:
                line.append(remaining.pop(idx))
                continue
            idx += 1
        lines.append(line)
    lines.sort(key=lambda line: sum(w.cy for w in line) / len(line))
    return [sorted(line, key=lambda w: w.xmin) for line in lines]


def lines_to_text(lines: list[list[WordBox]]) -> str:
    return "\n".join(_join_line(line) for line in lines if line)


def _starts_with_upper(line: str) -> bool:
    for ch in line:
        if ch.isspace():
            continue
        return ch.isupper()
    return False


def _ends_with_period(line: str) -> bool:
    text = line.rstrip()
    while text and text[-1] in "\"')”’»]:":
        text = text[:-1]
    return bool(text) and text.endswith(".")


def reflow_layout_lines(text: str) -> str:
    """Ghép dòng ngắt theo layout. Chỉ xuống dòng khi dòng mới viết hoa hoặc dòng trên đã hết câu (.)."""
    if not (text or "").strip():
        return ""
    paragraphs: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if buf:
                paragraphs.append(buf)
                buf = ""
            continue
        if not buf:
            buf = line
            continue
        if _ends_with_period(buf) or _starts_with_upper(line):
            paragraphs.append(buf)
            buf = line
            continue
        if buf.endswith("-") and line[:1].islower():
            buf = buf[:-1] + line
        else:
            buf = f"{buf} {line}"
    if buf:
        paragraphs.append(buf)
    return "\n".join(paragraphs)


def _x_centers(words: list[WordBox], x0: float, x1: float, n_bins: int) -> list[int]:
    """Histogram tâm chữ — khe hẹp giữa hai cột vẫn hiện, không bị từ dài lấp."""
    width = x1 - x0
    counts = [0] * n_bins
    if width <= 0:
        return counts
    for word in words:
        if word.cx < x0 or word.cx >= x1:
            continue
        i = int((word.cx - x0) / width * n_bins)
        counts[min(n_bins - 1, max(0, i))] += 1
    return counts


def _content_width(words: list[WordBox]) -> float:
    if not words:
        return 0.0
    return max(w.xmax for w in words) - min(w.xmin for w in words)


def _is_newspaper_split(left: list[WordBox], right: list[WordBox], page_width: float) -> bool:
    min_w = page_width * COLUMN_MIN_CONTENT_RATIO
    return _content_width(left) >= min_w and _content_width(right) >= min_w


def detect_column_split_in_span(
    words: list[WordBox],
    x0: float,
    x1: float,
    page_width: float,
    page_height: float = 0.0,
) -> float | None:
    """Một khe cột trong đoạn [x0, x1], hoặc None."""
    width = x1 - x0
    if width < page_width * COLUMN_MIN_WIDTH_RATIO or len(words) < COLUMN_MIN_WORDS * 2:
        return None

    n_bins = max(COLUMN_COVERAGE_BINS, int(width / COLUMN_CENTER_BIN_PX))
    coverage = _x_centers(words, x0, x1, n_bins)
    if not any(coverage):
        return None
    min_n = max(COLUMN_MIN_WORDS, int(len(words) * COLUMN_MIN_SHARE))
    lo = int(n_bins * COLUMN_MID_LO)
    hi = int(n_bins * COLUMN_MID_HI)
    bin_w = width / n_bins
    best_split: float | None = None
    best_score = 0.0
    i = lo
    while i <= hi:
        if coverage[i] != 0:
            i += 1
            continue
        j = i
        while j <= hi and coverage[j] == 0:
            j += 1
        mid = (i + j - 1) / 2.0
        candidate = x0 + (mid + 0.5) / n_bins * width
        gap_px = max(bin_w, (j - i) * bin_w)
        left = [w for w in words if w.cx < candidate]
        right = [w for w in words if w.cx >= candidate]
        if (
            len(left) >= min_n
            and len(right) >= min_n
            and _is_newspaper_split(left, right, page_width)
        ):
            score = gap_px * (len(left) * len(right)) ** 0.5
            if score > best_score:
                best_score = score
                best_split = candidate
        i = j
    return best_split


def detect_column_split(
    words: list[WordBox],
    page_width: float,
    page_height: float = 0.0,
) -> float | None:
    """Khe cột mạnh nhất trên cả trang (tương thích cũ)."""
    return detect_column_split_in_span(words, 0.0, page_width, page_width, page_height)


def detect_column_splits(
    words: list[WordBox],
    page_width: float,
    page_height: float = 0.0,
) -> list[float]:
    """Mọi khe cột, kể cả trang scan 2 tờ × 2 cột. Đệ quy trái → phải."""
    found: list[float] = []

    def walk(subset: list[WordBox], x0: float, x1: float) -> None:
        split = detect_column_split_in_span(subset, x0, x1, page_width, page_height)
        if split is None:
            return
        found.append(split)
        walk([w for w in subset if w.cx < split], x0, split)
        walk([w for w in subset if w.cx >= split], split, x1)

    walk(words, 0.0, page_width)
    return sorted(found)


def reconstruct_text(
    words: list[WordBox],
    page_width: float,
    page_height: float,
    exclude_rects: list[LayoutRect] | None = None,
) -> str:
    """Từng cột trái → phải. Xoá chữ nằm trong box header/footer (unknown ở mép)."""
    usable = [w for w in words if (w.text or "").strip()]
    usable = drop_words_in_rects(usable, exclude_rects or [], page_width, page_height)
    if not usable or page_width <= 0 or page_height <= 0:
        return ""

    splits = detect_column_splits(usable, page_width, page_height)
    bounds = [0.0] + splits + [page_width]
    regions: list[list[WordBox]] = []
    for i, (start, end) in enumerate(zip(bounds, bounds[1:])):
        if i == len(bounds) - 2:
            col = [w for w in usable if w.cx >= start]
        else:
            col = [w for w in usable if start <= w.cx < end]
        if col:
            regions.append(col)

    sections: list[str] = []
    for region in regions:
        text = lines_to_text(group_lines(region, page_height)).strip()
        if text:
            sections.append(text)
    return reflow_layout_lines("\n".join(sections))


def _vertices_bounds(vertices) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for v in vertices or []:
        xs.append(float(getattr(v, "x", 0) or 0))
        ys.append(float(getattr(v, "y", 0) or 0))
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def words_from_gcv_page(page) -> tuple[list[WordBox], float, float]:
    width = float(getattr(page, "width", 0) or 0)
    height = float(getattr(page, "height", 0) or 0)
    words: list[WordBox] = []
    for block in getattr(page, "blocks", None) or []:
        for para in getattr(block, "paragraphs", None) or []:
            for word in getattr(para, "words", None) or []:
                text = "".join(
                    getattr(sym, "text", "") or ""
                    for sym in getattr(word, "symbols", None) or []
                ).strip()
                if not text:
                    continue
                box = getattr(word, "bounding_box", None)
                bounds = _vertices_bounds(getattr(box, "vertices", None) if box else None)
                if bounds is None:
                    continue
                words.append(WordBox(text, *bounds))
    return words, width, height


def text_from_gcv_response(
    response,
    exclude_rects: list[LayoutRect] | None = None,
    box_space_size: tuple[float, float] | None = None,
) -> str:
    """Text Vision theo toạ độ; fallback chuỗi sẵn của API."""
    annotation = getattr(response, "full_text_annotation", None)
    if annotation is not None:
        parts: list[str] = []
        for page in getattr(annotation, "pages", None) or []:
            words, width, height = words_from_gcv_page(page)
            words, width, height = words_in_box_space(words, width, height, box_space_size)
            if words and width > 0 and height > 0:
                parts.append(reconstruct_text(words, width, height, exclude_rects=exclude_rects))
        if parts:
            return "\n\n".join(p for p in parts if p).strip()
        api_text = (getattr(annotation, "text", None) or "").strip()
        if api_text:
            return api_text

    annotations = getattr(response, "text_annotations", None) or []
    if annotations:
        return (getattr(annotations[0], "description", None) or "").strip()
    return ""


def words_from_paddle_result(result) -> list[WordBox]:
    words: list[WordBox] = []
    for block in result or []:
        for line in block or []:
            if not line or len(line) < 2 or not line[1]:
                continue
            text = str(line[1][0]).strip()
            if not text:
                continue
            box = line[0] or []
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            if not xs or not ys:
                continue
            words.append(WordBox(text, min(xs), min(ys), max(xs), max(ys)))
    return words
