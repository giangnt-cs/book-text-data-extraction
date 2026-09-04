from __future__ import annotations

import html

from django.contrib import admin, messages
from django.http import JsonResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin

from .constants import DEFAULT_BASE_URL, OCR_TOOL_CATALOG, PAGE_MODE_META, TOOL_META, catalog_entry
from .forms import (
    BookClassifyConfigForm,
    BookDedupeConfigForm,
    BookIngestConfigForm,
    ChunkingConfigForm,
    LLMProviderForm,
    OCRProviderForm,
    TextCleaningConfigForm,
)
from .model_discovery import list_text_models, list_vision_models
from .models import (
    DOCUMENT_AI_BACKENDS,
    BookClassifyConfig,
    BookDedupeConfig,
    BookIngestConfig,
    ChunkingConfig,
    LLMBackend,
    LLMProvider,
    OCRBackend,
    OCRProvider,
    PageOCRConfig,
    TextCleaningConfig,
    VLM_BACKENDS,
)


def _tool_badge(backend: str, *, name: str = "") -> str:
    meta = TOOL_META.get(backend, {"label": backend, "icon": "⚙️", "color": "#64748b", "bg": "#f1f5f9"})
    label = html.escape(name or meta["label"])
    return format_html(
        '<span style="display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;'
        'background:{};color:{};font-weight:600;font-size:12px;">'
        '<span style="font-size:15px;line-height:1;">{}</span> {}</span>',
        meta["bg"],
        meta["color"],
        meta["icon"],
        label,
    )


def _page_mode_badge(page_mode: str) -> str:
    meta = PAGE_MODE_META.get(page_mode, {"label": page_mode, "icon": "📄", "color": "#64748b", "hint": ""})
    return format_html(
        '<span style="display:inline-flex;align-items:center;gap:6px;font-weight:600;">'
        '<span style="font-size:16px;">{}</span>'
        '<span style="color:{};">{}</span></span>'
        '<div style="font-size:12px;color:#64748b;margin-top:2px;">{}</div>',
        meta["icon"],
        meta["color"],
        html.escape(meta["label"]),
        html.escape(meta.get("hint", "")),
    )


@admin.register(PageOCRConfig)
class PageOCRConfigAdmin(ModelAdmin):
    list_display = ("page_mode_display", "provider_display", "provider_backend", "notes", "updated_at")
    list_display_links = ("page_mode_display",)
    list_filter = ("provider__backend",)
    autocomplete_fields = ("provider",)
    list_before_template = "admin/llm/ocr_tool_catalog.html"
    change_form_before_template = "admin/llm/ocr_tool_catalog.html"
    fieldsets = (
        (
            None,
            {
                "fields": ("page_mode", "provider", "notes"),
                "description": (
                    "Gán một công cụ OCR cho từng loại trang. "
                    "Trang thường: Cloud Vision hoặc Document AI Enterprise OCR. "
                    "Trang có bảng: Document AI Layout Parser + OCR. "
                    "Trang có công thức/hình: Gemini hoặc Ollama."
                ),
            },
        ),
    )

    def has_add_permission(self, request):
        return PageOCRConfig.objects.count() < 2

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Loại trang", ordering="page_mode")
    def page_mode_display(self, obj):
        return mark_safe(_page_mode_badge(obj.page_mode))

    @admin.display(description="Công cụ OCR", ordering="provider__name")
    def provider_display(self, obj):
        return mark_safe(_tool_badge(obj.provider.backend, name=obj.provider.name))

    @admin.display(description="Backend", ordering="provider__backend")
    def provider_backend(self, obj):
        return obj.provider.get_backend_display()

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["ocr_tool_catalog"] = OCR_TOOL_CATALOG
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(OCRProvider)
class OCRProviderAdmin(ModelAdmin):
    form = OCRProviderForm
    change_form_template = "admin/llm/ocrprovider/change_form.html"
    list_before_template = "admin/llm/ocr_tool_catalog.html"
    change_form_before_template = "admin/llm/ocr_tool_catalog.html"
    autocomplete_fields = ("fallback_provider", "llm")
    list_display = (
        "tool_display",
        "name",
        "suitable_for",
        "llm",
        "fallback_display",
        "model",
        "base_url",
        "api_key_set",
        "is_active",
        "is_default",
    )
    list_filter = ("backend", "is_active")
    search_fields = ("name", "model")
    fieldsets = (
        (
            None,
            {
                "fields": ("name", "backend", "is_active", "is_default"),
                "description": (
                    "Chọn đúng option của công cụ. Document AI có 3 lựa chọn: "
                    "Enterprise OCR (trang chữ), Layout Parser + OCR (trang bảng), "
                    "hoặc tự theo loại trang. Xem bảng hướng dẫn phía trên danh sách công cụ."
                ),
            },
        ),
        (
            "Fallback khi lỗi",
            {
                "fields": ("fallback_provider",),
                "description": (
                    "Khi Gemini bị chặn (RECITATION) hoặc công cụ lỗi, chuyển sang công cụ này. "
                    "Gợi ý: Gemini → Ollama, rồi trên Ollama chọn Document AI · Layout Parser + OCR."
                ),
            },
        ),
        (
            "LLM dùng chung",
            {
                "fields": ("llm",),
                "description": (
                    "Với Gemini / ChatGPT / Ollama / DeepSeek: chọn LLM đã khai báo ở «LLM» "
                    "để dùng chung API key — không cần nhập lại bên dưới."
                ),
            },
        ),
        (
            "Kết nối riêng",
            {
                "fields": ("base_url", "api_key", "model"),
                "description": (
                    "Chỉ cần khi không chọn LLM, hoặc muốn ghi đè model. "
                    "Bấm «Tải danh sách model» rồi chọn model và lưu."
                ),
            },
        ),
    )

    @admin.display(description="Công cụ", ordering="backend")
    def tool_display(self, obj):
        return mark_safe(_tool_badge(obj.backend))

    @admin.display(description="Nên dùng cho")
    def suitable_for(self, obj):
        entry = catalog_entry(obj.backend)
        page_types = entry.get("page_types") or "—"
        option = (entry.get("option") or "").strip()
        if option and option != "—":
            return format_html(
                '<div style="font-weight:600;">{}</div>'
                '<div style="font-size:12px;color:#64748b;">Option: {}</div>',
                page_types,
                option,
            )
        return format_html('<div style="font-weight:600;">{}</div>', page_types)

    @admin.display(description="Fallback")
    def fallback_display(self, obj):
        if not obj.fallback_provider_id:
            return "—"
        chain: list[str] = []
        seen: set = set()
        current = obj
        while current.fallback_provider_id and current.fallback_provider_id not in seen:
            current = current.fallback_provider
            seen.add(current.pk)
            chain.append(current.name)
        return " → ".join(chain)

    @admin.display(description="API Key", boolean=True)
    def api_key_set(self, obj):
        if obj.api_key or obj.api_key_env:
            return True
        if obj.llm_id and obj.llm:
            return bool(obj.llm.api_key or obj.llm.api_key_env)
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("fallback_provider", "llm")

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["ocr_tool_catalog"] = OCR_TOOL_CATALOG
        return super().changelist_view(request, extra_context=extra_context)

    def get_changeform_initial_data(self, request):
        backend = request.GET.get("backend", OCRBackend.GOOGLE_VISION)
        return {
            "backend": backend,
            "base_url": DEFAULT_BASE_URL.get(backend, ""),
        }

    def get_urls(self):
        return [
            path(
                "fetch-models/",
                self.admin_site.admin_view(self.fetch_models_view),
                name="llm_ocrprovider_fetch_models",
            ),
            *super().get_urls(),
        ]

    def fetch_models_view(self, request):
        backend = request.GET.get("backend", "")
        base_url = request.GET.get("base_url", "")
        api_key = request.GET.get("api_key", "")
        object_id = request.GET.get("object_id", "")
        llm_id = request.GET.get("llm_id", "")

        if llm_id:
            llm = LLMProvider.objects.filter(pk=llm_id).first()
            if llm:
                api_key = api_key or llm.resolve_api_key() or ""
                base_url = base_url or (llm.base_url or "")

        if not api_key and object_id:
            provider = OCRProvider.objects.select_related("llm").filter(pk=object_id).first()
            if provider:
                api_key, stored_url, _model = provider.resolve_llm_credentials()
                api_key = api_key or ""
                base_url = base_url or stored_url

        try:
            models = list_vision_models(backend=backend, base_url=base_url, api_key=api_key)
        except Exception as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)

        if not models:
            hint = "Loại công cụ này không cần chọn model."
            return JsonResponse({"ok": True, "models": [], "hint": hint})

        return JsonResponse({"ok": True, "models": models})

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["fetch_models_url"] = reverse("admin:llm_ocrprovider_fetch_models")
        extra_context["default_base_urls"] = DEFAULT_BASE_URL
        extra_context["ocr_tool_catalog"] = OCR_TOOL_CATALOG
        extra_context["vlm_backends"] = list(VLM_BACKENDS)
        extra_context["backends_without_models"] = [
            OCRBackend.GOOGLE_VISION,
            OCRBackend.PADDLE_OCR,
            OCRBackend.MINERU,
            *DOCUMENT_AI_BACKENDS,
        ]
        return super().changeform_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.is_active:
            messages.success(request, f"Đã lưu công cụ OCR «{obj.name}».")


@admin.register(LLMProvider)
class LLMProviderAdmin(ModelAdmin):
    form = LLMProviderForm
    change_form_template = "admin/llm/ocrprovider/change_form.html"
    list_display = ("tool_display", "name", "model", "base_url", "api_key_set", "is_active", "is_default")
    list_filter = ("backend", "is_active")
    search_fields = ("name", "model")
    fieldsets = (
        (
            None,
            {
                "fields": ("name", "backend", "is_active", "is_default"),
                "description": (
                    "Khai báo LLM một lần (API key, Base URL, model mặc định). "
                    "Các chức năng khác chỉ chọn LLM và model."
                ),
            },
        ),
        (
            "Kết nối",
            {
                "fields": ("base_url", "api_key", "model"),
                "description": (
                    "Chọn loại LLM, điền Base URL và API Key (nếu cần), "
                    "bấm «Tải danh sách model» rồi chọn model mặc định và lưu. "
                    "vLLM: Base URL dạng http://ip:8000/v1 — API Key có thể để trống."
                ),
            },
        ),
        (
            "Ollama / vLLM",
            {
                "fields": ("temperature", "enable_thinking"),
                "description": (
                    "Temperature và thinking chỉ áp dụng cho Ollama và vLLM. "
                    "Làm sạch / rút tên sách nên để temperature = 0 và tắt thinking."
                ),
            },
        ),
    )

    @admin.display(description="LLM", ordering="backend")
    def tool_display(self, obj):
        return mark_safe(_tool_badge(obj.backend))

    @admin.display(description="API Key", boolean=True)
    def api_key_set(self, obj):
        return bool(obj.api_key or obj.api_key_env)

    def get_changeform_initial_data(self, request):
        backend = request.GET.get("backend", LLMBackend.GEMINI)
        return {
            "backend": backend,
            "base_url": DEFAULT_BASE_URL.get(backend, ""),
        }

    def get_urls(self):
        return [
            path(
                "fetch-models/",
                self.admin_site.admin_view(self.fetch_models_view),
                name="llm_llmprovider_fetch_models",
            ),
            *super().get_urls(),
        ]

    def fetch_models_view(self, request):
        backend = request.GET.get("backend", "")
        base_url = request.GET.get("base_url", "")
        api_key = request.GET.get("api_key", "")
        object_id = request.GET.get("object_id", "")

        if object_id:
            provider = LLMProvider.objects.filter(pk=object_id).first()
            if provider:
                backend = backend or provider.backend
                base_url = base_url or (provider.base_url or "")
                api_key = api_key or provider.resolve_api_key() or ""

        try:
            models = list_text_models(backend=backend, base_url=base_url, api_key=api_key)
        except Exception as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)

        if not models:
            hint = "Không tìm thấy model — kiểm tra Base URL / API Key."
            return JsonResponse({"ok": True, "models": [], "hint": hint})

        return JsonResponse({"ok": True, "models": models})

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["fetch_models_url"] = reverse("admin:llm_llmprovider_fetch_models")
        extra_context["default_base_urls"] = DEFAULT_BASE_URL
        extra_context["backends_without_models"] = []
        extra_context["sampling_backends"] = [LLMBackend.OLLAMA, LLMBackend.VLLM]
        return super().changeform_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.is_active:
            messages.success(request, f"Đã lưu LLM «{obj.name}».")


class _LLMFeatureAdmin(ModelAdmin):
    change_form_template = "admin/llm/llm_feature_form.html"

    def has_add_permission(self, request):
        return not self.model.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["fetch_models_url"] = reverse("admin:llm_llmprovider_fetch_models")
        return super().changeform_view(request, object_id, form_url, extra_context)


@admin.register(TextCleaningConfig)
class TextCleaningConfigAdmin(_LLMFeatureAdmin):
    form = TextCleaningConfigForm
    list_display = ("__str__", "provider", "model", "updated_at")
    autocomplete_fields = ("provider",)
    fieldsets = (
        (
            None,
            {
                "fields": ("provider", "model", "prompt"),
                "description": (
                    "Chọn LLM đã khai báo và model (để trống = model mặc định của LLM). "
                    "Prompt cũng chỉnh được trên màn xem Markdown của sách."
                ),
            },
        ),
    )


@admin.register(ChunkingConfig)
class ChunkingConfigAdmin(_LLMFeatureAdmin):
    form = ChunkingConfigForm
    list_display = ("__str__", "provider", "model", "window_pages", "overlap_pages", "updated_at")
    autocomplete_fields = ("provider",)
    fieldsets = (
        (
            None,
            {
                "fields": ("provider", "model", "window_pages", "overlap_pages", "prompt", "stitch_prompt"),
                "description": (
                    "Trước khi phân đoạn: nối đoạn dang dở qua ranh giới trang "
                    "(mỗi cặp hỏi LLM, chỉ trả true/false). "
                    "Sau đó gửi N trang đã đánh số đoạn. LLM chỉ trả JSON "
                    '{"break_after": [2, 5]} — chương trình tự chèn </break> và <!-- Trang P -->, '
                    "không viết lại trang. Prompt chỉnh được trên màn xem Markdown của Văn bản nguồn (Dataset)."
                ),
            },
        ),
    )


@admin.register(BookIngestConfig)
class BookIngestConfigAdmin(_LLMFeatureAdmin):
    form = BookIngestConfigForm
    list_display = ("__str__", "provider", "model", "updated_at")
    autocomplete_fields = ("provider",)
    fieldsets = (
        (
            None,
            {
                "fields": ("provider", "model", "prompt"),
                "description": (
                    "Rút trích tên sách từ OCR trang đầu của file trong danh sách tạm "
                    "và đối chiếu với tên sách đã có trên hệ thống."
                ),
            },
        ),
    )


@admin.register(BookDedupeConfig)
class BookDedupeConfigAdmin(_LLMFeatureAdmin):
    form = BookDedupeConfigForm
    list_display = ("__str__", "provider", "model", "batch_size", "updated_at")
    autocomplete_fields = ("provider",)
    fieldsets = (
        (
            None,
            {
                "fields": ("provider", "model", "batch_size", "prompt"),
                "description": (
                    "Mỗi lần gửi khoảng 30 tên sách (đã sắp xếp) để LLM tìm các tên cùng một cuốn."
                ),
            },
        ),
    )


@admin.register(BookClassifyConfig)
class BookClassifyConfigAdmin(_LLMFeatureAdmin):
    form = BookClassifyConfigForm
    list_display = ("__str__", "provider", "model", "max_pages", "updated_at")
    autocomplete_fields = ("provider",)
    fieldsets = (
        (
            None,
            {
                "fields": ("provider", "model", "max_pages", "prompt"),
                "description": (
                    "Chọn LLM đã khai báo. LLM đọc khoảng 20 trang đầu "
                    "(OCR hoặc chữ sẵn trong PDF) rồi điền tác giả, nhà xuất bản, "
                    "năm xuất bản và 5 nhóm phân loại."
                ),
            },
        ),
    )
