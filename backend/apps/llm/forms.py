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
        fields = ("provider", "model", "window_pages", "overlap_pages", "prompt", "stitch_prompt")
        widgets = {
            **LLMFeatureConfigForm.Meta.widgets,
            "window_pages": forms.NumberInput(attrs={"min": "1", "max": "20"}),
            "overlap_pages": forms.NumberInput(attrs={"min": "0", "max": "19"}),
        }

    def clean(self):
        cleaned = super().clean()
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
