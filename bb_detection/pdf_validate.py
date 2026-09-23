"""Kiểm tra PDF hợp lệ — không phụ thuộc numpy/pdf2image (dùng được trên web)."""

from __future__ import annotations

from pathlib import Path


class InvalidPdfError(Exception):
    """PDF không hợp lệ hoặc không đọc được."""


def validate_pdf(
    *,
    pdf_path: str | Path | None = None,
    pdf_bytes: bytes | None = None,
) -> tuple[bool, str]:
    """
    Kiểm tra PDF trước khi render/detect.
    Trả (True, "") nếu OK; (False, thông báo tiếng Việt) nếu lỗi.
    """
    path = Path(pdf_path) if pdf_path else None
    data = pdf_bytes
    if data is None:
        if path is None:
            return False, "Không có dữ liệu PDF để kiểm tra."
        if not path.is_file():
            return False, "Không tìm thấy file PDF."
        try:
            with path.open("rb") as fh:
                head = fh.read(2048)
        except OSError as exc:
            return False, f"Không đọc được file PDF ({exc})."
    else:
        if not data:
            return False, "File PDF trống."
        head = data[:2048]

    if not head.lstrip().startswith(b"%PDF"):
        return (
            False,
            "File không phải PDF hợp lệ (thiếu header %PDF). "
            "Có thể file bị đổi tên sai, upload lỗi, hoặc là định dạng khác (Word, ảnh…).",
        )

    try:
        import fitz

        doc = fitz.open(str(path)) if data is None else fitz.open(stream=data, filetype="pdf")
        try:
            if doc.page_count < 1:
                return False, "PDF không có trang nào."
            doc.load_page(0)
        finally:
            doc.close()
    except Exception as e:
        detail = str(e).strip() or type(e).__name__
        return (
            False,
            "PDF hỏng hoặc không đọc được (xref/trailer lỗi). "
            f"Hãy mở lại file bằng Adobe/Foxit, lưu bản PDF mới rồi upload lại. Chi tiết: {detail}",
        )

    return True, ""


def raise_if_invalid_pdf(*, pdf_path: str | Path | None = None, pdf_bytes: bytes | None = None) -> None:
    ok, err = validate_pdf(pdf_path=pdf_path, pdf_bytes=pdf_bytes)
    if not ok:
        raise InvalidPdfError(err)
