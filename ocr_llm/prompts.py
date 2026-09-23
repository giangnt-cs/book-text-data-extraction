from __future__ import annotations

from typing import Optional


def _common_rules(lang: str) -> str:
    return f"""
Bạn là một hệ thống OCR bằng LLM từ ảnh scan.
Trả lời bằng tiếng {lang}.
Chỉ trả về nội dung ở định dạng Markdown, KHÔNG giải thích, KHÔNG thêm lời dẫn.
CẤM viết câu mô tả/meta kiểu "Đây là ảnh...", "Hình ảnh hiển thị...", "Trên ảnh có..." — chỉ chép chữ/nội dung thực tế trên ảnh.
Nếu không đọc được, trả về chuỗi: [KHÔNG_ĐỌC_ĐƯỢC]
""".strip()


def prompt_for_text(*, lang: str = "Việt", label_hint: Optional[str] = None) -> str:
    hint = (label_hint or "").strip().lower()
    heading_rule = ""
    if "title" in hint or "heading" in hint or "header" in hint:
        heading_rule = (
            "Nếu vùng này là tiêu đề/đề mục, hãy dùng Markdown heading phù hợp (## hoặc ###).\n"
        )
    return f"""
{_common_rules(lang)}

Nhiệm vụ: OCR vùng VĂN BẢN từ ảnh.
{heading_rule}Giữ lại định dạng cơ bản:
- Đề mục/tiêu đề
- In đậm (**...**) và in nghiêng (*...*) nếu quan sát thấy
- Danh sách gạch đầu dòng/đánh số nếu có
- Hình ảnh cung cấp cho bạn là một đoạn văn bản, vì vậy bạn không cần ngắt dòng/đoạn như ảnh mà hãy viết liên tục như một đoạn văn.
Không bịa nội dung không có trên ảnh.
""".strip()


def prompt_for_table(*, lang: str = "Việt") -> str:
    return f"""
{_common_rules(lang)}

Nhiệm vụ: OCR vùng BẢNG từ ảnh.
Hãy xuất bảng ở dạng Markdown table (dùng dấu |).
- Cố gắng giữ đúng số cột và thứ tự hàng/cột.
- Nếu ô có nhiều dòng, dùng <br> để xuống dòng trong ô.
- Nếu có header, hãy đặt ở hàng đầu và thêm dòng phân cách ---.
Không bịa nội dung.
""".strip()


def prompt_for_formula(*, lang: str = "Việt") -> str:
    return f"""
{_common_rules(lang)}

Nhiệm vụ: OCR vùng CÔNG THỨC toán/hóa từ ảnh.
Yêu cầu:
- Xuất Markdown/LaTeX rõ ràng (inline `$...$` hoặc block `$$...$$` khi cần).
- Giữ đúng ký hiệu, chỉ số, mũ, phân số.
- Nếu là phương trình nhiều dòng, mỗi dòng một block `$$`.
Không bịa nội dung.
""".strip()


def prompt_for_full_page(*, lang: str = "Việt", has_table_or_formula: bool = False) -> str:
    """OCR cả trang sách — chỉ chép chữ, không thêm ký tự hay lời dẫn."""
    layout_rules = ""
    if has_table_or_formula:
        layout_rules = """
Trang có thể chứa bảng và/hoặc công thức:
- Bảng: xuất Markdown table (|) giữ đúng hàng/cột.
- Công thức: LaTeX inline `$...$` hoặc block `$$...$$`.
"""
    return f"""
Bạn là hệ thống OCR trang sách scan do người dùng upload để số hóa.
Nhiệm vụ duy nhất: chép nguyên văn chữ nhìn thấy trên ảnh (verbatim transcription).
Chỉ trả về nội dung chữ trên trang, đúng thứ tự đọc tự nhiên, giữ xuống dòng như trên ảnh.
CẤM thêm bất kỳ ký tự, lời dẫn, giải thích, mô tả ảnh, tóm tắt hay meta nào.
CẤM thêm markdown heading (#), in đậm (**), in nghiêng (*) nếu không có trên ảnh.
CẤM bịa nội dung không có trên trang.
CẤM tái bản/tóm tắt từ trí nhớ — chỉ chép chữ trên ảnh.
{layout_rules}
Nếu không đọc được, trả về đúng chuỗi: [KHÔNG_ĐỌC_ĐƯỢC]
""".strip()


def prompt_for_image(*, lang: str = "Việt") -> str:
    """Vùng IMAGE: chỉ OCR chữ, không mô tả ảnh."""
    return f"""
{_common_rules(lang)}

Nhiệm vụ: OCR toàn bộ chữ/văn bản trên vùng ảnh (bìa sách, nhãn, chú thích, chữ Hán/Nôm…).
- Giữ đúng thứ tự đọc và xuống dòng như trên ảnh.
- Giữ in đậm/in nghiêng nếu quan sát thấy.
- KHÔNG mô tả ảnh, KHÔNG tóm tắt, KHÔNG diễn giải.
- Nếu không có chữ nào, trả về: [KHÔNG_ĐỌC_ĐƯỢC]
""".strip()


def default_text_cleaning_prompt() -> str:
    return """
Bạn là chuyên gia biên tập sách y học cổ truyền Việt Nam.

Nhiệm vụ: làm sạch văn bản OCR từ một trang sách scan.

Yêu cầu:
- Giữ nguyên nội dung và ý nghĩa y khoa, không bịa thêm.
- Xóa header, footer, số trang, watermark, quảng cáo đọc sách (vd. "Tìm sách", "Digitized by Google").
- Sửa lỗi chính tả, dấu câu, thuật ngữ Hán-Việt y học cổ truyền.
- Giữ cấu trúc Markdown nếu có (tiêu đề ##, bảng |, công thức $...$).
- Ghép dòng bị ngắt do layout sách thành câu/đoạn tự nhiên.
- Chỉ trả về văn bản đã làm sạch, không giải thích, không bọc ```markdown.
""".strip()


def default_book_classify_prompt() -> str:
    return """
Bạn là thủ thư chuyên sách y học cổ truyền Việt Nam / Trung Quốc.

Nhiệm vụ: đọc phần đầu sách (bìa, lời nói đầu, mục lục, thông tin xuất bản) rồi điền metadata và phân loại.

Quy tắc:
- Chỉ dùng thông tin có trong văn bản. Không bịa.
- Nếu không chắc, để chuỗi rỗng "" (metadata) hoặc mã phù hợp nhất / UN (thời đại không rõ).
- published_year chỉ năm (vd. 2011) hoặc khoảng năm nếu sách ghi vậy.
- author: tên tác giả / chủ biên / dịch giả như trên sách, có thể nhiều người ngăn bằng "; ".
- publisher: tên nhà xuất bản.
- Các trường class_* phải là ĐÚNG một mã trong danh mục (không viết tên).

Chỉ trả về JSON, không markdown, không giải thích:
{
  "author": "",
  "publisher": "",
  "published_year": "",
  "class_source": "",
  "class_field": "",
  "class_language": "",
  "class_doc_type": "",
  "class_era": ""
}
""".strip()


def build_book_classify_prompt(
    *,
    template: str,
    book_title: str,
    catalog_text: str,
    front_text: str,
) -> str:
    return f"""{template.strip()}

---
Tên file / tiêu đề hiện tại: {book_title}

Danh mục mã phân loại được phép:

{catalog_text.strip()}

Văn bản khoảng 20 trang đầu:

{(front_text or "").strip()}
""".strip()


def default_page_chunking_prompt() -> str:
    return """
Bạn nhận các đoạn đã đánh số liên tục của sách y học cổ truyền / dược liệu Việt Nam: [1], [2], [3], …

Một MỤC (bài thuốc / phương) là CẢ KHỐI, thường theo thứ tự:
tên phương → vị thuốc → cách sắc/uống → Chủ trị → Nhận xét (có thể thêm gia giảm, kiêng kỵ).

Tách SAU đoạn [k] chỉ khi đoạn [k+1] mở một mục MỚI, độc lập:
- Tên phương/bài mới: số thứ tự ("7-", "8-"), hoặc tên riêng kiểu ĐẠI BỔ… PHƯƠNG / THANG / HOÀN / TÁN / CAO / ĐƠN.
- Mục từ điển / vị thuốc mới (tên mục khác hẳn, không phải lời giải của mục trước).

TUYỆT ĐỐI KHÔNG tách:
- Trước "Chủ trị", "Nhận xét", "Cách dùng", "Gia giảm", "Kiêng kỵ", "Công dụng", "Chú thích" — đây là phần của phương ngay trước.
- Dòng số trang in trong sách, header, footer (ví dụ "Trang 14") — không phải mục mới.
- Đoạn văn, gạch đầu dòng, thành phần, sắc uống của CÙNG một phương.

Ví dụ SAI: cắt giữa "8- BỔ ÂM LIỄM DƯƠNG PHƯƠNG" + vị thuốc và "Chủ trị: …".
Ví dụ ĐÚNG: tách sau hết "Nhận xét" của phương 7, trước "8- BỔ ÂM…"; tách sau hết nhận xét của phương 8, trước "CỬU VỊ PHƯƠNG".

Quy tắc trả lời:
- Chỉ trả JSON, không markdown, không giải thích, không xuất lại nội dung.
- Có tách: {"break_after": [2, 7, 11]}
- Không tách: {"break_after": []}
- Số trong mảng = số trong ngoặc vuông [k]; nghĩa là tách ngay SAU đoạn [k].
- Chỉ dùng số đã cho, không trùng, tăng dần.
""".strip()


def default_page_stitch_prompt() -> str:
    return """
Hai đoạn văn nằm ở ranh giới hai trang sách.
Chỉ trả đúng một từ: true hoặc false.
true = hai đoạn đang viết tiếp, cần nối.
false = đoạn sau là nội dung mới, không nối.
Không giải thích, không markdown, không ngoặc nhọn.
""".strip()


def build_page_stitch_prompt(*, template: str, left: str, right: str) -> str:
    return f"""{template.strip()}

A:
{(left or "").strip()}

B:
{(right or "").strip()}

Trả lời đúng một dòng: true hoặc false
""".strip()


def build_page_chunking_prompt(
    *,
    template: str,
    book_title: str,
    page_number: int,
    numbered_text: str,
    index_start: int,
    index_end: int,
) -> str:
    return build_window_chunking_prompt(
        template=template,
        book_title=book_title,
        pages=[
            {
                "numbered_text": numbered_text,
                "index_start": index_start,
                "index_end": index_end,
            }
        ],
    )


def build_window_chunking_prompt(
    *,
    template: str,
    book_title: str,
    pages: list[dict],
) -> str:
    """pages: {numbered_text, index_start, index_end} — không gửi số trang cho LLM."""
    blocks = [
        (item.get("numbered_text") or "").strip()
        for item in pages
        if (item.get("numbered_text") or "").strip()
    ]
    if not blocks:
        return template.strip()
    numbered = "\n".join(blocks)
    starts = [item["index_start"] for item in pages if item.get("index_start")]
    ends = [item["index_end"] for item in pages if item.get("index_end")]
    index_start = starts[0] if starts else 1
    index_end = ends[-1] if ends else index_start
    return f"""{template.strip()}

---
QUAN TRỌNG: Chỉ trả JSON {{"break_after": [...]}} — không xuất lại nội dung.
Sách: {book_title}
Đoạn [{index_start}]–[{index_end}].

{numbered}
""".strip()


def build_two_page_chunking_prompt(
    *,
    template: str,
    book_title: str,
    page_a: int,
    numbered_a: str,
    page_b: int,
    numbered_b: str,
    index_a_start: int,
    index_a_end: int,
    index_b_start: int,
    index_b_end: int,
) -> str:
    return build_window_chunking_prompt(
        template=template,
        book_title=book_title,
        pages=[
            {
                "page_number": page_a,
                "numbered_text": numbered_a,
                "index_start": index_a_start,
                "index_end": index_a_end,
            },
            {
                "page_number": page_b,
                "numbered_text": numbered_b,
                "index_start": index_b_start,
                "index_end": index_b_end,
            },
        ],
    )


def default_book_ingest_prompt() -> str:
    return """
Bạn nhận văn bản OCR trang đầu (bìa / trang tên) của một cuốn sách y học cổ truyền hoặc dược liệu Việt Nam.

Nhiệm vụ: rút trích tên sách chính thức, ngắn gọn, đúng chính tả.

Quy tắc:
- Không bịa tên không có trên trang.
- Bỏ số trang, watermark, "Digitized by", tên file.
- Chỉ trả JSON, không markdown, không giải thích:
{"title": ""}
""".strip()


def default_book_ingest_match_prompt() -> str:
    return """
Bạn nhận một tên sách vừa rút từ trang đầu, và một danh sách ngắn các tên gần giống đã có trên hệ thống.

Quy tắc:
- Coi là TRÙNG nếu cùng một cuốn (hoa/thường khác, thiếu dấu, thêm phụ đề/NXB).
- Tên khác nội dung thì không trùng.
- Chỉ trả JSON, không markdown, không giải thích:
{"duplicate": false, "matched_title": ""}
- duplicate=true thì matched_title phải sao chép ĐÚNG một tên trong danh sách ứng viên.
- duplicate=false thì matched_title để "".
""".strip()


def build_book_ingest_prompt(
    *,
    template: str,
    page_text: str,
    original_name: str = "",
    existing_titles: str = "",
) -> str:
    extra = ""
    if (existing_titles or "").strip():
        extra = f"""
Tên sách đã có (tham khảo, không bắt buộc dùng):

{existing_titles.strip()}
"""
    return f"""{template.strip()}

---
Tên file gốc: {original_name or "(không có)"}
{extra}
Văn bản OCR trang đầu:

{(page_text or "").strip()}
""".strip()


def build_book_ingest_match_prompt(
    *,
    template: str,
    title: str,
    candidates_text: str,
) -> str:
    return f"""{template.strip()}

---
Tên vừa rút: {title}

Ứng viên đã có trên hệ thống:

{(candidates_text or "").strip()}
""".strip()


def default_book_dedupe_prompt() -> str:
    return """
Đây là một danh sách tên sách (khoảng 30 tên). Hãy tìm các nhóm tên chỉ cùng một cuốn sách — viết hơi khác, thiếu dấu, thêm/bớt phụ đề, viết hoa khác.

Quy tắc:
- Chỉ nhóm khi gần như chắc là cùng một cuốn.
- Tên khác nội dung thì để riêng, không gom.
- Chỉ trả JSON, không markdown, không giải thích:
{"groups": [{"canonical": "tên chuẩn", "titles": ["tên 1", "tên 2"]}]}
- Nếu không có cặp trùng: {"groups": []}
- Mỗi phần tử trong titles phải là tên xuất hiện đúng trong danh sách (sao chép nguyên văn).
""".strip()


def build_book_dedupe_prompt(*, template: str, titles_text: str) -> str:
    return f"""{template.strip()}

---
Danh sách tên:

{(titles_text or "").strip()}
""".strip()


def build_page_cleaning_prompt(
    *,
    template: str,
    book_title: str,
    page_number: int,
    ocr_text: str,
) -> str:
    return f"""{template.strip()}

---
Trang số: {page_number}
Sách: {book_title}

Văn bản OCR cần làm sạch:

{(ocr_text or "").strip()}
""".strip()


def default_image_describe_prompt() -> str:
    return """
Dưới đây là chữ đọc được trên một hình minh họa rút trích từ sách y học cổ truyền Việt Nam.
Không gửi kèm ảnh — chỉ dựa vào chữ trên hình để mô tả.

Nhiệm vụ: viết mô tả ngắn, rõ, bằng tiếng Việt về nội dung hình.

Nếu hình minh họa dược liệu / thực vật / vị thuốc:
- Lấy đủ tên gọi (tên Việt, tên Nôm, tên dân gian, tên Trung Quốc nếu có trong chữ trên hình).
- Lấy tên khoa học (Latin) nếu có trong chữ trên hình hoặc suy ra chắc chắn từ chữ đó.
- Không bịa tên khoa học khi không có căn cứ.

Quy tắc:
- Không chép lại nguyên văn toàn bộ chữ trên hình (chữ đã có mục riêng); chỉ dùng để nhận diện và mô tả.
- Nếu chữ trên hình không đủ để mô tả, trả đúng một dòng: [KHÔNG_ĐỦ_THÔNG_TIN]
- Không markdown heading, không lời dẫn kiểu "Đây là ảnh…".
""".strip()


def default_image_describe_page_prompt() -> str:
    return """
Đây là hình minh họa rút trích từ sách y học cổ truyền Việt Nam, minh họa cho nội dung trang sách được gửi kèm.
Hãy mô tả hình sao cho phù hợp với ngữ cảnh trang.

Nếu hình minh họa dược liệu / thực vật / vị thuốc:
- Lấy đủ tên gọi (tên Việt, tên Nôm, tên dân gian, tên Trung Quốc nếu có).
- Lấy tên khoa học (Latin) nếu có trong chữ trên hình hoặc trong nội dung trang, hoặc suy ra chắc chắn từ đó.
- Không bịa tên khoa học khi không có căn cứ.

Quy tắc:
- Mô tả bằng tiếng Việt, ngắn và rõ.
- Kết hợp chữ trên hình (nếu có) và nội dung trang sách.
- Không bịa chi tiết không suy ra được từ chữ trên hình hoặc nội dung trang.
- Không markdown heading, không lời dẫn kiểu "Đây là ảnh…".
- Chỉ trả về đoạn mô tả hình.
""".strip()


def build_image_describe_prompt(*, template: str, ocr_text: str) -> str:
    ocr = (ocr_text or "").strip() or "(không có chữ)"
    return f"""{template.strip()}

---
Chữ đọc được trên hình:

{ocr}
""".strip()


def build_image_describe_page_prompt(*, template: str, ocr_text: str, page_text: str) -> str:
    ocr = (ocr_text or "").strip() or "(không có chữ)"
    page = (page_text or "").strip() or "(không có nội dung trang)"
    return f"""{template.strip()}

---
Chữ đọc được trên hình:

{ocr}

Nội dung trang sách (hình này là minh họa cho nội dung sau):

{page}
""".strip()


