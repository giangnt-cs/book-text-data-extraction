from __future__ import annotations

import hashlib
import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class BookStatus(models.TextChoices):
    NEW = "new", "Mới"
    PROCESSING = "processing", "Đang xử lý OCR"
    DONE = "done", "Hoàn thành OCR"
    APPROVED = "approved", "Đã duyệt"
    FAILED = "failed", "Lỗi"


OCR_READY_STATUSES = {BookStatus.DONE, BookStatus.APPROVED}


class CleaningCoverage(models.TextChoices):
    NONE = "none", "Chưa làm sạch"
    PARTIAL = "partial", "Làm sạch một phần"
    FULL = "full", "Làm sạch toàn bộ"


class ContentType(models.TextChoices):
    TEXT = "text", "Text"
    IMAGE = "image", "Image"
    TABLE = "table", "Table"
    FORMULA = "formula", "Formula"
    COLUMNAR = "columnar", "Columnar"
    UNKNOWN = "unknown", "Unknown"


class PageOcrMode(models.TextChoices):
    STANDARD = "standard", "Trang thường (không bảng/công thức)"
    LAYOUT = "layout", "Trang có bảng hoặc công thức"


class ClassificationKind(models.TextChoices):
    MAIN = "main", "Nhóm chính"
    AUX = "aux", "Nhóm phụ"


class ClassificationGroup(models.Model):
    """Nhóm phân loại sách (nguồn, lĩnh vực, ngôn ngữ, loại tài liệu, thời đại)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=32, unique=True, verbose_name="Mã nhóm")
    name = models.CharField(max_length=120, verbose_name="Tên nhóm")
    kind = models.CharField(
        max_length=8,
        choices=ClassificationKind.choices,
        default=ClassificationKind.MAIN,
        verbose_name="Loại nhóm",
    )
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name="Thứ tự")

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Nhóm phân loại"
        verbose_name_plural = "Nhóm phân loại"

    def __str__(self) -> str:
        return self.name


class ClassificationTerm(models.Model):
    """Phần tử trong một nhóm — mã ký tự dùng khi đặt mã sách."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        ClassificationGroup,
        on_delete=models.CASCADE,
        related_name="terms",
        verbose_name="Nhóm",
    )
    code = models.CharField(max_length=8, verbose_name="Ký tự / mã")
    name = models.CharField(max_length=200, verbose_name="Thuật ngữ")
    description = models.CharField(max_length=400, blank=True, verbose_name="Mô tả")
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name="Thứ tự")

    class Meta:
        ordering = ["group__sort_order", "sort_order", "code"]
        verbose_name = "Thuật ngữ phân loại"
        verbose_name_plural = "Thuật ngữ phân loại"
        constraints = [
            models.UniqueConstraint(fields=["group", "code"], name="uniq_class_term_group_code"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


def _term_fk(*, related_name: str, verbose_name: str, group_slug: str):
    return models.ForeignKey(
        ClassificationTerm,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name=related_name,
        verbose_name=verbose_name,
        limit_choices_to={"group__slug": group_slug},
    )


class Book(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255, verbose_name="Tên sách")
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    pdf_file = models.FileField(upload_to="books/pdfs/", verbose_name="File PDF")
    markdown_file = models.FileField(upload_to="books/markdown/", blank=True, verbose_name="File Markdown")
    status = models.CharField(max_length=16, choices=BookStatus.choices, default=BookStatus.NEW, verbose_name="Trạng thái")
    error_message = models.TextField(blank=True, verbose_name="Lỗi xử lý")
    lang = models.CharField(
        max_length=32,
        default="vi",
        verbose_name="Ngôn ngữ OCR",
        help_text="Ngôn ngữ dùng khi chạy OCR, khác với nhóm phân loại «Ngôn ngữ» của sách.",
    )
    author = models.CharField(max_length=400, blank=True, verbose_name="Tên tác giả")
    publisher = models.CharField(max_length=400, blank=True, verbose_name="Nhà xuất bản")
    published_year = models.CharField(max_length=32, blank=True, verbose_name="Năm xuất bản")
    class_source = _term_fk(related_name="books_source", verbose_name="Nguồn y học", group_slug="source")
    class_field = _term_fk(related_name="books_field", verbose_name="Lĩnh vực", group_slug="field")
    class_language = _term_fk(related_name="books_language", verbose_name="Ngôn ngữ", group_slug="language")
    class_doc_type = _term_fk(related_name="books_doc_type", verbose_name="Loại tài liệu", group_slug="doc_type")
    class_era = _term_fk(related_name="books_era", verbose_name="Thời đại", group_slug="era")
    ocr_standard_provider = models.ForeignKey(
        "llm.OCRProvider",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="books_standard",
        verbose_name="OCR trang thường",
        help_text="Để trống = dùng cấu hình hệ thống.",
    )
    ocr_layout_provider = models.ForeignKey(
        "llm.OCRProvider",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="books_layout",
        verbose_name="OCR trang bảng/công thức",
        help_text="Để trống = dùng cấu hình hệ thống.",
    )
    cleaning_coverage = models.CharField(
        max_length=16,
        choices=CleaningCoverage.choices,
        default=CleaningCoverage.NONE,
        verbose_name="Mức làm sạch",
        help_text="Tự cập nhật: chưa làm sạch, một phần (một số trang), hoặc toàn bộ sách.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="books_created",
        verbose_name="Người tạo",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Ngày tạo")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Ngày cập nhật")

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Sách"
        verbose_name_plural = "Sách"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._make_unique_slug(self.title, self.id, exclude_pk=self.pk)
        super().save(*args, **kwargs)

    @staticmethod
    def _make_unique_slug(title: str, book_id: uuid.UUID, *, exclude_pk=None) -> str:
        base = slugify(title)[:250] or str(book_id)
        slug = base
        counter = 2
        while True:
            qs = Book.objects.filter(slug=slug)
            if exclude_pk is not None:
                qs = qs.exclude(pk=exclude_pk)
            if not qs.exists():
                return slug
            suffix = f"-{counter}"
            slug = f"{base[: 250 - len(suffix)]}{suffix}"
            counter += 1

    @property
    def classification_codes(self) -> str:
        parts = []
        for attr in ("class_source", "class_field", "class_language", "class_doc_type", "class_era"):
            term = getattr(self, attr)
            parts.append(term.code if term else "—")
        return " · ".join(parts)

    @property
    def is_ocr_ready(self) -> bool:
        return self.status in OCR_READY_STATUSES

    def refresh_cleaning_coverage(self) -> str:
        """Tính lại mức làm sạch từ lần làm sạch hoàn thành mới nhất."""
        batch = (
            self.cleaning_batches.filter(status=CleaningBatchStatus.DONE)
            .order_by("-created_at")
            .first()
        )
        ocr_ids = [
            page_id
            for page_id, text in self.pages.values_list("id", "text_md")
            if (text or "").strip()
        ]
        if not batch or not ocr_ids:
            coverage = CleaningCoverage.NONE
        else:
            cleaned_n = (
                PageCleanedText.objects.filter(batch=batch, page_id__in=ocr_ids)
                .exclude(text_md="")
                .values("page_id")
                .distinct()
                .count()
            )
            if cleaned_n <= 0:
                coverage = CleaningCoverage.NONE
            elif cleaned_n >= len(ocr_ids):
                coverage = CleaningCoverage.FULL
            else:
                coverage = CleaningCoverage.PARTIAL

        if self.cleaning_coverage != coverage:
            Book.objects.filter(pk=self.pk).update(
                cleaning_coverage=coverage,
                updated_at=timezone.now(),
            )
            self.cleaning_coverage = coverage
        return coverage

    def display_status(
        self,
        *,
        cleaning_running: bool | None = None,
        has_cleaned_text: bool | None = None,
    ) -> tuple[str, str]:
        """Trạng thái hiển thị tuần tự: OCR → làm sạch → duyệt."""
        if self.status == BookStatus.PROCESSING:
            return "ocr_processing", "Đang xử lý OCR"
        if self.status == BookStatus.FAILED:
            return "failed", "Lỗi"
        if self.status == BookStatus.NEW:
            return "new", "Mới"

        if cleaning_running is None:
            cleaning_running = self.cleaning_batches.filter(
                status__in=["pending", "processing"]
            ).exists()
        if cleaning_running:
            return "cleaning", "Đang làm sạch"

        if self.status == BookStatus.APPROVED:
            return "approved", "Đã duyệt"

        coverage = self.cleaning_coverage
        if has_cleaned_text is False:
            coverage = CleaningCoverage.NONE
        elif has_cleaned_text is True and coverage == CleaningCoverage.NONE:
            coverage = CleaningCoverage.PARTIAL

        if coverage == CleaningCoverage.PARTIAL:
            return "cleaned_partial", "Làm sạch một phần"
        if coverage == CleaningCoverage.FULL:
            return "cleaned", "Hoàn thành làm sạch"

        if self.status == BookStatus.DONE:
            return "ocr_done", "Hoàn thành OCR"
        return self.status, self.get_status_display()

    def __str__(self) -> str:
        return self.title


class Page(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="pages")
    page_number = models.PositiveIntegerField()
    page_image = models.FileField(upload_to="books/pages/", blank=True)
    width = models.FloatField(null=True, blank=True)
    height = models.FloatField(null=True, blank=True)
    ocr_mode = models.CharField(
        max_length=16,
        choices=PageOcrMode.choices,
        default=PageOcrMode.STANDARD,
        verbose_name="Chế độ OCR",
    )
    has_table = models.BooleanField(default=False, verbose_name="Có bảng")
    has_formula = models.BooleanField(default=False, verbose_name="Có công thức")
    text_md = models.TextField(blank=True, verbose_name="Nội dung OCR trang")
    ocr_provider = models.ForeignKey(
        "llm.OCRProvider",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ocr_pages",
        verbose_name="Công cụ OCR đã dùng",
    )
    ocr_tool_name = models.CharField(max_length=160, blank=True, verbose_name="Tên công cụ OCR")
    ocr_tool_backend = models.CharField(max_length=40, blank=True, verbose_name="Loại công cụ OCR")
    ocr_fallback_from = models.CharField(
        max_length=160,
        blank=True,
        verbose_name="Fallback từ",
        help_text="Công cụ được chọn ban đầu nếu trang này dùng fallback.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["book", "page_number"], name="uniq_book_page_number"),
        ]
        ordering = ["page_number"]
        verbose_name = "Trang"
        verbose_name_plural = "Các trang"

    @property
    def ocr_tool_display(self) -> str:
        name = (self.ocr_tool_name or "").strip()
        if not name and self.ocr_provider_id:
            name = self.ocr_provider.name
        if not name:
            return ""
        from_name = (self.ocr_fallback_from or "").strip()
        if from_name:
            return f"{name} (fallback từ {from_name})"
        return name

    def __str__(self) -> str:
        return f"{self.book.title} – trang {self.page_number}"


class CleaningBatchStatus(models.TextChoices):
    PENDING = "pending", "Chờ xử lý"
    PROCESSING = "processing", "Đang làm sạch"
    DONE = "done", "Hoàn thành"
    FAILED = "failed", "Lỗi"


class BookCleaningBatch(models.Model):
    """Một lần chạy làm sạch văn bản cho cả sách."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="cleaning_batches")
    provider = models.ForeignKey(
        "llm.LLMProvider",
        on_delete=models.PROTECT,
        related_name="cleaning_batches",
    )
    model = models.CharField(max_length=200, blank=True, verbose_name="Model đã dùng")
    prompt = models.TextField(verbose_name="Prompt đã dùng")
    status = models.CharField(
        max_length=16,
        choices=CleaningBatchStatus.choices,
        default=CleaningBatchStatus.PENDING,
    )
    error_message = models.TextField(blank=True, verbose_name="Lỗi")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cleaning_batches",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Lần làm sạch văn bản"
        verbose_name_plural = "Lần làm sạch văn bản"

    def __str__(self) -> str:
        return f"Làm sạch {self.book.title} ({self.get_status_display()})"


class PageCleanedText(models.Model):
    """Kết quả làm sạch một trang — không ghi đè OCR gốc."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(BookCleaningBatch, on_delete=models.CASCADE, related_name="page_texts")
    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="cleaned_texts")
    text_md = models.TextField(blank=True, verbose_name="Văn bản đã làm sạch")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["batch", "page"], name="uniq_cleaning_batch_page"),
        ]
        ordering = ["page__page_number"]
        verbose_name = "Trang đã làm sạch"
        verbose_name_plural = "Trang đã làm sạch"

    def __str__(self) -> str:
        return f"Trang {self.page.page_number} ({self.batch_id})"


class ChunkingBatchStatus(models.TextChoices):
    PENDING = "pending", "Chờ xử lý"
    PROCESSING = "processing", "Đang phân đoạn"
    DONE = "done", "Hoàn thành"
    FAILED = "failed", "Lỗi"


class BookChunkingBatch(models.Model):
    """Một lần chạy phân đoạn (chunking) cho cả sách — snapshot cửa sổ trang."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="chunking_batches")
    provider = models.ForeignKey(
        "llm.LLMProvider",
        on_delete=models.PROTECT,
        related_name="chunking_batches",
    )
    model = models.CharField(max_length=200, blank=True, verbose_name="Model đã dùng")
    window_pages = models.PositiveSmallIntegerField(
        default=2,
        verbose_name="Số trang mỗi lần gửi",
    )
    overlap_pages = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="Số trang trùng giữa hai lần",
    )
    prompt = models.TextField(verbose_name="Prompt đã dùng")
    status = models.CharField(
        max_length=16,
        choices=ChunkingBatchStatus.choices,
        default=ChunkingBatchStatus.PENDING,
    )
    error_message = models.TextField(blank=True, verbose_name="Lỗi")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chunking_batches",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Lần phân đoạn"
        verbose_name_plural = "Lần phân đoạn"

    def __str__(self) -> str:
        return f"Phân đoạn {self.book.title} ({self.get_status_display()})"


class PageChunkedText(models.Model):
    """Kết quả phân đoạn một trang — không ghi đè OCR / bản làm sạch."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(BookChunkingBatch, on_delete=models.CASCADE, related_name="page_texts")
    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="chunked_texts")
    text_md = models.TextField(blank=True, verbose_name="Văn bản đã phân đoạn")
    break_count = models.PositiveIntegerField(default=0, verbose_name="Số mốc </break>")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["batch", "page"], name="uniq_chunking_batch_page"),
        ]
        ordering = ["page__page_number"]
        verbose_name = "Trang đã phân đoạn"
        verbose_name_plural = "Trang đã phân đoạn"

    def __str__(self) -> str:
        return f"Trang {self.page.page_number} ({self.batch_id})"


class Box(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="boxes")
    reading_order = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=16, choices=ContentType.choices, default=ContentType.UNKNOWN)
    x = models.FloatField(default=0.0)
    y = models.FloatField(default=0.0)
    width = models.FloatField(default=0.0)
    height = models.FloatField(default=0.0)
    confidence = models.FloatField(default=0.0)
    text_md = models.TextField(blank=True, verbose_name="Nội dung OCR")

    class Meta:
        ordering = ["reading_order"]
        indexes = [models.Index(fields=["page", "reading_order"])]

    def __str__(self) -> str:
        return f"Box #{self.reading_order} ({self.content_type})"

    @property
    def x2(self) -> float:
        return float(self.x + self.width)

    @property
    def y2(self) -> float:
        return float(self.y + self.height)


class StagedBookStatus(models.TextChoices):
    PENDING = "pending", "Chờ xử lý"
    PROCESSING = "processing", "Đang xử lý"
    CREATED = "created", "Đã tạo sách"
    DUPLICATE = "duplicate", "Trùng — đã xóa file"
    FAILED = "failed", "Lỗi"


class StagedBook(models.Model):
    """PDF chờ nhập: OCR trang đầu → LLM đặt tên / lọc trùng → tạo Book hoặc xóa file."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_name = models.CharField(max_length=255, verbose_name="Tên file")
    pdf_file = models.FileField(upload_to="staging/pdfs/", blank=True, verbose_name="File PDF")
    status = models.CharField(
        max_length=16,
        choices=StagedBookStatus.choices,
        default=StagedBookStatus.PENDING,
        verbose_name="Trạng thái",
    )
    extracted_title = models.CharField(max_length=255, blank=True, verbose_name="Tên rút trích")
    first_page_text = models.TextField(blank=True, verbose_name="OCR trang đầu")
    matched_book = models.ForeignKey(
        Book,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staging_matches",
        verbose_name="Trùng với sách",
    )
    created_book = models.ForeignKey(
        Book,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staging_sources",
        verbose_name="Sách đã tạo",
    )
    error_message = models.TextField(blank=True, verbose_name="Lỗi")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staged_books",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Sách chờ nhập"
        verbose_name_plural = "Sách chờ nhập"

    def __str__(self) -> str:
        return self.extracted_title or self.original_name

    def delete_pdf(self) -> None:
        if self.pdf_file:
            self.pdf_file.delete(save=False)
            self.pdf_file = ""

    def delete(self, using=None, keep_parents=False):
        self.delete_pdf()
        return super().delete(using=using, keep_parents=keep_parents)


class DuplicateReportStatus(models.TextChoices):
    PENDING = "pending", "Chờ xử lý"
    PROCESSING = "processing", "Đang kiểm tra"
    DONE = "done", "Hoàn thành"
    FAILED = "failed", "Lỗi"


class BookDuplicateReport(models.Model):
    """Kết quả một lần kiểm tra trùng tên trên danh sách sách."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=16,
        choices=DuplicateReportStatus.choices,
        default=DuplicateReportStatus.PENDING,
    )
    groups = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Nhóm trùng",
        help_text="[{canonical, books: [{id, title}]}]",
    )
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicate_reports",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Báo cáo sách trùng"
        verbose_name_plural = "Báo cáo sách trùng"

    def __str__(self) -> str:
        return f"Kiểm tra trùng {self.created_at:%Y-%m-%d %H:%M}"
