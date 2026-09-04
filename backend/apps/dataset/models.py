from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class SourceType(models.TextChoices):
    BOOK = "book", "Sách đã duyệt"
    UPLOAD = "upload", "Tải lên"


class ChunkingBatchStatus(models.TextChoices):
    PENDING = "pending", "Chờ xử lý"
    PROCESSING = "processing", "Đang phân đoạn"
    DONE = "done", "Hoàn thành"
    FAILED = "failed", "Lỗi"


class RagflowSyncStatus(models.TextChoices):
    PENDING = "pending", "Chờ nạp RAG"
    PROCESSING = "processing", "Đang nạp RAG"
    DONE = "done", "Đã nạp RAG"
    FAILED = "failed", "Lỗi nạp RAG"


class SourceDocument(models.Model):
    """Văn bản nguồn cho dataset — snapshot MD sách đã duyệt hoặc file tải lên."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=500, verbose_name="Tên văn bản")
    source_type = models.CharField(
        max_length=16,
        choices=SourceType.choices,
        default=SourceType.UPLOAD,
        verbose_name="Nguồn",
    )
    book = models.OneToOneField(
        "documents.Book",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dataset_source",
        verbose_name="Sách",
    )
    markdown_file = models.FileField(
        upload_to="dataset/sources/",
        blank=True,
        verbose_name="File Markdown",
    )
    original_name = models.CharField(max_length=255, blank=True, verbose_name="Tên file gốc")
    class_source = models.ForeignKey(
        "documents.ClassificationTerm",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="dataset_sources_source",
        verbose_name="Nguồn y học",
        limit_choices_to={"group__slug": "source"},
    )
    class_field = models.ForeignKey(
        "documents.ClassificationTerm",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="dataset_sources_field",
        verbose_name="Lĩnh vực",
        limit_choices_to={"group__slug": "field"},
    )
    class_language = models.ForeignKey(
        "documents.ClassificationTerm",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="dataset_sources_language",
        verbose_name="Ngôn ngữ",
        limit_choices_to={"group__slug": "language"},
    )
    class_doc_type = models.ForeignKey(
        "documents.ClassificationTerm",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="dataset_sources_doc_type",
        verbose_name="Loại tài liệu",
        limit_choices_to={"group__slug": "doc_type"},
    )
    class_era = models.ForeignKey(
        "documents.ClassificationTerm",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="dataset_sources_era",
        verbose_name="Thời đại",
        limit_choices_to={"group__slug": "era"},
    )
    catalog_code = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="Mã văn bản",
        help_text="Tự gán khi đủ Nguồn y học, Lĩnh vực và Ngôn ngữ. Dạng DSG_001.",
    )
    ragflow_document_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="RAGFlow document ID",
    )
    ragflow_status = models.CharField(
        max_length=16,
        blank=True,
        default="",
        choices=RagflowSyncStatus.choices,
        verbose_name="Trạng thái RAGFlow",
    )
    ragflow_error = models.TextField(blank=True, default="", verbose_name="Lỗi RAGFlow")
    ragflow_synced_at = models.DateTimeField(null=True, blank=True, verbose_name="Nạp RAGFlow lúc")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dataset_sources",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Văn bản nguồn"
        verbose_name_plural = "Văn bản nguồn"
        constraints = [
            models.UniqueConstraint(
                fields=["catalog_code"],
                condition=~Q(catalog_code=""),
                name="uniq_dataset_catalog_code",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def classification_codes(self) -> str:
        parts = []
        for attr in ("class_source", "class_field", "class_language", "class_doc_type", "class_era"):
            term = getattr(self, attr)
            parts.append(term.code if term else "—")
        return " · ".join(parts)

    def display_status(
        self,
        *,
        chunking_running: bool | None = None,
        has_chunked: bool | None = None,
        last_failed: bool | None = None,
    ) -> tuple[str, str]:
        if chunking_running is None:
            chunking_running = self.chunking_batches.filter(
                status__in=[ChunkingBatchStatus.PENDING, ChunkingBatchStatus.PROCESSING]
            ).exists()
        if chunking_running:
            return "chunking", "Đang phân đoạn"
        if has_chunked is None:
            has_chunked = self.chunking_batches.filter(status=ChunkingBatchStatus.DONE).exists()
        if has_chunked:
            return "chunked", "Đã phân đoạn"
        if last_failed is None:
            latest = self.chunking_batches.order_by("-created_at").first()
            last_failed = bool(latest and latest.status == ChunkingBatchStatus.FAILED)
        if last_failed:
            return "failed", "Lỗi phân đoạn"
        return "ready", "Chưa phân đoạn"

    def ragflow_status_label(self) -> tuple[str, str]:
        status = (self.ragflow_status or "").strip()
        if status == RagflowSyncStatus.PROCESSING:
            return "processing", "Đang nạp RAG"
        if status == RagflowSyncStatus.PENDING:
            return "pending", "Chờ nạp RAG"
        if status == RagflowSyncStatus.DONE:
            return "done", "Đã nạp RAG"
        if status == RagflowSyncStatus.FAILED:
            return "failed", "Lỗi nạp RAG"
        return "idle", "Chưa nạp RAG"


class SourceSegment(models.Model):
    """Một trang (hoặc khối) trong văn bản nguồn — đơn vị cửa sổ phân đoạn."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        SourceDocument,
        on_delete=models.CASCADE,
        related_name="segments",
    )
    page_number = models.PositiveIntegerField(verbose_name="Thứ tự")
    text_md = models.TextField(blank=True, verbose_name="Nội dung")

    class Meta:
        ordering = ["page_number"]
        verbose_name = "Trang nguồn"
        verbose_name_plural = "Trang nguồn"
        constraints = [
            models.UniqueConstraint(fields=["document", "page_number"], name="uniq_dataset_segment_page"),
        ]

    def __str__(self) -> str:
        return f"{self.document.title} — trang {self.page_number}"


class SourceChunkingBatch(models.Model):
    """Một lần chạy phân đoạn cho văn bản nguồn."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        SourceDocument,
        on_delete=models.CASCADE,
        related_name="chunking_batches",
    )
    provider = models.ForeignKey(
        "llm.LLMProvider",
        on_delete=models.PROTECT,
        related_name="dataset_chunking_batches",
    )
    model = models.CharField(max_length=200, blank=True, verbose_name="Model đã dùng")
    window_pages = models.PositiveSmallIntegerField(default=2, verbose_name="Số trang mỗi lần gửi")
    overlap_pages = models.PositiveSmallIntegerField(default=1, verbose_name="Số trang trùng giữa hai lần")
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
        related_name="dataset_chunking_batches",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Lần phân đoạn"
        verbose_name_plural = "Lần phân đoạn"

    def __str__(self) -> str:
        return f"Phân đoạn {self.document.title} ({self.get_status_display()})"


class SourceChunkedText(models.Model):
    """Kết quả phân đoạn một trang nguồn — không ghi đè file MD gốc."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(
        SourceChunkingBatch,
        on_delete=models.CASCADE,
        related_name="page_texts",
    )
    segment = models.ForeignKey(
        SourceSegment,
        on_delete=models.CASCADE,
        related_name="chunked_texts",
    )
    text_md = models.TextField(blank=True, verbose_name="Văn bản đã phân đoạn")
    break_count = models.PositiveIntegerField(default=0, verbose_name="Số mốc </break>")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["batch", "segment"], name="uniq_dataset_chunk_batch_segment"),
        ]
        verbose_name = "Trang đã phân đoạn"
        verbose_name_plural = "Trang đã phân đoạn"

    def __str__(self) -> str:
        return f"Trang {self.segment.page_number} ({self.batch_id})"


class SourceChunk(models.Model):
    """Một khối nội dung sau khi tách MD đã phân đoạn theo </break>."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        SourceDocument,
        on_delete=models.CASCADE,
        related_name="chunks",
        verbose_name="Văn bản",
    )
    position = models.PositiveIntegerField(verbose_name="Thứ tự")
    page_number = models.PositiveIntegerField(default=1, verbose_name="Trang")
    page_seq = models.PositiveIntegerField(default=1, verbose_name="Thứ tự trong trang")
    chunk_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="Mã phân đoạn",
        help_text="Dạng DSG_fff_ccc_ppp: mã văn bản, trang, thứ tự trong trang.",
    )
    text_md = models.TextField(blank=True, verbose_name="Nội dung")
    ragflow_chunk_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="RAGFlow chunk ID",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position"]
        verbose_name = "Khối phân đoạn"
        verbose_name_plural = "Khối phân đoạn"
        constraints = [
            models.UniqueConstraint(fields=["document", "position"], name="uniq_dataset_chunk_position"),
            models.UniqueConstraint(
                fields=["chunk_code"],
                condition=~Q(chunk_code=""),
                name="uniq_dataset_chunk_code",
            ),
        ]

    def __str__(self) -> str:
        code = (self.chunk_code or "").strip()
        if code:
            return code
        return f"{self.document.title} — đoạn {self.position}"


class ChunkedDocument(SourceDocument):
    """Proxy: danh sách văn bản đã có phân đoạn tách từ </break>."""

    class Meta:
        proxy = True
        verbose_name = "Phân đoạn"
        verbose_name_plural = "Phân đoạn"


class RAGFlowConfig(models.Model):
    """Khai báo server RAGFlow để nạp phân đoạn."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    base_url = models.CharField(
        max_length=400,
        blank=True,
        verbose_name="Base URL",
        help_text="Ví dụ http://host:80 — không kèm /api/v1.",
    )
    api_key = models.CharField(max_length=500, blank=True, verbose_name="API Key")
    dataset_id = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Dataset ID",
        help_text="ID knowledge base trên RAGFlow.",
    )
    meta_timeout = models.PositiveIntegerField(
        default=30,
        verbose_name="Timeout tạo document (giây)",
        help_text="Tạo document rỗng và PATCH metadata.",
    )
    chunk_timeout = models.PositiveIntegerField(
        default=300,
        verbose_name="Timeout Add/PATCH chunk (giây)",
        help_text="Mỗi chunk RAGFlow embed rồi ghi vector DB — có thể rất chậm.",
    )
    embed_prefix = models.CharField(
        max_length=64,
        default="Passage: ",
        verbose_name="Tiền tố embed",
        help_text=(
            "Gắn trước nội dung khi gửi RAGFlow (jina-embeddings-v4-text-retrieval). "
            "Không lưu vào phân đoạn local."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "RAGFlow"
        verbose_name_plural = "RAGFlow"

    def __str__(self) -> str:
        return "Cấu hình RAGFlow"
