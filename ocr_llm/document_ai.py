"""Google Cloud Document AI – Enterprise OCR & Layout Parser."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

from google.api_core.client_options import ClientOptions
from google.cloud import documentai


OCR_PROCESSOR_TYPE = "OCR_PROCESSOR"
LAYOUT_PROCESSOR_TYPE = "LAYOUT_PARSER_PROCESSOR"

# Layout Parser gắn nhãn header/footer — không tự loại, ta lọc khi trích text
_SKIP_LAYOUT_TEXT_TYPES = frozenset({"header", "footer"})

# Dòng thường gặp ở footer sách scan / UI đọc sách (không phải nội dung sách)
_NOISE_LINE_PATTERNS = (
    re.compile(r"^-?\s*\d+\s*$"),  # -42, 42
    re.compile(r"^tìm\s+sách\.?$", re.I),
    re.compile(r"^digitized\s+by\s+google\.?$", re.I),
    re.compile(r"^[-–—]\s*$"),
)

# Khớp Google Cloud Vision — sách YHCT: Việt + Hán phồn thể.
OCR_LANGUAGE_HINTS = ("vi", "zh", "zh-Hant")


@dataclass
class DocumentAIConfig:
    project_id: str
    location: str = "us"
    ocr_processor_id: str = ""
    layout_processor_id: str = ""
    credentials_path: Optional[str] = None
    language_hints: tuple[str, ...] = OCR_LANGUAGE_HINTS


def _apply_credentials(credentials_path: Optional[str]) -> None:
    if credentials_path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path


def _client(cfg: DocumentAIConfig) -> documentai.DocumentProcessorServiceClient:
    _apply_credentials(cfg.credentials_path)
    endpoint = f"{cfg.location}-documentai.googleapis.com"
    return documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(api_endpoint=endpoint)
    )


def _processor_resource_name(cfg: DocumentAIConfig, processor_id: str) -> str:
    return _client(cfg).processor_path(cfg.project_id, cfg.location, processor_id)


def list_processors(cfg: DocumentAIConfig) -> list[documentai.Processor]:
    client = _client(cfg)
    parent = client.common_location_path(cfg.project_id, cfg.location)
    return list(client.list_processors(parent=parent))


def find_processor_id(cfg: DocumentAIConfig, processor_type: str) -> Optional[str]:
    for proc in list_processors(cfg):
        if proc.type_ == processor_type and proc.state == documentai.Processor.State.ENABLED:
            return proc.name.split("/")[-1]
    return None


def create_processor(cfg: DocumentAIConfig, *, display_name: str, processor_type: str) -> documentai.Processor:
    client = _client(cfg)
    parent = client.common_location_path(cfg.project_id, cfg.location)
    return client.create_processor(
        parent=parent,
        processor=documentai.Processor(display_name=display_name, type_=processor_type),
    )


def ensure_processors(cfg: DocumentAIConfig) -> tuple[str, str]:
    """Trả về (ocr_processor_id, layout_processor_id), tạo mới nếu chưa có."""
    ocr_id = (cfg.ocr_processor_id or "").strip() or find_processor_id(cfg, OCR_PROCESSOR_TYPE)
    layout_id = (cfg.layout_processor_id or "").strip() or find_processor_id(cfg, LAYOUT_PROCESSOR_TYPE)

    if not ocr_id:
        proc = create_processor(cfg, display_name="book-ocr-enterprise", processor_type=OCR_PROCESSOR_TYPE)
        ocr_id = proc.name.split("/")[-1]
    if not layout_id:
        proc = create_processor(cfg, display_name="book-layout-parser", processor_type=LAYOUT_PROCESSOR_TYPE)
        layout_id = proc.name.split("/")[-1]

    return ocr_id, layout_id


def _block_kind(block) -> str:
    """oneof `block`: text_block | table_block | list_block | image_block."""
    pb = getattr(block, "_pb", None)
    if pb is not None:
        kind = pb.WhichOneof("block")
        if kind:
            return kind
    for name in ("table_block", "list_block", "image_block", "text_block"):
        if getattr(block, name, None):
            return name
    return ""


def _layout_block_to_text(block) -> str:
    """Trích text đệ quy từ DocumentLayoutBlock (text/table/list lồng nhau)."""
    if block is None:
        return ""

    kind = _block_kind(block)
    if kind == "table_block":
        return _table_to_markdown(block.table_block)
    if kind == "list_block":
        parts: list[str] = []
        for entry in getattr(block.list_block, "list_entries", []) or []:
            for child in getattr(entry, "blocks", []) or []:
                child_text = _layout_block_to_text(child)
                if child_text:
                    parts.append(child_text)
        return "\n".join(parts).strip()
    if kind == "image_block":
        return ""
    if kind != "text_block":
        return ""

    text_block = block.text_block
    type_ = (getattr(text_block, "type_", None) or "").strip().lower()
    if type_ in _SKIP_LAYOUT_TEXT_TYPES:
        return ""
    parts: list[str] = []
    if getattr(text_block, "text", None):
        parts.append(str(text_block.text).strip())
    for child in getattr(text_block, "blocks", []) or []:
        child_text = _layout_block_to_text(child)
        if child_text:
            parts.append(child_text)
    return "\n".join(parts).strip()


def _text_from_layout_block(block) -> str:
    return _layout_block_to_text(block)


def _flatten_cell_text(text: str) -> str:
    """Giữ cell trên một hàng markdown — newline trong cell sẽ phá bảng."""
    cleaned = re.sub(r"\s+", " ", (text or "").replace("|", "\\|")).strip()
    return cleaned


def _table_to_markdown(table_block) -> str:
    rows_md: list[str] = []

    def row_cells(row) -> list[str]:
        cells: list[str] = []
        for cell in getattr(row, "cells", []) or []:
            parts: list[str] = []
            for nested in getattr(cell, "blocks", []) or []:
                t = _layout_block_to_text(nested)
                if t:
                    parts.append(t)
            cells.append(_flatten_cell_text(" ".join(parts)))
        return cells

    for row in getattr(table_block, "header_rows", []) or []:
        cells = row_cells(row)
        if any(cells):
            rows_md.append("| " + " | ".join(cells) + " |")
            rows_md.append("| " + " | ".join("---" for _ in cells) + " |")

    body = [
        cells
        for cells in (row_cells(r) for r in getattr(table_block, "body_rows", []) or [])
        if any(cells)
    ]
    if body and not rows_md:
        rows_md.append("| " + " | ".join("---" for _ in body[0]) + " |")
    for cells in body:
        rows_md.append("| " + " | ".join(cells) + " |")

    return "\n".join(rows_md).strip()


def _page_tables_to_markdown(document: documentai.Document) -> str:
    """Bảng từ page.tables (Form Parser / OCR) — mỗi hàng giữ nguyên số cột."""
    chunks: list[str] = []
    for page in getattr(document, "pages", []) or []:
        for table in getattr(page, "tables", []) or []:
            rows_md: list[str] = []

            def row_cells(row) -> list[str]:
                cells: list[str] = []
                for cell in getattr(row, "cells", []) or []:
                    layout = getattr(cell, "layout", None)
                    text = ""
                    if layout and getattr(layout, "text_anchor", None):
                        text = _text_from_anchor(document, layout.text_anchor)
                    cells.append(_flatten_cell_text(text))
                return cells

            for row in getattr(table, "header_rows", []) or []:
                cells = row_cells(row)
                if any(cells):
                    rows_md.append("| " + " | ".join(cells) + " |")
                    rows_md.append("| " + " | ".join("---" for _ in cells) + " |")
            body = [
                cells
                for cells in (row_cells(r) for r in getattr(table, "body_rows", []) or [])
                if any(cells)
            ]
            if body and not rows_md:
                rows_md.append("| " + " | ".join("---" for _ in body[0]) + " |")
            for cells in body:
                rows_md.append("| " + " | ".join(cells) + " |")
            md = "\n".join(rows_md).strip()
            if md:
                chunks.append(md)
    return "\n\n".join(chunks).strip()


def _looks_like_markdown_table(text: str) -> bool:
    pipe_rows = [ln for ln in (text or "").splitlines() if ln.strip().startswith("|")]
    return len(pipe_rows) >= 2


def _blocks_to_text(blocks) -> list[str]:
    chunks: list[str] = []
    for block in blocks or []:
        text = _layout_block_to_text(block)
        if text:
            chunks.append(text)
    return chunks


def _text_from_chunked_document(document: documentai.Document) -> str:
    chunked = getattr(document, "chunked_document", None)
    if not chunked:
        return ""
    parts: list[str] = []
    for chunk in getattr(chunked, "chunks", []) or []:
        content = getattr(chunk, "content", None)
        if content:
            parts.append(str(content).strip())
    return "\n\n".join(parts).strip()


def _text_from_anchor(document: documentai.Document, text_anchor: documentai.Document.TextAnchor) -> str:
    if not text_anchor.text_segments:
        return ""
    parts: list[str] = []
    for seg in text_anchor.text_segments:
        start = int(seg.start_index or 0)
        end = int(seg.end_index or 0)
        parts.append(document.text[start:end])
    return "".join(parts).strip()


def _rect_from_poly(poly) -> tuple[float, float, float, float] | None:
    """(x0, y0, x1, y1) chuẩn hóa 0–1. None nếu không có / tọa độ pixel."""
    if poly is None:
        return None
    verts = list(getattr(poly, "normalized_vertices", None) or [])
    if not verts:
        verts = list(getattr(poly, "vertices", None) or [])
    if not verts:
        return None
    xs = [float(v.x) for v in verts if getattr(v, "x", None) is not None]
    ys = [float(v.y) for v in verts if getattr(v, "y", None) is not None]
    if not xs or not ys:
        return None
    if max(xs) > 1.5 or max(ys) > 1.5:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _block_y_center(layout) -> float | None:
    """Tọa độ Y trung tâm (0–1) của một layout element."""
    if layout is None:
        return None
    rect = _rect_from_poly(getattr(layout, "bounding_poly", None))
    if rect is None:
        return None
    return (rect[1] + rect[3]) / 2.0


def _center_in_rect(item: dict, table: dict, *, pad: float = 0.012) -> bool:
    if not table.get("has_bbox"):
        return False
    cx = (item["x0"] + item["x1"]) / 2.0
    cy = (item["y0"] + item["y1"]) / 2.0
    return (
        table["x0"] - pad <= cx <= table["x1"] + pad
        and table["y0"] - pad <= cy <= table["y1"] + pad
    )


def _collect_layout_tables(blocks, out: list[dict]) -> None:
    for block in blocks or []:
        kind = _block_kind(block)
        if kind == "table_block":
            md = _table_to_markdown(block.table_block)
            if not md:
                continue
            rect = _rect_from_poly(getattr(block, "bounding_box", None))
            out.append(
                {
                    "x0": rect[0] if rect else 0.0,
                    "y0": rect[1] if rect else 0.999,
                    "x1": rect[2] if rect else 1.0,
                    "y1": rect[3] if rect else 1.0,
                    "has_bbox": rect is not None,
                    "md": md,
                }
            )
            continue
        if kind == "text_block":
            _collect_layout_tables(getattr(block.text_block, "blocks", None), out)
        elif kind == "list_block":
            for entry in getattr(block.list_block, "list_entries", []) or []:
                _collect_layout_tables(getattr(entry, "blocks", None), out)


def _layout_tables_with_bbox(document: documentai.Document) -> list[dict]:
    out: list[dict] = []
    layout = getattr(document, "document_layout", None)
    if layout:
        _collect_layout_tables(getattr(layout, "blocks", None), out)
    return out


def _ocr_paragraphs_with_bbox(document: documentai.Document) -> list[dict]:
    items: list[dict] = []
    for page in getattr(document, "pages", []) or []:
        elements = list(getattr(page, "paragraphs", None) or [])
        if not elements:
            elements = list(getattr(page, "lines", None) or [])
        for el in elements:
            layout = getattr(el, "layout", None)
            if not layout or not getattr(layout, "text_anchor", None):
                continue
            text = _text_from_anchor(document, layout.text_anchor)
            if not text:
                continue
            rect = _rect_from_poly(getattr(layout, "bounding_poly", None))
            items.append(
                {
                    "x0": rect[0] if rect else 0.0,
                    "y0": rect[1] if rect else 0.0,
                    "x1": rect[2] if rect else 1.0,
                    "y1": rect[3] if rect else 0.0,
                    "text": text,
                }
            )
    items.sort(key=lambda p: (p["y0"], p["x0"]))
    return items


def merge_layout_tables_with_ocr_text(
    layout_doc: documentai.Document,
    ocr_doc: documentai.Document | None,
    exclude_rects=None,
    box_space_size: tuple[float, float] | None = None,
) -> str:
    """
    Layout Parser → bảng markdown; Enterprise OCR → chữ theo thứ tự đọc.
    Bỏ dòng OCR nằm trong vùng bảng để không nhân đôi / làm loạn hàng.
    """
    tables = _layout_tables_with_bbox(layout_doc)
    if ocr_doc is None:
        return extract_document_text(
            layout_doc, mode="layout", exclude_rects=exclude_rects, box_space_size=box_space_size
        )

    paras = _ocr_paragraphs_with_bbox(ocr_doc)
    if not tables:
        return extract_document_text(
            ocr_doc, mode="standard", exclude_rects=exclude_rects, box_space_size=box_space_size
        )
    if not paras:
        layout_only = extract_document_text(
            layout_doc, mode="layout", exclude_rects=exclude_rects, box_space_size=box_space_size
        )
        ocr_only = extract_document_text(
            ocr_doc, mode="standard", exclude_rects=exclude_rects, box_space_size=box_space_size
        )
        return layout_only if _looks_like_markdown_table(layout_only) else ocr_only

    tables_sorted = sorted(tables, key=lambda t: (t["y0"], t["x0"]))
    ti = 0
    parts: list[str] = []
    for para in paras:
        while ti < len(tables_sorted) and tables_sorted[ti]["y0"] <= para["y0"] + 0.02:
            parts.append(tables_sorted[ti]["md"])
            ti += 1
        if any(_center_in_rect(para, t) for t in tables):
            continue
        parts.append(para["text"])
    while ti < len(tables_sorted):
        parts.append(tables_sorted[ti]["md"])
        ti += 1

    logger.info(
        "Hybrid OCR: %s bảng Layout Parser + %s đoạn Enterprise OCR → %s khối",
        len(tables),
        len(paras),
        len(parts),
    )
    return _postprocess_ocr_text("\n\n".join(p for p in parts if p and p.strip()))


def _extract_margin_filtered_text(document: documentai.Document) -> str:
    """Enterprise OCR: lấy dòng theo thứ tự API (không cắt mép 7%/6%)."""
    if not getattr(document, "pages", None):
        return ""

    lines_out: list[str] = []
    for page in document.pages:
        for line in getattr(page, "lines", []) or []:
            layout = getattr(line, "layout", None)
            if layout and getattr(layout, "text_anchor", None):
                text = _text_from_anchor(document, layout.text_anchor)
                if text:
                    lines_out.append(text)

    return "\n".join(lines_out).strip()


def _strip_noise_lines(text: str) -> str:
    """Loại dòng rác: số trang đơn lẻ, 'Tìm sách', v.v."""
    if not text:
        return ""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and any(p.match(stripped) for p in _NOISE_LINE_PATTERNS):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


_SENTENCE_END_RE = re.compile(r'[.!?。．]["\'")\]]*\s*$')
_TABLE_LINE_RE = re.compile(r"^\s*\|")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _is_cjk_char(ch: str) -> bool:
    return bool(ch and _CJK_RE.match(ch))


def _needs_space_between(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if _is_cjk_char(left[-1]) and _is_cjk_char(right[0]):
        return False
    if left.endswith("-"):
        return False
    return True


def _join_line_parts(parts: list[str]) -> str:
    if not parts:
        return ""
    out = parts[0].strip()
    for part in parts[1:]:
        p = part.strip()
        if not p:
            continue
        if out.endswith("-") and p and p[0].islower():
            out = out[:-1] + p
        elif _needs_space_between(out, p):
            out = f"{out} {p}"
        else:
            out = out + p
    return out.strip()


def _is_standalone_header(line: str) -> bool:
    """Tiêu đề/mục đơn dòng — không ghép với đoạn trước/sau."""
    if not line:
        return False
    if line.endswith(":") and len(line) < 120:
        return True
    if _SENTENCE_END_RE.search(line):
        return False
    if len(line) <= 35 and "," not in line and ";" not in line:
        words = line.split()
        if line[0].isupper() and len(words) <= 5:
            return True
    if len(line) < 50:
        upper_ratio = sum(1 for c in line if c.isupper()) / max(len(line.replace(" ", "")), 1)
        if upper_ratio > 0.7 and any(c.isalpha() for c in line):
            return True
    return False


def _reflow_plain_text(text: str) -> str:
    """
    Ghép dòng xuống dòng giữa câu (do layout sách) thành đoạn liền mạch.
    Đoạn kết thúc khi gặp dòng có dấu '.' (hoặc ! ?).
    """
    paragraphs: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            paragraphs.append(_join_line_parts(buf))
            buf.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue

        if _is_standalone_header(line):
            flush()
            paragraphs.append(line)
            continue

        buf.append(line)
        if _SENTENCE_END_RE.search(line):
            flush()

    flush()
    return "\n\n".join(paragraphs)


def _split_table_blocks(text: str) -> list[tuple[str, str]]:
    """Tách khối bảng markdown (| ...) để không reflow phá cấu trúc bảng."""
    blocks: list[tuple[str, str]] = []
    buf: list[str] = []
    in_table = False

    for raw in text.splitlines():
        is_table = bool(_TABLE_LINE_RE.match(raw))
        if is_table:
            if not in_table and buf:
                blocks.append(("text", "\n".join(buf)))
                buf = []
            in_table = True
            buf.append(raw)
        else:
            if in_table and buf:
                blocks.append(("table", "\n".join(buf)))
                buf = []
            in_table = False
            buf.append(raw)

    if buf:
        blocks.append(("table" if in_table else "text", "\n".join(buf)))
    return blocks


def reflow_ocr_paragraphs(text: str) -> str:
    if not text or not text.strip():
        return ""

    parts: list[str] = []
    for kind, block in _split_table_blocks(text):
        block = block.strip()
        if not block:
            continue
        if kind == "table":
            parts.append(block)
        else:
            reflowed = _reflow_plain_text(block)
            if reflowed:
                parts.append(reflowed)
    return "\n\n".join(parts)


def postprocess_ocr_text(text: str) -> str:
    """Hậu xử lý OCR: lọc rác + ghép dòng thành đoạn."""
    text = _strip_noise_lines(text)
    return reflow_ocr_paragraphs(text)


def _postprocess_ocr_text(text: str) -> str:
    return postprocess_ocr_text(text)


def _page_size(page) -> tuple[float, float]:
    dim = getattr(page, "dimension", None)
    width = float(getattr(dim, "width", 0) or 0)
    height = float(getattr(dim, "height", 0) or 0)
    return (width or 1.0, height or 1.0)


def _target_page_size(
    page,
    box_space_size: tuple[float, float] | None,
) -> tuple[float, float, float, float]:
    """(target_w, target_h, source_w, source_h). target = ảnh YOLO khi có."""
    source_w, source_h = _page_size(page)
    if box_space_size and box_space_size[0] > 0 and box_space_size[1] > 0:
        return float(box_space_size[0]), float(box_space_size[1]), source_w, source_h
    return source_w, source_h, source_w, source_h


def _layout_to_wordbox(
    document: documentai.Document,
    layout,
    target_width: float,
    target_height: float,
    source_width: float,
    source_height: float,
):
    """Token DocAI → pixel cùng hệ với bbox YOLO (normalized × ảnh detect)."""
    from ocr_llm.reading_order import WordBox

    if layout is None or not getattr(layout, "text_anchor", None):
        return None
    text = _text_from_anchor(document, layout.text_anchor).strip()
    if not text:
        return None
    poly = getattr(layout, "bounding_poly", None)
    if poly is None:
        return None
    norm = list(getattr(poly, "normalized_vertices", None) or [])
    if norm:
        xs = [float(getattr(v, "x", 0) or 0) for v in norm]
        ys = [float(getattr(v, "y", 0) or 0) for v in norm]
        if xs and ys:
            return WordBox(
                text,
                min(xs) * target_width,
                min(ys) * target_height,
                max(xs) * target_width,
                max(ys) * target_height,
            )
    verts = list(getattr(poly, "vertices", None) or [])
    if verts:
        xs = [float(getattr(v, "x", 0) or 0) for v in verts]
        ys = [float(getattr(v, "y", 0) or 0) for v in verts]
        if xs and ys:
            sx = target_width / source_width if source_width else 1.0
            sy = target_height / source_height if source_height else 1.0
            return WordBox(text, min(xs) * sx, min(ys) * sy, max(xs) * sx, max(ys) * sy)
    return None


def _reconstruct_document_text(
    document: documentai.Document,
    exclude_rects=None,
    box_space_size: tuple[float, float] | None = None,
) -> str:
    """Dựng text Document AI theo toạ độ từ (y rồi x), tách cột nếu có."""
    from ocr_llm.reading_order import reconstruct_text

    pages_out: list[str] = []
    for page in getattr(document, "pages", None) or []:
        target_w, target_h, source_w, source_h = _target_page_size(page, box_space_size)
        if box_space_size and (abs(target_w - source_w) > 1 or abs(target_h - source_h) > 1):
            logger.info(
                "DocAI toạ độ: page.dimension=%.1fx%.1f → ảnh YOLO=%.1fx%.1f",
                source_w,
                source_h,
                target_w,
                target_h,
            )
        words = []
        for token in getattr(page, "tokens", None) or []:
            box = _layout_to_wordbox(
                document, getattr(token, "layout", None), target_w, target_h, source_w, source_h
            )
            if box:
                words.append(box)
        if not words:
            for line in getattr(page, "lines", None) or []:
                box = _layout_to_wordbox(
                    document, getattr(line, "layout", None), target_w, target_h, source_w, source_h
                )
                if box:
                    words.append(box)
        if words:
            pages_out.append(
                reconstruct_text(words, target_w, target_h, exclude_rects=exclude_rects)
            )
    return "\n\n".join(p for p in pages_out if p).strip()


def extract_document_text(
    document: documentai.Document,
    *,
    mode: str = "standard",
    exclude_rects=None,
    box_space_size: tuple[float, float] | None = None,
) -> str:
    """Gom nguồn text. Layout Parser ưu tiên document_layout (giữ hàng/cột bảng)."""
    layout_text = ""
    layout = getattr(document, "document_layout", None)
    if layout and getattr(layout, "blocks", None):
        layout_text = "\n\n".join(_blocks_to_text(layout.blocks)).strip()

    page_tables = _page_tables_to_markdown(document)
    mode = (mode or "standard").lower()

    # Layout Parser: không lấy bản OCR dòng (mỗi cell = 1 dòng) chỉ vì dài hơn.
    if mode == "layout" and _looks_like_markdown_table(layout_text):
        return _postprocess_ocr_text(layout_text)
    if mode == "layout" and _looks_like_markdown_table(page_tables):
        return _postprocess_ocr_text(page_tables)
    if mode == "layout" and layout_text:
        return _postprocess_ocr_text(layout_text)

    coord_text = _reconstruct_document_text(
        document, exclude_rects=exclude_rects, box_space_size=box_space_size
    )
    if coord_text:
        return coord_text

    candidates: list[str] = []
    if layout_text:
        candidates.append(layout_text)
    if page_tables:
        candidates.append(page_tables)

    margin_text = _extract_margin_filtered_text(document)
    if margin_text:
        candidates.append(margin_text)

    chunked_text = _text_from_chunked_document(document)
    if chunked_text:
        candidates.append(chunked_text)

    if (document.text or "").strip():
        candidates.append(document.text.strip())

    anchor_chunks: list[str] = []
    if getattr(document, "pages", None):
        for page in document.pages:
            for paragraph in getattr(page, "paragraphs", []) or []:
                layout_el = getattr(paragraph, "layout", None)
                if layout_el and getattr(layout_el, "text_anchor", None):
                    anchor_chunks.append(_text_from_anchor(document, layout_el.text_anchor))
    if anchor_chunks:
        candidates.append("\n\n".join(c for c in anchor_chunks if c).strip())

    if not candidates:
        return ""

    cleaned = [_postprocess_ocr_text(c) for c in candidates if c.strip()]
    if not cleaned:
        return ""
    with_tables = [c for c in cleaned if _looks_like_markdown_table(c)]
    if with_tables:
        return max(with_tables, key=len)
    return max(cleaned, key=len)


def _process_raw(
    cfg: DocumentAIConfig,
    content: bytes,
    *,
    processor_id: str,
    mime_type: str,
    layout_options: bool = False,
) -> documentai.Document:
    client = _client(cfg)
    ocr_config = documentai.OcrConfig(
        hints=documentai.OcrConfig.Hints(
            language_hints=list(cfg.language_hints or OCR_LANGUAGE_HINTS),
        )
    )
    process_options = documentai.ProcessOptions(ocr_config=ocr_config)
    if layout_options:
        process_options.layout_config = documentai.ProcessOptions.LayoutConfig(
            enable_table_annotation=True,
            enable_image_annotation=True,
            return_bounding_boxes=True,
        )
    request = documentai.ProcessRequest(
        name=_processor_resource_name(cfg, processor_id),
        raw_document=documentai.RawDocument(content=content, mime_type=mime_type),
        process_options=process_options,
    )
    return client.process_document(request=request).document


def process_bytes(
    cfg: DocumentAIConfig,
    content: bytes,
    *,
    mode: str,
    mime_type: str,
    exclude_rects=None,
    box_space_size: tuple[float, float] | None = None,
    ocr_image_bytes: bytes | None = None,
) -> str:
    """
    OCR nội dung trang.
    mode: 'standard' → Enterprise OCR (png/pdf).
          'layout' → Layout Parser (bảng) + Enterprise OCR (chữ), ghép theo vị trí.
    """
    mode = (mode or "standard").lower()
    if mode == "layout" and mime_type != "application/pdf":
        raise ValueError("Layout Parser yêu cầu mime_type=application/pdf")

    ocr_id, layout_id = ensure_processors(cfg)

    if mode == "layout":
        layout_doc = _process_raw(
            cfg,
            content,
            processor_id=layout_id,
            mime_type=mime_type,
            layout_options=True,
        )
        ocr_doc = None
        try:
            if ocr_image_bytes:
                ocr_doc = _process_raw(
                    cfg, ocr_image_bytes, processor_id=ocr_id, mime_type="image/png"
                )
            else:
                ocr_doc = _process_raw(cfg, content, processor_id=ocr_id, mime_type=mime_type)
        except Exception:
            logger.warning("Enterprise OCR phụ thất bại — chỉ dùng Layout Parser", exc_info=True)
        return merge_layout_tables_with_ocr_text(
            layout_doc, ocr_doc, exclude_rects=exclude_rects, box_space_size=box_space_size
        )

    doc = _process_raw(cfg, content, processor_id=ocr_id, mime_type=mime_type)
    return extract_document_text(
        doc, mode="standard", exclude_rects=exclude_rects, box_space_size=box_space_size
    )


def process_image(
    cfg: DocumentAIConfig,
    image_bytes: bytes,
    *,
    mode: str,
    mime_type: str = "image/png",
    exclude_rects=None,
    box_space_size: tuple[float, float] | None = None,
) -> str:
    """OCR ảnh PNG/JPEG – chỉ dùng cho Enterprise OCR."""
    if (mode or "standard").lower() == "layout":
        raise ValueError("Layout Parser không hỗ trợ ảnh PNG; dùng process_page_pdf()")
    if not box_space_size:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        box_space_size = (float(img.size[0]), float(img.size[1]))
    return process_bytes(
        cfg,
        image_bytes,
        mode=mode,
        mime_type=mime_type,
        exclude_rects=exclude_rects,
        box_space_size=box_space_size,
    )


def extract_page_pdf(pdf_bytes: bytes, page_index: int) -> bytes:
    """Trích một trang PDF (0-based) thành file PDF 1 trang (theo CropBox)."""
    import pymupdf

    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    src_page = src.load_page(page_index)
    # CropBox = vùng nội dung thực (tránh nền quét trong MediaBox)
    clip = src_page.cropbox
    if clip.is_empty or clip.width <= 0 or clip.height <= 0:
        clip = src_page.rect

    dst = pymupdf.open()
    dst_page = dst.new_page(width=clip.width, height=clip.height)
    dst_page.show_pdf_page(dst_page.rect, src, page_index, clip=clip)
    out = dst.tobytes()
    src.close()
    dst.close()
    return out


def process_page_pdf(
    cfg: DocumentAIConfig,
    pdf_bytes: bytes,
    *,
    page_index: int,
    mode: str,
    dpi: int = 200,
    standard_format: str = "pdf",
    exclude_rects=None,
    box_space_size: tuple[float, float] | None = None,
    page_image_bytes: bytes | None = None,
) -> str:
    """
    OCR một trang từ PDF gốc.
    standard_format: 'pdf' gửi PDF 1 trang, 'png' render ảnh (dpi).
    """
    mode = (mode or "standard").lower()
    if mode == "layout":
        page_pdf = extract_page_pdf(pdf_bytes, page_index)
        return process_bytes(
            cfg,
            page_pdf,
            mode="layout",
            mime_type="application/pdf",
            exclude_rects=exclude_rects,
            box_space_size=box_space_size,
            ocr_image_bytes=page_image_bytes or None,
        )

    if standard_format == "pdf":
        page_pdf = extract_page_pdf(pdf_bytes, page_index)
        return process_bytes(
            cfg,
            page_pdf,
            mode="standard",
            mime_type="application/pdf",
            exclude_rects=exclude_rects,
            box_space_size=box_space_size,
        )

    from bb_detection.pdf_render import render_page_png

    png, _w, _h = render_page_png(
        pdf_bytes=pdf_bytes,
        page_number=page_index + 1,
        dpi=dpi,
        use_cropbox=True,
    )
    return process_bytes(
        cfg,
        png,
        mode="standard",
        mime_type="image/png",
        exclude_rects=exclude_rects,
        box_space_size=box_space_size,
    )


def ocr_regions_standard(
    cfg: DocumentAIConfig,
    image_bytes: bytes,
    regions: list[tuple[float, float, float, float, str]],
    *,
    dpi_scale: float = 1.0,
) -> str:
    """OCR từng vùng bbox riêng rồi ghép — dùng khi OCR full-page thất bại."""
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img_w, img_h = img.size
    chunks: list[str] = []

    for x, y, width, height, content_type in regions:
        if content_type == "image":
            continue
        x1 = max(0, int(x * dpi_scale))
        y1 = max(0, int(y * dpi_scale))
        x2 = min(img_w, int((x + width) * dpi_scale))
        y2 = min(img_h, int((y + height) * dpi_scale))
        if x2 - x1 < 8 or y2 - y1 < 8:
            continue
        crop = img.crop((x1, y1, x2, y2))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        text = process_image(cfg, buf.getvalue(), mode="standard").strip()
        if text:
            chunks.append(text)

    return "\n\n".join(chunks).strip()


MIN_OCR_CHARS = 80
OCR_DPI_DEFAULT = 200
OCR_DPI_RETRY = 300


class DocumentAIOCR:
    """Facade tương thích OCRProviderBase."""

    def __init__(self, cfg: DocumentAIConfig):
        self.cfg = cfg
        self._pdf_bytes: bytes | None = None

    def set_source_pdf(self, pdf_bytes: bytes) -> None:
        self._pdf_bytes = pdf_bytes

    def ocr_page(
        self,
        image_bytes: bytes,
        *,
        mode: str = "standard",
        page_index: int | None = None,
        dpi: int = OCR_DPI_DEFAULT,
        standard_format: str = "image",
        exclude_rects=None,
        box_space_size: tuple[float, float] | None = None,
    ) -> str:
        mode = (mode or "standard").lower()
        fmt = (standard_format or "image").lower()

        if mode == "layout":
            if self._pdf_bytes is None or page_index is None:
                raise RuntimeError("Layout Parser cần PDF gốc – gọi set_source_pdf() trước")
            return process_page_pdf(
                self.cfg,
                self._pdf_bytes,
                page_index=page_index,
                mode=mode,
                dpi=dpi,
                standard_format=fmt,
                exclude_rects=exclude_rects,
                box_space_size=box_space_size,
                page_image_bytes=image_bytes or None,
            )

        # Trang thường: OCR đúng ảnh YOLO để toạ độ trùng bbox.
        if fmt in {"image", "png"} and image_bytes and dpi == OCR_DPI_DEFAULT:
            return process_image(
                self.cfg,
                image_bytes,
                mode="standard",
                exclude_rects=exclude_rects,
                box_space_size=box_space_size,
            )

        if self._pdf_bytes is not None and page_index is not None:
            return process_page_pdf(
                self.cfg,
                self._pdf_bytes,
                page_index=page_index,
                mode="standard",
                dpi=dpi,
                standard_format="pdf" if fmt == "pdf" else "png",
                exclude_rects=exclude_rects,
                box_space_size=box_space_size,
                page_image_bytes=image_bytes or None,
            )

        if image_bytes:
            return process_image(
                self.cfg,
                image_bytes,
                mode="standard",
                exclude_rects=exclude_rects,
                box_space_size=box_space_size,
            )
        raise RuntimeError("Không có ảnh trang hoặc PDF để OCR Document AI.")

    def ocr_page_with_fallback(
        self,
        image_bytes: bytes,
        *,
        mode: str = "standard",
        page_index: int | None = None,
        regions: list[tuple[float, float, float, float, str]] | None = None,
        exclude_rects=None,
        box_space_size: tuple[float, float] | None = None,
    ) -> tuple[str, str]:
        """
        OCR trang; thử thêm chiến lược khi kết quả quá ngắn, chọn bản dài nhất.
        regions: bbox (x, y, w, h, content_type) để OCR từng vùng khi full-page thất bại.
        """
        if self._pdf_bytes is None or page_index is None:
            text = self.ocr_page(
                image_bytes,
                mode=mode,
                page_index=page_index,
                exclude_rects=exclude_rects,
                box_space_size=box_space_size,
            )
            return text, mode

        attempts: list[tuple[str, str]] = []

        def _try(label: str, fn) -> str:
            try:
                result = fn().strip()
                attempts.append((label, result))
                return result
            except Exception:
                return ""

        def _best() -> tuple[str, str]:
            table_hits = [item for item in attempts if _looks_like_markdown_table(item[1])]
            if table_hits:
                return max(table_hits, key=lambda item: len(item[1]))
            return max(attempts, key=lambda item: len(item[1]))

        _try(
            mode,
            lambda: self.ocr_page(
                image_bytes,
                mode=mode,
                page_index=page_index,
                exclude_rects=exclude_rects,
                box_space_size=box_space_size,
            ),
        )
        label, text = _best()
        # Layout hybrid đã gồm Enterprise OCR + bảng; đủ dài thì không gọi lại OCR dòng.
        if mode == "layout" and (_looks_like_markdown_table(text) or len(text) >= MIN_OCR_CHARS):
            return text, label
        if len(text) >= MIN_OCR_CHARS:
            return text, label

        alt_mode = "standard" if mode == "layout" else "layout"
        _try(
            alt_mode,
            lambda: self.ocr_page(
                image_bytes,
                mode=alt_mode,
                page_index=page_index,
                exclude_rects=exclude_rects,
                box_space_size=box_space_size,
            ),
        )
        label, text = _best()
        if mode == "layout" and _looks_like_markdown_table(text):
            return text, label
        if len(text) >= MIN_OCR_CHARS:
            return text, label

        if "standard-pdf" not in {a[0] for a in attempts}:
            _try(
                "standard-pdf",
                lambda: self.ocr_page(
                    image_bytes,
                    mode="standard",
                    page_index=page_index,
                    standard_format="pdf",
                    exclude_rects=exclude_rects,
                    box_space_size=box_space_size,
                ),
            )
            label, text = _best()
            if len(text) >= MIN_OCR_CHARS:
                return text, label

        _try(
            "standard-png@300",
            lambda: self.ocr_page(
                image_bytes,
                mode="standard",
                page_index=page_index,
                dpi=OCR_DPI_RETRY,
                standard_format="png",
                exclude_rects=exclude_rects,
                box_space_size=box_space_size,
            ),
        )
        label, text = _best()
        if len(text) >= MIN_OCR_CHARS:
            return text, label

        if len(text) < MIN_OCR_CHARS and regions and image_bytes:
            box_text = ocr_regions_standard(self.cfg, image_bytes, regions)
            if len(box_text) > len(text):
                return box_text, "by-boxes"

        return text, label

    def ocr(self, image_bytes: bytes) -> str:
        return self.ocr_page(image_bytes, mode="standard")
