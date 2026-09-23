from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from .models import LLM_TO_VLM_BACKEND, LLMProvider, OCRProvider, OCR_BACKEND_GROUPED_CHOICES, VLM_BACKENDS


class LLMProviderForm(forms.ModelForm):
    class Meta:
        from .models import LLMProvider

        model = LLMProvider
        fields = (
            "name",
            "backend",
            "is_active",
            "is_default",
            "base_url",
            "api_key",
            "model",
            "temperature",
            "enable_thinking",
        )
        widgets = {
            "api_key": forms.PasswordInput(render_value=True, attrs={"autocomplete": "new-password"}),
            "base_url": forms.TextInput(attrs={"placeholder": "https://api.openai.com/v1 hoặc http://host:8000/v1 (vLLM)"}),
            "model": forms.TextInput(attrs={"id": "id_model", "placeholder": "Chọn model sau khi tải danh sách"}),
            "temperature": forms.NumberInput(attrs={"step": "0.1", "min": "0", "max": "2"}),
        }


TextCleaningProviderForm = LLMProviderForm


class LLMFeatureConfigForm(forms.ModelForm):
    """Form chức năng (làm sạch / phân loại): chọn LLM + model."""

    class Meta:
        fields = ()
        widgets = {
            "model": forms.TextInput(
                attrs={"id": "id_model", "placeholder": "Để trống = model mặc định của LLM"}
            ),
        }


class TextCleaningConfigForm(LLMFeatureConfigForm):
    class Meta(LLMFeatureConfigForm.Meta):
        from .models import TextCleaningConfig

        model = TextCleaningConfig
        fields = ("provider", "model", "prompt")


class BookClassifyConfigForm(LLMFeatureConfigForm):
    class Meta(LLMFeatureConfigForm.Meta):
        from .models import BookClassifyConfig

        model = BookClassifyConfig
        fields = ("provider", "model", "max_pages", "prompt")


class ChunkingConfigForm(LLMFeatureConfigForm):
    class Meta(LLMFeatureConfigForm.Meta):
        from .models import ChunkingConfig

        model = ChunkingConfig
        fields = (
            "method",
            "provider",
            "model",
            "window_pages",
            "overlap_pages",
            "prompt",
            "stitch_prompt",
            "embed_backend",
            "embed_host",
            "embed_model",
            "embed_api_key",
            "embed_batch_size",
            "poincare_window",
            "poincare_step",
            "poincare_c",
            "smooth_s",
            "smooth_n",
            "min_tokens",
            "max_tokens",
        )
        widgets = {
            **LLMFeatureConfigForm.Meta.widgets,
            "window_pages": forms.NumberInput(attrs={"min": "1", "max": "20"}),
            "overlap_pages": forms.NumberInput(attrs={"min": "0", "max": "19"}),
            "embed_api_key": forms.PasswordInput(render_value=True, attrs={"autocomplete": "new-password"}),
            "poincare_c": forms.NumberInput(attrs={"step": "0.1", "min": "0.01"}),
            "smooth_s": forms.NumberInput(attrs={"min": "0", "step": "2"}),
            "embed_batch_size": forms.NumberInput(attrs={"min": "1"}),
            "poincare_window": forms.NumberInput(attrs={"min": "1"}),
            "poincare_step": forms.NumberInput(attrs={"min": "1"}),
            "min_tokens": forms.NumberInput(attrs={"min": "0"}),
            "max_tokens": forms.NumberInput(attrs={"min": "0"}),
        }

    def clean(self):
        from .models import ChunkingMethod

        cleaned = super().clean()
        if cleaned.get("method") == ChunkingMethod.POINCARE:
            if int(cleaned.get("smooth_s") or 0) % 2 != 0:
                self.add_error("smooth_s", "Làm mịn s phải là số chẵn.")
            if float(cleaned.get("poincare_c") or 0) <= 0:
                self.add_error("poincare_c", "Độ cong c phải > 0.")
            if not (cleaned.get("embed_host") or "").strip():
                self.add_error("embed_host", "Nhập host embedding.")
            if not (cleaned.get("embed_model") or "").strip():
                self.add_error("embed_model", "Nhập model embedding.")
            return cleaned
        window = cleaned.get("window_pages")
        overlap = cleaned.get("overlap_pages")
        if window is None or overlap is None:
            return cleaned
        if window < 1 or window > 20:
            self.add_error("window_pages", "Số trang mỗi lần gửi phải từ 1 đến 20.")
        if overlap < 0:
            self.add_error("overlap_pages", "Số trang trùng không được âm.")
        elif overlap >= window:
            self.add_error("overlap_pages", "Số trang trùng phải nhỏ hơn số trang gửi mỗi lần.")
        return cleaned


class ChunkResizeConfigForm(LLMFeatureConfigForm):
    class Meta(LLMFeatureConfigForm.Meta):
        from .models import ChunkResizeConfig

        model = ChunkResizeConfig
        fields = ("provider", "model", "max_tokens", "prompt")
        widgets = {
            **LLMFeatureConfigForm.Meta.widgets,
            "max_tokens": forms.NumberInput(attrs={"min": "100", "step": "100"}),
        }

    def clean_max_tokens(self):
        value = self.cleaned_data.get("max_tokens")
        if value is not None and int(value) < 100:
            raise ValidationError("Giới hạn token phải từ 100 trở lên.")
        return value


class BookIngestConfigForm(LLMFeatureConfigForm):
    class Meta(LLMFeatureConfigForm.Meta):
        from .models import BookIngestConfig

        model = BookIngestConfig
        fields = ("provider", "model", "prompt")


class BookDedupeConfigForm(LLMFeatureConfigForm):
    class Meta(LLMFeatureConfigForm.Meta):
        from .models import BookDedupeConfig

        model = BookDedupeConfig
        fields = ("provider", "model", "batch_size", "prompt")


class ImageDescribeConfigForm(LLMFeatureConfigForm):
    class Meta(LLMFeatureConfigForm.Meta):
        from .models import ImageDescribeConfig

        model = ImageDescribeConfig
        fields = ("provider", "model", "min_description_chars", "prompt", "page_prompt")
        widgets = {
            **LLMFeatureConfigForm.Meta.widgets,
            "min_description_chars": forms.NumberInput(attrs={"min": "1", "step": "5"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = OCRProvider.objects.filter(backend__in=VLM_BACKENDS).order_by("name")
        self.fields["provider"].queryset = qs
        self.fields["provider"].label = "VLM"
        self.fields["provider"].help_text = (
            "Chọn công cụ VLM đã khai báo ở «Công cụ OCR». Để trống = VLM đang kích hoạt."
        )

    def clean_min_description_chars(self):
        value = self.cleaned_data.get("min_description_chars")
        if value is not None and int(value) < 1:
            raise ValidationError("Độ dài tối thiểu phải từ 1 trở lên.")
        return value

    def clean_provider(self):
        provider = self.cleaned_data.get("provider")
        if provider is not None and provider.backend not in VLM_BACKENDS:
            raise ValidationError("Chỉ chọn công cụ VLM (Gemini / Ollama / vLLM / ChatGPT / DeepSeek).")
        return provider


class OCRProviderForm(forms.ModelForm):
    class Meta:
        model = OCRProvider
        fields = (
            "name",
            "backend",
            "llm",
            "is_active",
            "is_default",
            "fallback_provider",
            "base_url",
            "api_key",
            "model",
        )
        widgets = {
            "api_key": forms.PasswordInput(render_value=True, attrs={"autocomplete": "new-password"}),
            "base_url": forms.TextInput(attrs={"placeholder": "https://api.openai.com/v1"}),
            "model": forms.TextInput(attrs={"id": "id_model", "placeholder": "Chọn model sau khi tải danh sách"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["backend"].choices = OCR_BACKEND_GROUPED_CHOICES
        qs = OCRProvider.objects.order_by("name")
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        self.fields["fallback_provider"].queryset = qs
        self.fields["fallback_provider"].required = False
        self.fields["llm"].required = False
        llm_qs = LLMProvider.objects.order_by("name")
        if self.instance.pk and self.instance.llm_id:
            llm_qs = llm_qs.filter(Q(is_active=True) | Q(pk=self.instance.llm_id))
        else:
            llm_qs = llm_qs.filter(is_active=True)
        self.fields["llm"].queryset = llm_qs

    def clean(self):
        cleaned = super().clean()
        backend = cleaned.get("backend")
        llm = cleaned.get("llm")
        if llm and backend in VLM_BACKENDS:
            expected = LLM_TO_VLM_BACKEND.get(llm.backend)
            if expected and backend != expected:
                self.add_error(
                    "llm",
                    f"LLM «{llm}» không khớp loại VLM đã chọn. "
                    f"Chọn LLM {llm.get_backend_display()} tương ứng, hoặc đổi loại công cụ.",
                )
        return cleaned

    def clean_fallback_provider(self):
        fallback = self.cleaned_data.get("fallback_provider")
        if not fallback or not self.instance.pk:
            return fallback
        if fallback.pk == self.instance.pk:
            raise ValidationError("Không thể fallback về chính công cụ này.")
        seen = {self.instance.pk}
        current = fallback
        while current is not None:
            if current.pk in seen:
                raise ValidationError("Chuỗi fallback tạo vòng lặp. Chọn công cụ khác.")
            seen.add(current.pk)
            current = current.fallback_provider
        return fallback
