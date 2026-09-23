"""Phân đoạn (chunking) bằng LLM — cửa sổ N trang, LLM chỉ trả chỉ số đoạn."""

from __future__ import annotations

import json
import re

from apps.llm.models import ChunkingConfig, ChunkResizeConfig, LLMProvider
from apps.llm.text_cleaning_runner import get_default_llm, get_text_llm_client
from ocr_llm.prompts import (
    build_window_chunking_prompt,
    default_page_chunking_prompt,
    default_page_stitch_prompt,
)

BREAK_TOKEN = "</break>"
CHUNKING_MAX_TOKENS = 256
CHUNKING_MAX_WINDOW = 20
_LEGACY_STITCH_PROMPTS = {
    (
        "Hai đoạn văn nằm ở ranh giới hai trang sách.\n"
        "Chỉ trả JSON: true hoặc false.\n"
        "true = hai đoạn là một câu/đoạn đang viết tiếp, cần nối.\n"
        "false = đoạn sau là nội dung mới, không nối.\n"
        "Không giải thích, không markdown, không object."
    ),
    (
        "Hai đoạn văn nằm ở ranh giới hai trang sách.\n"
        "Trả đúng một giá trị JSON: true hoặc false.\n"
        "true = hai đoạn đang viết tiếp, cần nối.\n"
        "false = đoạn sau là nội dung mới, không nối.\n"
        "CẤM object, CẤM khóa (is_continuation, join, …), CẤM markdown, CẤM giải thích.\n"
        "Ví dụ đúng: true\n"
        "Ví dụ đúng: false"
    ),
}
_BREAK_RE = re.compile(r"</\s*break\s*>", re.IGNORECASE)
_PAGE_COMMENT_RE = re.compile(r"^<!--\s*Trang\s+(\d+)\s*-->\s*$", re.IGNORECASE)
_PAGE_HEADING_RE = re.compile(r"^##\s*Trang\s+\d+\s*$", re.IGNORECASE)
_LEGACY_SINGLE_PAGE_PROMPT = """
Dưới đây là nội dung của một trang sách y học cổ truyền / dược liệu Việt Nam.

Nếu trong nội dung này có nhiều chỉ mục tách biệt nhau hoàn toàn (ví dụ nhiều bài thuốc, nhiều vị thuốc, nhiều mục từ điển độc lập) thì hãy chèn đúng chuỗi ký tự </break> vào giữa hai nội dung tách biệt nhau. Nếu không có thì không cần làm gì.

Cân nhắc kỹ xem các phần có liên quan / tiếp nối nhau hay không. Chỉ tách khi hai phần độc lập, không phải đoạn đang viết tiếp của cùng một mục.

Quy tắc:
- Không sửa, không tóm tắt, không dịch, không thêm chữ nào khác ngoài </break>.
- Giữ nguyên toàn bộ nội dung, thứ tự, xuống dòng và Markdown.
- Không bọc ```markdown, không giải thích, không lời dẫn.
- Trả về toàn bộ trang (đã chèn </break> nếu cần).
""".strip()
_LEGACY_REWRITE_TWO_PAGE_PROMPT = """
Bạn nhận HAI trang liên tiếp của sách y học cổ truyền / dược liệu Việt Nam (trang trước + trang sau).

Chèn đúng chuỗi </break> giữa các chỉ mục tách biệt hoàn toàn (bài thuốc, vị thuốc, mục từ điển độc lập). Nếu không có thì không chèn.

Đặc biệt chú ý ranh giới hai trang:
- Nếu mục ở cuối trang trước viết tiếp sang đầu trang sau thì KHÔNG tách ở đầu trang sau.
- Nếu mục mới bắt đầu ngay đầu trang sau (trang trước đã kết thúc mục) thì chèn </break> ở ranh giới đó (cuối trang trước và/hoặc đầu trang sau, tùy chỗ ngắt tự nhiên).

Trang trước có thể đã có </break> từ lần gọi trước: giữ các mốc đã đúng, chỉ thêm/bớt ở ranh giới với trang sau nếu cần.

Quy tắc:
- Không sửa, không tóm tắt, không dịch, không thêm chữ nào khác ngoài </break>.
- Giữ nguyên toàn bộ nội dung, thứ tự, xuống dòng và Markdown của từng trang.
- Không bọc ```markdown, không giải thích, không lời dẫn.
- Trả về ĐÚNG hai khối, mỗi khối một trang, theo format:

===== TRANG {số} =====
(toàn bộ nội dung trang đó, đã chèn </break> nếu cần)
""".strip()


_LEGACY_JSON_BOTH_PAGES_PROMPT = """
Bạn nhận các đoạn đã đánh số của HAI trang liên tiếp sách y học cổ truyền / dược liệu Việt Nam.

Chọn vị trí tách giữa các chỉ mục độc lập hoàn toàn (bài thuốc, vị thuốc, mục từ điển). Nếu không có thì không tách.

Đặc biệt chú ý ranh giới hai trang:
- Nếu mục ở cuối trang trước viết tiếp sang đầu trang sau thì KHÔNG tách sau đoạn cuối trang trước.
- Nếu mục mới bắt đầu ngay đầu trang sau thì tách sau đoạn cuối trang trước.

Số trong break_after là số đoạn [k]: </break> sẽ được chèn ngay SAU đoạn đó.

Quy tắc:
- Không sửa, không tóm tắt, không dịch, không xuất lại nội dung.
- Chỉ trả JSON, không markdown, không giải thích:
{"break_after": [2, 5]}
- Không tách: {"break_after": []}
- Các số phải nằm trong danh sách đoạn đã cho, không trùng, tăng dần.
""".strip()


_LEGACY_JSON_LOCK_LEFT_PROMPT = """
Bạn nhận hai trang liên tiếp sách y học cổ truyền / dược liệu Việt Nam, các đoạn cần quyết đã được đánh số [k].

Chọn vị trí tách giữa các chỉ mục độc lập hoàn toàn (bài thuốc, vị thuốc, mục từ điển). Nếu không có thì không tách.

Ranh giới hai trang:
- Mục viết tiếp sang trang sau: KHÔNG tách ở đầu trang sau.
- Mục mới bắt đầu ngay đầu trang sau: tách trước đoạn đầu trang sau (break_after có 0) hoặc sau đoạn cuối trang trước (khi cả hai trang đều đang đánh số).

Số k trong break_after = chèn </break> ngay SAU đoạn [k]. Số 0 = chèn </break> ngay ĐẦU trang đang quyết (trang sau).

Quy tắc:
- Không sửa, không tóm tắt, không dịch, không xuất lại nội dung.
- Chỉ trả JSON, không markdown, không giải thích:
{"break_after": [2, 5]}
- Không tách: {"break_after": []}
- Chỉ trả số đoạn được phép (xem hướng dẫn lần gọi). Không trùng, tăng dần.
""".strip()


_LEGACY_JSON_NUMBERED_SHORT_PROMPT = """
Bạn nhận hai trang liên tiếp sách y học cổ truyền / dược liệu Việt Nam. Các đoạn được đánh số liên tục, bắt đầu từ trang trước: [1], [2], …

Chọn vị trí tách giữa các chỉ mục độc lập hoàn toàn (bài thuốc, vị thuốc, mục từ điển). Nếu không có thì không tách.

Ranh giới hai trang:
- Nếu mục ở cuối trang trước viết tiếp sang đầu trang sau thì KHÔNG tách sau đoạn cuối trang trước.
- Nếu mục mới bắt đầu ngay đầu trang sau thì tách sau đoạn cuối trang trước.

Số k trong break_after = chèn mốc ngay SAU đoạn [k].

Quy tắc:
- Không sửa, không tóm tắt, không dịch, không xuất lại nội dung.
- Chỉ trả JSON, không markdown, không giải thích:
{"break_after": [2, 5]}
- Không tách: {"break_after": []}
- Các số phải nằm trong danh sách đoạn đã cho, không trùng, tăng dần.
""".strip()


_LEGACY_TWO_PAGE_WINDOW_PROMPT = """
Bạn nhận HAI trang liên tiếp sách y học cổ truyền / dược liệu Việt Nam.
Mỗi dòng không rỗng đã được đánh số liên tục từ trang trước: [1], [2], [3], …

Nhiệm vụ: chỉ ra SAU những đoạn nào nên tách thành mục độc lập (bài thuốc, vị thuốc, mục từ điển, phương khác hẳn).

Tách khi:
- Bắt đầu một chỉ mục mới, độc lập với phần trước (tên bài/vị thuốc/mục từ điển mới, đề mục cấp cao mới).
- Trang trước đã hết mục, trang sau mở mục mới → tách sau đoạn cuối trang trước.

KHÔNG tách khi:
- Trang sau đang viết tiếp câu/đoạn/mục của trang trước (cùng bài, cùng vị, cùng mục từ điển).
- Chỉ là đoạn văn, gạch đầu dòng, thành phần, cách dùng, kiêng kỵ… của CÙNG một mục.
- Tiêu đề nhỏ, chú thích, số trang, header/footer.

Quy tắc trả lời:
- Chỉ trả JSON, không markdown, không giải thích, không xuất lại nội dung.
- Có tách: {"break_after": [2, 7, 11]}
- Không tách: {"break_after": []}
- Số trong mảng = số trong ngoặc vuông [k]; nghĩa là tách ngay SAU đoạn [k].
- Chỉ dùng số đã cho, không trùng, tăng dần.
""".strip()


_LEGACY_N_PAGE_WINDOW_PROMPT = """
Bạn nhận các trang liên tiếp sách y học cổ truyền / dược liệu Việt Nam.
Mỗi dòng không rỗng đã được đánh số liên tục từ trang đầu cửa sổ: [1], [2], [3], …

Nhiệm vụ: chỉ ra SAU những đoạn nào nên tách thành mục độc lập (bài thuốc, vị thuốc, mục từ điển, phương khác hẳn).

Tách khi:
- Bắt đầu một chỉ mục mới, độc lập với phần trước (tên bài/vị thuốc/mục từ điển mới, đề mục cấp cao mới).
- Trang trước trong cửa sổ đã hết mục, trang sau mở mục mới → tách sau đoạn cuối trang trước.

KHÔNG tách khi:
- Trang sau đang viết tiếp câu/đoạn/mục của trang trước (cùng bài, cùng vị, cùng mục từ điển).
- Chỉ là đoạn văn, gạch đầu dòng, thành phần, cách dùng, kiêng kỵ… của CÙNG một mục.
- Tiêu đề nhỏ, chú thích, số trang, header/footer.

Quy tắc trả lời:
- Chỉ trả JSON, không markdown, không giải thích, không xuất lại nội dung.
- Có tách: {"break_after": [2, 7, 11]}
- Không tách: {"break_after": []}
- Số trong mảng = số trong ngoặc vuông [k]; nghĩa là tách ngay SAU đoạn [k].
- Chỉ dùng số đã cho, không trùng, tăng dần.
""".strip()


_LEGACY_PAGE_AWARE_PROMPT = """
Bạn nhận các trang liên tiếp sách y học cổ truyền / dược liệu Việt Nam.
Mỗi dòng không rỗng đã được đánh số liên tục từ trang đầu cửa sổ: [1], [2], [3], …

Một MỤC (bài thuốc / phương) là CẢ KHỐI, thường theo thứ tự:
tên phương → vị thuốc → cách sắc/uống → Chủ trị → Nhận xét (có thể thêm gia giảm, kiêng kỵ).
Các phần này thuộc CÙNG một mục, kể cả khi bị cắt sang trang sau.

Tách SAU đoạn [k] chỉ khi đoạn [k+1] mở một mục MỚI, độc lập:
- Tên phương/bài mới: số thứ tự ("7-", "8-"), hoặc tên riêng kiểu ĐẠI BỔ… PHƯƠNG / THANG / HOÀN / TÁN / CAO / ĐƠN.
- Mục từ điển / vị thuốc mới (tên mục khác hẳn, không phải lời giải của mục trước).

TUYỆT ĐỐI KHÔNG tách:
- Trước "Chủ trị", "Nhận xét", "Cách dùng", "Gia giảm", "Kiêng kỵ", "Công dụng", "Chú thích" — đây là phần của phương ngay trước.
- Khi sang trang: trang sau bắt đầu bằng Chủ trị / Nhận xét / tiếp vị thuốc / "Trang …" thì đang viết TIẾP mục trang trước. Không tách sau đoạn cuối trang trước.
- Dòng số trang, header, footer (ví dụ "Trang 14").
- Đoạn văn, gạch đầu dòng, thành phần, sắc uống của CÙNG một phương.

Ví dụ SAI: cắt giữa "8- BỔ ÂM LIỄM DƯƠNG PHƯƠNG" + vị thuốc và "Chủ trị: …" chỉ vì "Chủ trị" nằm ở trang sau.
Ví dụ ĐÚNG: tách sau hết "Nhận xét" của phương 7, trước "8- BỔ ÂM…"; tách sau hết nhận xét của phương 8, trước "CỬU VỊ PHƯƠNG".

Quy tắc trả lời:
- Chỉ trả JSON, không markdown, không giải thích, không xuất lại nội dung.
- Có tách: {"break_after": [2, 7, 11]}
- Không tách: {"break_after": []}
- Số trong mảng = số trong ngoặc vuông [k]; nghĩa là tách ngay SAU đoạn [k].
- Chỉ dùng số đã cho, không trùng, tăng dần.
""".strip()


def normalize_chunk_window(window_pages: int | None, overlap_pages: int | None) -> tuple[int, int]:
    """Ép cửa sổ hợp lệ: 1 ≤ overlap < window ≤ 20; window=1 thì overlap=0."""
    try:
        window = int(window_pages or 2)
    except (TypeError, ValueError):
        window = 2
    try:
        overlap = int(overlap_pages or 0)
    except (TypeError, ValueError):
        overlap = 0
    window = max(1, min(window, CHUNKING_MAX_WINDOW))
    overlap = max(0, overlap)
    if overlap >= window:
        overlap = window - 1
    return window, overlap


def iter_chunk_windows(page_count: int, window_pages: int, overlap_pages: int):
    """Sinh (start, end, lock_prefix) — nửa mở [start, end)."""
    if page_count <= 0:
        return
    window, overlap = normalize_chunk_window(window_pages, overlap_pages)
    step = max(1, window - overlap)
    start = 0
    first = True
    while start < page_count:
        end = min(start + window, page_count)
        if first:
            lock_prefix = 0
        else:
            lock_prefix = min(overlap, max(0, end - start - 1))
            if lock_prefix >= (end - start):
                break
        yield start, end, lock_prefix
        first = False
        if end >= page_count:
            break
        start += step


def get_chunking_config() -> ChunkingConfig:
    obj, _ = ChunkingConfig.objects.get_or_create(
        pk=1,
        defaults={
            "prompt": default_page_chunking_prompt(),
            "stitch_prompt": default_page_stitch_prompt(),
            "window_pages": 2,
            "overlap_pages": 1,
        },
    )
    prompt = (obj.prompt or "").strip()
    fields: list[str] = []
    if not prompt or prompt in {
        _LEGACY_SINGLE_PAGE_PROMPT,
        _LEGACY_REWRITE_TWO_PAGE_PROMPT,
        _LEGACY_JSON_BOTH_PAGES_PROMPT,
        _LEGACY_JSON_LOCK_LEFT_PROMPT,
        _LEGACY_JSON_NUMBERED_SHORT_PROMPT,
        _LEGACY_TWO_PAGE_WINDOW_PROMPT,
        _LEGACY_N_PAGE_WINDOW_PROMPT,
        _LEGACY_PAGE_AWARE_PROMPT,
    }:
        obj.prompt = default_page_chunking_prompt()
        fields.append("prompt")
    stitch = (getattr(obj, "stitch_prompt", None) or "").strip()
    if not stitch or stitch in _LEGACY_STITCH_PROMPTS:
        obj.stitch_prompt = default_page_stitch_prompt()
        fields.append("stitch_prompt")
    if fields:
        obj.save(update_fields=[*fields, "updated_at"])
    return obj


def resolve_chunking_provider(config: ChunkingConfig | None = None) -> LLMProvider:
    config = config or get_chunking_config()
    if config.provider_id and config.provider and config.provider.is_active:
        return config.provider
    return get_default_llm()


def save_chunking_prompt(prompt: str) -> str:
    config = save_chunking_settings(prompt=prompt)
    return config.prompt


def save_chunking_settings(
    *,
    prompt: str | None = None,
    window_pages: int | None = None,
    overlap_pages: int | None = None,
) -> ChunkingConfig:
    config = get_chunking_config()
    fields = ["updated_at"]
    if prompt is not None:
        text = (prompt or "").strip()
        if not text:
            raise ValueError("Prompt trống.")
        config.prompt = text
        fields.append("prompt")
    if window_pages is not None or overlap_pages is not None:
        requested_window = window_pages if window_pages is not None else config.window_pages
        requested_overlap = overlap_pages if overlap_pages is not None else config.overlap_pages
        try:
            window_int = int(requested_window)
        except (TypeError, ValueError) as e:
            raise ValueError("Số trang mỗi lần gửi không hợp lệ.") from e
        try:
            overlap_int = int(requested_overlap)
        except (TypeError, ValueError) as e:
            raise ValueError("Số trang trùng không hợp lệ.") from e
        if window_pages is not None and (window_int < 1 or window_int > CHUNKING_MAX_WINDOW):
            raise ValueError(f"Số trang mỗi lần gửi phải từ 1 đến {CHUNKING_MAX_WINDOW}.")
        if overlap_pages is not None and overlap_int < 0:
            raise ValueError("Số trang trùng không được âm.")
        if overlap_int >= window_int:
            raise ValueError("Số trang trùng phải nhỏ hơn số trang gửi mỗi lần.")
        window, overlap = normalize_chunk_window(window_int, overlap_int)
        config.window_pages = window
        config.overlap_pages = overlap
        fields.extend(["window_pages", "overlap_pages"])
    config.save(update_fields=fields)
    return config


def get_chunk_resize_config() -> ChunkResizeConfig:
    chunking = get_chunking_config()
    prompt = (chunking.prompt or "").strip() or default_page_chunking_prompt()
    obj, _ = ChunkResizeConfig.objects.get_or_create(
        pk=1,
        defaults={
            "provider": chunking.provider,
            "model": chunking.model or "",
            "max_tokens": 5000,
            "prompt": prompt,
        },
    )
    if not (obj.prompt or "").strip():
        obj.prompt = prompt
        obj.save(update_fields=["prompt", "updated_at"])
    if int(obj.max_tokens or 0) < 100:
        obj.max_tokens = 5000
        obj.save(update_fields=["max_tokens", "updated_at"])
    return obj


def resolve_chunk_resize_provider(config: ChunkResizeConfig | None = None) -> LLMProvider:
    config = config or get_chunk_resize_config()
    if config.provider_id and config.provider and config.provider.is_active:
        return config.provider
    return resolve_chunking_provider()


def count_breaks(text: str) -> int:
    return len(_BREAK_RE.findall(text or ""))


def paragraph_units(text: str) -> list[str]:
    """Mỗi dòng không rỗng (đã bỏ </break> và comment trang) là một đoạn đánh số."""
    cleaned = _BREAK_RE.sub("", text or "")
    return [line for line in cleaned.splitlines() if line.strip() and not _is_page_meta_line(line)]


def format_numbered_units(units: list[str], start: int = 1) -> str:
    return "\n".join(f"[{start + i}] {unit}" for i, unit in enumerate(units))


def _is_page_meta_line(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return True
    return bool(_PAGE_COMMENT_RE.match(s) or _PAGE_HEADING_RE.match(s))


_CONTINUATION_HEAD_RE = re.compile(
    r"^(Chủ\s*trị|Nhận\s*xét|Cách\s*dùng|Gia\s*giảm|Kiêng\s*kỵ|Kiêng\b|"
    r"Công\s*dụng|Chú\s*thích|Liều\s*dùng|Phương\s*dùng|Bào\s*chế)\s*[:：.]?",
    re.IGNORECASE,
)
_PAGE_MARKER_RE = re.compile(r"^Trang\s+\d+\s*$", re.IGNORECASE)


def _is_continuation_unit(text: str) -> bool:
    """Phần tiếp của cùng một phương — không được mở mục mới."""
    s = (text or "").strip()
    if not s:
        return False
    if _PAGE_MARKER_RE.match(s):
        return True
    return bool(_CONTINUATION_HEAD_RE.match(s))


def drop_continuation_breaks(units_by_index: dict[int, str], breaks: set[int]) -> set[int]:
    """Bỏ mốc tách ngay trước Chủ trị / Nhận xét / số trang… của cùng một phương."""
    kept: set[int] = set()
    for item in breaks:
        nxt = units_by_index.get(item + 1, "")
        here = units_by_index.get(item, "")
        if _is_continuation_unit(nxt) or _PAGE_MARKER_RE.match((here or "").strip()):
            continue
        kept.add(item)
    return kept


def apply_breaks_to_text(
    text: str,
    break_after: set[int],
    start_index: int,
    *,
    page_by_index: dict[int, int] | None = None,
) -> str:
    """Chèn </break> sau các đoạn đã chọn; ngay sau đó neo <!-- Trang P --> của đoạn tiếp theo."""
    raw = _BREAK_RE.sub("", text or "")
    if not raw.strip():
        return raw
    ended_nl = raw.endswith("\n")
    lines = raw.splitlines()
    out: list[str] = []
    n = start_index
    for i, line in enumerate(lines):
        out.append(line)
        stripped = line.strip()
        if not stripped or _is_page_meta_line(stripped):
            continue
        if n in break_after:
            out.append(BREAK_TOKEN)
            nxt_page = (page_by_index or {}).get(n + 1)
            if nxt_page:
                marker = f"<!-- Trang {nxt_page} -->"
                already = False
                for later in lines[i + 1 :]:
                    ls = later.strip()
                    if not ls:
                        continue
                    already = ls == marker
                    break
                if not already:
                    out.append(marker)
        n += 1
    body = "\n".join(out)
    if ended_nl and not body.endswith("\n"):
        body += "\n"
    return body


def parse_break_after(raw: str, *, max_index: int, min_index: int = 1) -> list[int]:
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = "```".join(parts[1:-1]).strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()

    data: object | None = None
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            data = None
    if data is None:
        a0 = text.find("[")
        a1 = text.rfind("]")
        if a0 >= 0 and a1 > a0:
            try:
                data = json.loads(text[a0 : a1 + 1])
            except json.JSONDecodeError:
                data = None
    if data is None:
        return []

    values: list = []
    if isinstance(data, dict):
        raw_vals = data.get("break_after", data.get("breaks", []))
        if isinstance(raw_vals, str):
            values = re.findall(r"\d+", raw_vals)
        elif isinstance(raw_vals, list):
            values = raw_vals
    elif isinstance(data, list):
        values = data

    out: list[int] = []
    seen: set[int] = set()
    for item in values:
        try:
            num = int(item)
        except (TypeError, ValueError):
            continue
        if num < min_index or num > max_index or num in seen:
            continue
        seen.add(num)
        out.append(num)
    out.sort()
    return out


def _generate_break_after(
    *,
    provider: LLMProvider,
    prompt: str,
    model: str,
    max_index: int,
    min_index: int = 1,
) -> list[int]:
    if max_index < 1:
        return []
    client = get_text_llm_client(provider, model)
    raw = client.generate(prompt, max_tokens=CHUNKING_MAX_TOKENS)
    return parse_break_after(raw, max_index=max_index, min_index=min_index)


def chunk_page_text(
    *,
    provider: LLMProvider,
    prompt_template: str,
    book_title: str,
    page_number: int,
    page_text: str,
    model: str = "",
) -> str:
    results = chunk_pages(
        provider=provider,
        prompt_template=prompt_template,
        book_title=book_title,
        pages=[(page_number, page_text)],
        model=model,
    )
    return results[0] if results else _BREAK_RE.sub("", page_text or "")


def chunk_pages(
    *,
    provider: LLMProvider,
    prompt_template: str,
    book_title: str,
    pages: list[tuple[int, str]],
    model: str = "",
    lock_prefix: int = 0,
    kept_texts: list[str] | None = None,
) -> list[str]:
    """Phân đoạn N trang. lock_prefix trang đầu giữ nguyên; chỉ gắn mốc vào trang mới."""
    if not pages:
        return []
    n = len(pages)
    lock_prefix = max(0, min(int(lock_prefix or 0), n))
    origs = [_BREAK_RE.sub("", orig or "") for _num, orig in pages]
    kept = list(kept_texts or [])
    units_per_page = [paragraph_units(source) for source in origs]

    def _kept_or_orig(index: int) -> str:
        if index < lock_prefix and index < len(kept) and (kept[index] or "").strip():
            return kept[index]
        return origs[index]

    new_has_units = any(units_per_page[i] for i in range(lock_prefix, n))
    if not new_has_units:
        return [_kept_or_orig(i) for i in range(n)]

    idx = 1
    ranges: list[tuple[int, int]] = []
    numbered_blocks: list[str] = []
    for units in units_per_page:
        if not units:
            ranges.append((0, 0))
            numbered_blocks.append("")
            continue
        start = idx
        end = idx + len(units) - 1
        ranges.append((start, end))
        numbered_blocks.append(format_numbered_units(units, start))
        idx = end + 1
    max_index = idx - 1

    prompt_pages = [
        {
            "page_number": pages[i][0],
            "numbered_text": numbered_blocks[i],
            "index_start": ranges[i][0],
            "index_end": ranges[i][1],
        }
        for i in range(n)
        if ranges[i][0]
    ]
    prompt = build_window_chunking_prompt(
        template=prompt_template or default_page_chunking_prompt(),
        book_title=book_title,
        pages=prompt_pages,
    )
    breaks = set(
        _generate_break_after(
            provider=provider,
            prompt=prompt,
            model=model,
            max_index=max_index,
        )
    )
    units_by_index: dict[int, str] = {}
    page_by_index: dict[int, int] = {}
    n_idx = 1
    for i, units in enumerate(units_per_page):
        page_num = pages[i][0]
        for unit in units:
            units_by_index[n_idx] = unit
            page_by_index[n_idx] = page_num
            n_idx += 1
    breaks = drop_continuation_breaks(units_by_index, breaks)

    out: list[str] = []
    for i, orig in enumerate(origs):
        if i < lock_prefix:
            out.append(_kept_or_orig(i))
            continue
        start, end = ranges[i]
        if start == 0:
            text = orig
        else:
            page_breaks = {item for item in breaks if start <= item <= end}
            text = apply_breaks_to_text(orig, page_breaks, start, page_by_index=page_by_index)
        if i == lock_prefix and lock_prefix > 0:
            prev_end = 0
            for j in range(lock_prefix - 1, -1, -1):
                if ranges[j][1]:
                    prev_end = ranges[j][1]
                    break
            if prev_end and prev_end in breaks:
                rest = text.lstrip()
                prefix = BREAK_TOKEN
                nxt_page = page_by_index.get(start)
                if nxt_page:
                    marker = f"<!-- Trang {nxt_page} -->"
                    first = rest.splitlines()[0].strip() if rest else ""
                    if first != marker:
                        prefix = f"{BREAK_TOKEN}\n{marker}"
                text = f"{prefix}\n{rest}" if rest else prefix
        out.append(text)
    return out


def chunk_two_pages(
    *,
    provider: LLMProvider,
    prompt_template: str,
    book_title: str,
    page_a: int,
    text_a: str,
    orig_a: str,
    page_b: int,
    text_b: str,
    orig_b: str,
    model: str = "",
    apply_right_only: bool = False,
) -> tuple[str, str]:
    lock_prefix = 1 if apply_right_only else 0
    kept_texts = [text_a] if apply_right_only else []
    out = chunk_pages(
        provider=provider,
        prompt_template=prompt_template,
        book_title=book_title,
        pages=[(page_a, orig_a or text_a), (page_b, orig_b or text_b)],
        model=model,
        lock_prefix=lock_prefix,
        kept_texts=kept_texts,
    )
    return out[0], out[1]
