from __future__ import annotations

import os
import uuid
from typing import Optional

from django.db import models


class OCRBackend(models.TextChoices):
    GOOGLE_VISION = "google_vision", "Google Cloud Vision — trang thường (chữ)"
    GOOGLE_DOCUMENT_AI_OCR = (
        "google_document_ai_ocr",
        "Document AI · Enterprise OCR — trang thường (chữ)",
    )
    GOOGLE_DOCUMENT_AI_LAYOUT = (
        "google_document_ai_layout",
        "Document AI · Layout Parser + OCR — trang có bảng",
    )
    GOOGLE_DOCUMENT_AI = (
        "google_document_ai",
        "Document AI · Tự theo loại trang (chữ → OCR, bảng → Layout Parser)",
    )
    VLM_GEMINI = "vlm_gemini", "Gemini (VLM) — trang bảng / công thức / hình"
    VLM_OLLAMA = "vlm_ollama", "Ollama (VLM) — trang bảng / công thức / hình (local)"
    VLM_VLLM = "vlm_vllm", "vLLM (VLM) — trang bảng / công thức / hình (OpenAI-compatible)"
    VLM_OPENAI = "vlm_openai", "ChatGPT (VLM) — trang bảng / công thức / hình"
    VLM_DEEPSEEK = "vlm_deepseek", "DeepSeek (VLM) — trang bảng / công thức / hình"
    PADDLE_OCR = "paddle_ocr", "PaddleOCR — trang thường (chữ)"
    MINERU = "mineru", "MinerU — PDF phức tạp"
    OLLAMA = "ollama", "Ollama (cũ)"


DOCUMENT_AI_BACKENDS = {
    OCRBackend.GOOGLE_DOCUMENT_AI,
    OCRBackend.GOOGLE_DOCUMENT_AI_OCR,
    OCRBackend.GOOGLE_DOCUMENT_AI_LAYOUT,
}


VLM_BACKENDS = {
    OCRBackend.VLM_GEMINI,
    OCRBackend.VLM_OPENAI,
    OCRBackend.VLM_OLLAMA,
    OCRBackend.VLM_VLLM,
    OCRBackend.VLM_DEEPSEEK,
    OCRBackend.OLLAMA,
}


OCR_BACKEND_GROUPED_CHOICES = [
    (
        "Google Cloud Vision",
        [(OCRBackend.GOOGLE_VISION.value, OCRBackend.GOOGLE_VISION.label)],
    ),
    (
        "Google Document AI",
        [
            (OCRBackend.GOOGLE_DOCUMENT_AI_OCR.value, OCRBackend.GOOGLE_DOCUMENT_AI_OCR.label),
            (OCRBackend.GOOGLE_DOCUMENT_AI_LAYOUT.value, OCRBackend.GOOGLE_DOCUMENT_AI_LAYOUT.label),
            (OCRBackend.GOOGLE_DOCUMENT_AI.value, OCRBackend.GOOGLE_DOCUMENT_AI.label),
        ],
    ),
    (
        "VLM (ảnh → text)",
        [
            (OCRBackend.VLM_GEMINI.value, OCRBackend.VLM_GEMINI.label),
            (OCRBackend.VLM_OLLAMA.value, OCRBackend.VLM_OLLAMA.label),
            (OCRBackend.VLM_VLLM.value, OCRBackend.VLM_VLLM.label),
            (OCRBackend.VLM_OPENAI.value, OCRBackend.VLM_OPENAI.label),
            (OCRBackend.VLM_DEEPSEEK.value, OCRBackend.VLM_DEEPSEEK.label),
        ],
    ),
    (
        "Khác",
        [
            (OCRBackend.PADDLE_OCR.value, OCRBackend.PADDLE_OCR.label),
            (OCRBackend.MINERU.value, OCRBackend.MINERU.label),
            (OCRBackend.OLLAMA.value, OCRBackend.OLLAMA.label),
        ],
    ),
]


class OCRProvider(models.Model):
    """Công cụ OCR / VLM — người dùng thêm và cấu hình trong Admin."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True, verbose_name="Tên cấu hình")
    backend = models.CharField(max_length=40, choices=OCRBackend.choices, verbose_name="Loại công cụ")
    model = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Model",
        help_text="Chọn sau khi bấm «Tải danh sách model».",
    )
    base_url = models.CharField(
        max_length=400,
        blank=True,
        verbose_name="Base URL",
        help_text="URL gốc API (Ollama, OpenAI-compatible, MinerU, …).",
    )
    api_key = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="API Key",
        help_text="API Key hoặc đường dẫn file credentials (Google Vision).",
    )
    api_key_env = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Biến môi trường API key (legacy)",
        help_text="Chỉ dùng khi chưa điền API Key trực tiếp.",
    )
    llm = models.ForeignKey(
        "LLMProvider",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ocr_providers",
        verbose_name="LLM",
        help_text="Với Gemini / ChatGPT / Ollama / vLLM / DeepSeek: chọn LLM đã khai báo để dùng chung API key.",
    )
    params = models.JSONField(default=dict, blank=True, verbose_name="Tham số bổ sung")
    is_active = models.BooleanField(default=True, verbose_name="Kích hoạt")
    is_default = models.BooleanField(
        default=False,
        verbose_name="Mặc định",
        help_text="Công cụ dự phòng khi cấu hình trang trỏ tới provider không kích hoạt.",
    )
    fallback_provider = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fallback_sources",
        verbose_name="Fallback",
        help_text=(
            "Khi công cụ này lỗi hoặc bị Gemini chặn (RECITATION/SAFETY), "
            "chuyển sang công cụ này. Có thể xâu chuỗi, ví dụ Gemini → Ollama → Document AI."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Công cụ OCR"
        verbose_name_plural = "2. Công cụ OCR"
        ordering = ["name"]

    def resolve_api_key(self) -> Optional[str]:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.getenv(self.api_key_env) or None
        return None

    def resolve_llm_credentials(self) -> tuple[Optional[str], str, str]:
        """api_key, base_url, model — ưu tiên LLM đã liên kết (chỉ VLM)."""
        llm = self.llm if self.llm_id and self.backend in VLM_BACKENDS else None
        api_key = (llm.resolve_api_key() if llm else None) or self.resolve_api_key()
        base_url = ((llm.base_url if llm else "") or self.base_url or "").strip()
        model = (self.model or (llm.model if llm else "") or "").strip()
        return api_key, base_url, model

    @property
    def is_vlm(self) -> bool:
        return self.backend in VLM_BACKENDS

    def save(self, *args, **kwargs):
        if self.is_default:
            OCRProvider.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        if self.backend not in VLM_BACKENDS:
            self.llm = None
        super().save(*args, **kwargs)

    def next_active_fallback(self, tried_ids: set | None = None) -> Optional["OCRProvider"]:
        """Công cụ fallback tiếp theo còn kích hoạt, bỏ qua vòng lặp và đã thử."""
        seen = set(tried_ids or ())
        seen.add(self.pk)
        current = self
        while current.fallback_provider_id:
            nxt = current.fallback_provider
            if nxt is None or nxt.pk in seen:
                return None
            seen.add(nxt.pk)
            if nxt.is_active:
                return nxt
            current = nxt
        return None

    def __str__(self) -> str:
        return f"{self.name} ({self.get_backend_display()})"


class PageOCRConfig(models.Model):
    """Ánh xạ loại trang → công cụ OCR (OCR cả trang, không tách box)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    page_mode = models.CharField(
        max_length=16,
        choices=[
            ("standard", "Trang thường (không bảng/công thức)"),
            ("layout", "Trang có bảng hoặc công thức"),
        ],
        unique=True,
        verbose_name="Loại trang",
    )
    provider = models.ForeignKey(
        OCRProvider,
        on_delete=models.PROTECT,
        related_name="page_configs",
        verbose_name="Công cụ OCR",
    )
    notes = models.CharField(max_length=255, blank=True, verbose_name="Ghi chú")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "OCR theo loại trang"
        verbose_name_plural = "1. OCR theo loại trang"
        ordering = ["page_mode"]

    def __str__(self) -> str:
        return f"{self.get_page_mode_display()} → {self.provider.name}"


class LLMBackend(models.TextChoices):
    GEMINI = "llm_gemini", "Gemini"
    OPENAI = "llm_openai", "ChatGPT"
    OLLAMA = "llm_ollama", "Ollama"
    VLLM = "llm_vllm", "vLLM"
    DEEPSEEK = "llm_deepseek", "DeepSeek"


TextCleaningBackend = LLMBackend
TEXT_CLEANING_BACKENDS = {
    LLMBackend.GEMINI,
    LLMBackend.OPENAI,
    LLMBackend.OLLAMA,
    LLMBackend.VLLM,
    LLMBackend.DEEPSEEK,
}

LLM_TO_VLM_BACKEND = {
    LLMBackend.GEMINI: OCRBackend.VLM_GEMINI,
    LLMBackend.OPENAI: OCRBackend.VLM_OPENAI,
    LLMBackend.OLLAMA: OCRBackend.VLM_OLLAMA,
    LLMBackend.VLLM: OCRBackend.VLM_VLLM,
    LLMBackend.DEEPSEEK: OCRBackend.VLM_DEEPSEEK,
}


class LLMProvider(models.Model):
    """Khai báo LLM dùng chung — API key / URL / model mặc định."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True, verbose_name="Tên")
    backend = models.CharField(max_length=32, choices=LLMBackend.choices, verbose_name="Loại LLM")
    model = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Model mặc định",
        help_text="Model dùng khi chức năng không chọn model riêng.",
    )
    base_url = models.CharField(max_length=400, blank=True, verbose_name="Base URL")
    api_key = models.CharField(max_length=500, blank=True, verbose_name="API Key")
    api_key_env = models.CharField(max_length=120, blank=True, verbose_name="Biến môi trường API key")
    temperature = models.FloatField(
        default=0.0,
        verbose_name="Temperature",
        help_text="0 = ổn định (OCR, rút tên, làm sạch). Tăng dần nếu muốn đa dạng hơn. Chỉ Ollama và vLLM.",
    )
    enable_thinking = models.BooleanField(
        default=False,
        verbose_name="Bật thinking",
        help_text="Chuỗi suy nghĩ (Ollama think / Qwen trên vLLM). Tắt thì nhanh hơn.",
    )
    params = models.JSONField(default=dict, blank=True, verbose_name="Tham số bổ sung")
    is_active = models.BooleanField(default=True, verbose_name="Kích hoạt")
    is_default = models.BooleanField(default=False, verbose_name="Mặc định")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "LLM"
        verbose_name_plural = "3. LLM"
        ordering = ["name"]

    def resolve_api_key(self) -> Optional[str]:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.getenv(self.api_key_env) or None
        return None

    def resolve_model(self, override: str = "") -> str:
        return (override or "").strip() or (self.model or "").strip()

    def generation_kwargs(self) -> dict:
        thinking = bool(self.enable_thinking)
        try:
            temp = float(self.temperature)
        except (TypeError, ValueError):
            temp = 0.0
        temp = max(0.0, min(temp, 2.0))
        return {
            "temperature": temp,
            "enable_thinking": thinking,
            "disable_thinking": not thinking,
        }

    def save(self, *args, **kwargs):
        if self.is_default:
            LLMProvider.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        extra = f" · {self.model}" if self.model else ""
        return f"{self.name} ({self.get_backend_display()}{extra})"


TextCleaningProvider = LLMProvider


class TextCleaningPrompt(models.Model):
    """Prompt hệ thống cho làm sạch văn bản (legacy, đồng bộ với TextCleaningConfig)."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    prompt = models.TextField(verbose_name="Prompt làm sạch")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Prompt làm sạch văn bản"
        verbose_name_plural = "Prompt làm sạch văn bản"

    def __str__(self) -> str:
        return "Prompt làm sạch văn bản"


class TextCleaningConfig(models.Model):
    """Chọn LLM + model + prompt cho làm sạch văn bản."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    provider = models.ForeignKey(
        LLMProvider,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cleaning_configs",
        verbose_name="LLM",
        help_text="Chọn LLM đã khai báo. Để trống = LLM mặc định.",
    )
    model = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Model",
        help_text="Để trống = model mặc định của LLM.",
    )
    prompt = models.TextField(verbose_name="Prompt làm sạch")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Làm sạch văn bản"
        verbose_name_plural = "4. Làm sạch văn bản"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        TextCleaningPrompt.objects.update_or_create(pk=1, defaults={"prompt": self.prompt})

    def __str__(self) -> str:
        return "Cấu hình làm sạch văn bản"


class BookClassifyConfig(models.Model):
    """Chọn LLM + model + prompt cho phân loại / metadata sách."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    provider = models.ForeignKey(
        LLMProvider,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="classify_configs",
        verbose_name="LLM",
        help_text="Chọn LLM đã khai báo. Để trống = LLM mặc định.",
    )
    model = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Model",
        help_text="Để trống = model mặc định của LLM.",
    )
    max_pages = models.PositiveSmallIntegerField(
        default=20,
        verbose_name="Số trang đầu đọc",
        help_text="Đọc tối đa số trang này (OCR/làm sạch, hoặc chữ trong PDF nếu chưa OCR).",
    )
    prompt = models.TextField(verbose_name="Prompt phân loại")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Phân loại sách"
        verbose_name_plural = "5. Phân loại sách"

    def __str__(self) -> str:
        return "Cấu hình phân loại sách"


class ChunkingConfig(models.Model):
    """Chọn LLM + model + prompt + cửa sổ trang cho phân đoạn (LLM trả chỉ số đoạn)."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    provider = models.ForeignKey(
        LLMProvider,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chunking_configs",
        verbose_name="LLM",
        help_text="Chọn LLM đã khai báo. Để trống = LLM mặc định.",
    )
    model = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Model",
        help_text="Để trống = model mặc định của LLM.",
    )
    window_pages = models.PositiveSmallIntegerField(
        default=2,
        verbose_name="Số trang mỗi lần gửi",
        help_text="Mỗi lần gọi LLM gửi bấy nhiêu trang liên tiếp (1–20). Mặc định 2.",
    )
    overlap_pages = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="Số trang trùng giữa hai lần",
        help_text=(
            "Số trang cuối của lần trước được gửi lại làm ngữ cảnh. "
            "Phải nhỏ hơn số trang mỗi lần. 0 = không trùng. Mặc định 1."
        ),
    )
    prompt = models.TextField(verbose_name="Prompt phân đoạn")
    stitch_prompt = models.TextField(
        blank=True,
        verbose_name="Prompt nối trang",
        help_text="LLM chỉ trả true hoặc false — có nối đoạn cuối trang trước với đoạn đầu trang sau.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Phân đoạn"
        verbose_name_plural = "6. Phân đoạn"

    def __str__(self) -> str:
        return "Cấu hình phân đoạn"

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        window = int(self.window_pages or 0)
        overlap = int(self.overlap_pages or 0)
        if window < 1 or window > 20:
            raise ValidationError({"window_pages": "Số trang mỗi lần gửi phải từ 1 đến 20."})
        if overlap < 0:
            raise ValidationError({"overlap_pages": "Số trang trùng không được âm."})
        if overlap >= window:
            raise ValidationError(
                {"overlap_pages": "Số trang trùng phải nhỏ hơn số trang gửi mỗi lần."}
            )


class BookIngestConfig(models.Model):
    """LLM rút trích tên từ trang đầu và đối chiếu sách đã có."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    provider = models.ForeignKey(
        LLMProvider,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ingest_configs",
        verbose_name="LLM",
        help_text="Chọn LLM đã khai báo. Để trống = LLM mặc định.",
    )
    model = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Model",
        help_text="Để trống = model mặc định của LLM.",
    )
    prompt = models.TextField(verbose_name="Prompt tạo sách từ upload")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Tạo sách từ upload"
        verbose_name_plural = "7. Tạo sách từ upload"

    def __str__(self) -> str:
        return "Cấu hình tạo sách từ upload"


class BookDedupeConfig(models.Model):
    """LLM kiểm tra trùng tên trong từng lô ~30 sách."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    provider = models.ForeignKey(
        LLMProvider,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dedupe_configs",
        verbose_name="LLM",
        help_text="Chọn LLM đã khai báo. Để trống = LLM mặc định.",
    )
    model = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Model",
        help_text="Để trống = model mặc định của LLM.",
    )
    batch_size = models.PositiveSmallIntegerField(
        default=30,
        verbose_name="Số tên mỗi lần gọi",
        help_text="Mỗi lần gửi tối đa số tên này cho LLM.",
    )
    prompt = models.TextField(verbose_name="Prompt kiểm tra trùng tên")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Kiểm tra sách trùng"
        verbose_name_plural = "8. Kiểm tra sách trùng"

    def __str__(self) -> str:
        return "Cấu hình kiểm tra sách trùng"
