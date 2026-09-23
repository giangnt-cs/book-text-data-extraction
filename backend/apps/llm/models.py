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


class ChunkingMethod(models.TextChoices):
    LLM = "llm", "LLM (cửa sổ trang)"
    POINCARE = "poincare", "Regex đề mục + Poincaré"


class EmbedBackend(models.TextChoices):
    OLLAMA = "ollama", "Ollama"
    VLLM = "vllm", "vLLM / OpenAI-compatible"


class ChunkingConfig(models.Model):
    """Chọn phương pháp phân đoạn: LLM cửa sổ trang, hoặc regex đề mục + Poincaré."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    method = models.CharField(
        max_length=16,
        choices=ChunkingMethod.choices,
        default=ChunkingMethod.LLM,
        verbose_name="Phương pháp",
        help_text="LLM: gửi cửa sổ trang cho LLM. Poincaré: cắt đề mục regex rồi embed + Depth Score.",
    )
    provider = models.ForeignKey(
        LLMProvider,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chunking_configs",
        verbose_name="LLM",
        help_text="Chỉ dùng khi phương pháp là LLM. Để trống = LLM mặc định.",
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
    embed_backend = models.CharField(
        max_length=16,
        choices=EmbedBackend.choices,
        default=EmbedBackend.OLLAMA,
        verbose_name="Nguồn embedding",
        help_text="Chỉ dùng khi phương pháp là Poincaré.",
    )
    embed_host = models.CharField(
        max_length=400,
        blank=True,
        default="http://ai1.rnd.lcs.com.vn",
        verbose_name="Host embedding",
        help_text="Ollama: http://host:11434. vLLM: http://host:port/v1.",
    )
    embed_model = models.CharField(
        max_length=200,
        blank=True,
        default="jina-embeddings-v4-text-retrieval-F16:latest",
        verbose_name="Model embedding",
    )
    embed_api_key = models.CharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="API key embedding",
        help_text="Tùy chọn — vLLM/OpenAI-compatible.",
    )
    embed_batch_size = models.PositiveIntegerField(
        default=32,
        verbose_name="Batch embedding",
        help_text="Số cửa sổ gửi embedding mỗi lần.",
    )
    poincare_window = models.PositiveIntegerField(
        default=20,
        verbose_name="Cửa sổ token",
        help_text="Độ dài cửa sổ trượt (token regex).",
    )
    poincare_step = models.PositiveIntegerField(
        default=3,
        verbose_name="Bước trượt",
        help_text="Số token dịch mỗi bước.",
    )
    poincare_c = models.FloatField(
        default=1.0,
        verbose_name="Độ cong c",
        help_text="Độ cong đĩa Poincaré (phải > 0). Mặc định 1.",
    )
    smooth_s = models.PositiveIntegerField(
        default=2,
        verbose_name="Làm mịn s",
        help_text="Khoảng trống lân cận (số chẵn). Mặc định 2.",
    )
    smooth_n = models.PositiveIntegerField(
        default=2,
        verbose_name="Làm mịn n",
        help_text="Số lần lặp làm mịn. Mặc định 2.",
    )
    min_tokens = models.PositiveIntegerField(
        default=200,
        verbose_name="Gom đến khi > token",
        help_text="Gom các phần đề mục ngắn đến khi số token tiktoken vượt ngưỡng này.",
    )
    max_tokens = models.PositiveIntegerField(
        default=200,
        verbose_name="Cắt Poincaré khi > token",
        help_text="Chỉ chạy Poincaré/Depth Score với đoạn đã gom dài hơn số này.",
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
        if self.method == ChunkingMethod.LLM:
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
            return
        if int(self.poincare_window or 0) < 1:
            raise ValidationError({"poincare_window": "Cửa sổ token phải >= 1."})
        if int(self.poincare_step or 0) < 1:
            raise ValidationError({"poincare_step": "Bước trượt phải >= 1."})
        if float(self.poincare_c or 0) <= 0:
            raise ValidationError({"poincare_c": "Độ cong c phải > 0."})
        if int(self.smooth_s or 0) % 2 != 0:
            raise ValidationError({"smooth_s": "Làm mịn s phải là số chẵn."})
        if not (self.embed_host or "").strip() or not (self.embed_model or "").strip():
            raise ValidationError("Poincaré cần host và model embedding.")


class ChunkResizeConfig(models.Model):
    """Tách lại các phân đoạn vượt hạn token."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    provider = models.ForeignKey(
        LLMProvider,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chunk_resize_configs",
        verbose_name="LLM",
        help_text="Chọn LLM đã khai báo. Để trống = LLM mặc định.",
    )
    model = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Model",
        help_text="Để trống = model mặc định của LLM.",
    )
    max_tokens = models.PositiveIntegerField(
        default=5000,
        verbose_name="Giới hạn token mỗi đoạn",
        help_text="Đoạn dài hơn mức này sẽ được gọi phân đoạn lại.",
    )
    prompt = models.TextField(verbose_name="Prompt giảm kích thước")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Giảm kích thước phân đoạn"
        verbose_name_plural = "6b. Giảm kích thước phân đoạn"

    def __str__(self) -> str:
        return "Cấu hình giảm kích thước phân đoạn"

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        limit = int(self.max_tokens or 0)
        if limit < 100:
            raise ValidationError({"max_tokens": "Giới hạn token phải từ 100 trở lên."})


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


class ImageDescribeConfig(models.Model):
    """VLM mô tả hình minh họa khi rút trích ảnh sách."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    provider = models.ForeignKey(
        OCRProvider,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="image_describe_configs",
        verbose_name="VLM",
        help_text="Chọn công cụ VLM đã khai báo (Gemini / Ollama / vLLM / ChatGPT). Để trống = VLM đang kích hoạt.",
    )
    model = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Model",
        help_text="Để trống = model mặc định của VLM đã chọn.",
    )
    prompt = models.TextField(
        verbose_name="Prompt mô tả từ chữ trên hình",
        help_text="Lần 1: chỉ gửi chữ đọc được trên hình (không gửi ảnh).",
    )
    page_prompt = models.TextField(
        verbose_name="Prompt mô tả kèm nội dung trang",
        help_text=(
            "Lần 2 khi lần 1 không đủ thông tin: gửi ảnh crop, chữ trên hình và nội dung trang sách. "
            "Prompt cần nói rõ đây là hình minh họa rút trích từ sách y học cổ truyền Việt Nam."
        ),
    )
    min_description_chars = models.PositiveIntegerField(
        default=40,
        verbose_name="Độ dài tối thiểu của mô tả",
        help_text="Mô tả ngắn hơn mức này (hoặc VLM trả [KHÔNG_ĐỦ_THÔNG_TIN]) sẽ gọi lần 2 với nội dung trang.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Mô tả hình minh họa"
        verbose_name_plural = "6c. Mô tả hình minh họa"

    def __str__(self) -> str:
        return "Cấu hình mô tả hình minh họa"
