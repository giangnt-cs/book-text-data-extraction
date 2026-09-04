from __future__ import annotations

import re

from django.contrib import admin, messages
from django.db.models import Count, Q, Value
from django.db.models.functions import Replace
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from apps.dataset.forms import RAGFlowConfigForm, SourceChunkAddForm, SourceChunkAdminForm
from apps.dataset.markdown import delete_source_chunk, insert_source_chunk
from apps.dataset.models import RAGFlowConfig, ChunkedDocument, SourceChunk, SourceDocument
from apps.dataset.ragflow import can_enqueue_ragflow_sync, enqueue_ragflow_chunk_update, enqueue_ragflow_sync

_RAGFLOW_STATUS_COLOR = {
    "idle": "#94a3b8",
    "pending": "#d97706",
    "processing": "#d97706",
    "done": "#0f766e",
    "failed": "#b91c1c",
}


def _ragflow_status_html(document: SourceDocument) -> str:
    key, label = document.ragflow_status_label()
    color = _RAGFLOW_STATUS_COLOR.get(key, "#94a3b8")
    extra = ""
    error = (document.ragflow_error or "").strip()
    if key == "failed" and error:
        extra = format_html(' title="{}"', error)
    return format_html(
        '<span style="color:{};font-weight:600;"{}>{}</span>',
        color,
        extra,
        label,
    )


@admin.register(RAGFlowConfig)
class RAGFlowConfigAdmin(ModelAdmin):
    form = RAGFlowConfigForm
    list_display = ("__str__", "base_url", "dataset_id", "updated_at")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "base_url",
                    "api_key",
                    "dataset_id",
                    "meta_timeout",
                    "chunk_timeout",
                    "embed_prefix",
                ),
                "description": (
                    "Knowledge base tạo sẵn trên RAGFlow. Nạp phân đoạn: tạo document rỗng "
                    "tên «văn bản.md», gắn metadata phân loại, rồi Add chunk text (tiền tố embed)."
                ),
            },
        ),
    )

    def has_add_permission(self, request):
        return not RAGFlowConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = RAGFlowConfig.objects.first()
        if obj:
            return redirect("admin:dataset_ragflowconfig_change", obj.pk)
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(ChunkedDocument)
class ChunkedDocumentAdmin(ModelAdmin):
    list_display = (
        "title_display",
        "catalog_code_display",
        "chunk_count",
        "classification_codes_display",
        "source_type",
    )
    list_display_links = ("title_display",)
    search_help_text = "Tên văn bản, mã DSG_fff."
    search_fields = ("title", "catalog_code", "original_name")
    ordering = ("title",)
    actions = ["sync_to_ragflow"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("class_source", "class_field", "class_language", "class_doc_type", "class_era")
            .annotate(_chunk_count=Count("chunks"))
            .filter(_chunk_count__gt=0)
        )

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return redirect("admin:dataset_sourcechunk_document_changelist", object_id)

    @admin.display(description="Tên văn bản", ordering="title")
    def title_display(self, obj):
        return format_html("{} · {}", obj.title, _ragflow_status_html(obj))

    @admin.display(description="Mã", ordering="catalog_code")
    def catalog_code_display(self, obj):
        code = (obj.catalog_code or "").strip()
        if not code:
            return format_html('<span style="color:#94a3b8;">—</span>')
        return format_html(
            '<span style="font-family:ui-monospace,monospace;font-weight:600;">{}</span>',
            code,
        )

    @admin.display(description="Phân loại", ordering="class_source__code")
    def classification_codes_display(self, obj):
        codes = obj.classification_codes
        if not codes or all(part.strip() == "—" for part in str(codes).split(" · ")):
            return format_html('<span style="color:#94a3b8;">Chưa phân loại</span>')
        return format_html(
            '<span style="font-family:ui-monospace,monospace;font-weight:600;">{}</span>',
            codes,
        )

    @admin.display(description="Số phân đoạn", ordering="_chunk_count")
    def chunk_count(self, obj):
        count = int(getattr(obj, "_chunk_count", 0) or 0)
        url = reverse("admin:dataset_sourcechunk_document_changelist", args=[obj.pk])
        return format_html('<a href="{}" style="font-weight:600;">{}</a>', url, count)

    @admin.action(description="Nạp RAGFlow")
    def sync_to_ragflow(self, request, queryset):
        queued = 0
        for document in queryset:
            ok, detail = enqueue_ragflow_sync(document)
            if ok:
                queued += 1
            else:
                messages.warning(request, f"«{document.title}»: {detail}")
        if queued:
            messages.success(request, f"Đã gửi {queued} văn bản vào hàng đợi nạp RAGFlow.")
        if not queued:
            messages.warning(request, "Không gửi được văn bản nào.")


@admin.register(SourceChunk)
class SourceChunkAdmin(ModelAdmin):
    form = SourceChunkAdminForm
    list_display = ("chunk_code_display", "position_label", "text_full")
    list_display_links = ("chunk_code_display", "position_label")
    list_per_page = 20
    search_fields = ("chunk_code", "text_md")
    search_help_text = "Mã phân đoạn (DSG_fff_ccc_ppp) hoặc nội dung."
    ordering = ("position",)
    actions = ["delete_selected"]
    change_list_template = "admin/dataset/chunk_changelist.html"
    change_form_template = "admin/dataset/chunk_change_form.html"
    show_save_and_continue = False
    show_save_and_add_another = False

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ("chunk_code",)
        return ()

    def get_fieldsets(self, request, obj=None):
        if obj:
            return ((None, {"fields": ("chunk_code", "text_md")}),)
        return ((None, {"fields": ("page_number", "after_chunk", "text_md")}),)

    def get_form(self, request, obj=None, change=False, **kwargs):
        if obj is None:
            kwargs["form"] = SourceChunkAddForm
        Form = super().get_form(request, obj, change=change, **kwargs)
        document = getattr(self, "_document_scope", None)
        if obj is None and document is not None:
            class FormWithDocument(Form):
                def __init__(self, *args, **inner):
                    inner.setdefault("document", document)
                    super().__init__(*args, **inner)

            return FormWithDocument
        return Form

    def has_add_permission(self, request):
        return bool(getattr(self, "_document_scope_id", None))

    def has_module_permission(self, request):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        if "delete_selected" in actions:
            action, name, _label = actions["delete_selected"]
            actions["delete_selected"] = (action, name, "Xóa phân đoạn đã chọn")
        return actions

    def get_urls(self):
        return [
            path(
                "document/<uuid:document_id>/sync-ragflow/",
                self.admin_site.admin_view(self.document_sync_ragflow_view),
                name="dataset_sourcechunk_document_sync_ragflow",
            ),
            path(
                "document/<uuid:document_id>/add/",
                self.admin_site.admin_view(self.document_add_view),
                name="dataset_sourcechunk_document_add",
            ),
            path(
                "document/<uuid:document_id>/",
                self.admin_site.admin_view(self.document_changelist_view),
                name="dataset_sourcechunk_document_changelist",
            ),
            *super().get_urls(),
        ]

    def document_changelist_view(self, request, document_id):
        document = get_object_or_404(SourceDocument, pk=document_id)
        self._document_scope_id = str(document.pk)
        self._document_scope = document
        try:
            sync_ok, sync_detail = can_enqueue_ragflow_sync(document)
            return self.changelist_view(
                request,
                extra_context={
                    "title": f"Phân đoạn — {document.title}",
                    "chunk_document": document,
                    "chunk_documents_url": reverse("admin:dataset_chunkeddocument_changelist"),
                    "chunk_add_url": reverse("admin:dataset_sourcechunk_document_add", args=[document.pk]),
                    "chunk_sync_url": reverse(
                        "admin:dataset_sourcechunk_document_sync_ragflow",
                        args=[document.pk],
                    ),
                    "chunk_sync_enabled": sync_ok,
                    "chunk_sync_disabled_reason": "" if sync_ok else sync_detail,
                    "chunk_ragflow_status": _ragflow_status_html(document),
                    "has_add_permission": False,
                },
            )
        finally:
            self._document_scope_id = None
            self._document_scope = None

    def document_sync_ragflow_view(self, request, document_id):
        document = get_object_or_404(SourceDocument, pk=document_id)
        list_url = reverse("admin:dataset_sourcechunk_document_changelist", args=[document.pk])
        if request.method != "POST":
            return redirect(list_url)
        ok, detail = enqueue_ragflow_sync(document)
        if ok:
            messages.success(request, f"Đã gửi «{document.title}» vào hàng đợi nạp RAGFlow.")
        else:
            messages.warning(request, detail)
        return redirect(list_url)

    def document_add_view(self, request, document_id):
        document = get_object_or_404(SourceDocument, pk=document_id)
        self._document_scope_id = str(document.pk)
        self._document_scope = document
        try:
            return self.add_view(
                request,
                extra_context={
                    "title": f"Thêm phân đoạn — {document.title}",
                    "chunk_document": document,
                    "chunk_list_url": reverse("admin:dataset_sourcechunk_document_changelist", args=[document.pk]),
                },
            )
        finally:
            self._document_scope_id = None
            self._document_scope = None

    def changelist_view(self, request, extra_context=None):
        if not getattr(self, "_document_scope_id", None):
            return redirect("admin:dataset_chunkeddocument_changelist")
        extra_context = extra_context or {}
        document = getattr(self, "_document_scope", None)
        if document:
            extra_context.setdefault("title", f"Phân đoạn — {document.title}")
            extra_context.setdefault("chunk_document", document)
            extra_context.setdefault(
                "chunk_documents_url",
                reverse("admin:dataset_chunkeddocument_changelist"),
            )
            extra_context.setdefault(
                "chunk_add_url",
                reverse("admin:dataset_sourcechunk_document_add", args=[document.pk]),
            )
            extra_context.setdefault(
                "chunk_sync_url",
                reverse("admin:dataset_sourcechunk_document_sync_ragflow", args=[document.pk]),
            )
            if "chunk_sync_enabled" not in extra_context:
                sync_ok, sync_detail = can_enqueue_ragflow_sync(document)
                extra_context["chunk_sync_enabled"] = sync_ok
                extra_context["chunk_sync_disabled_reason"] = "" if sync_ok else sync_detail
            extra_context.setdefault("chunk_ragflow_status", _ragflow_status_html(document))
            extra_context["has_add_permission"] = False
        return super().changelist_view(request, extra_context)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("document")
        document_id = getattr(self, "_document_scope_id", None)
        if document_id:
            qs = qs.filter(document_id=document_id)
        return qs

    def get_search_results(self, request, queryset, search_term):
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        raw = (search_term or "").strip()
        if not raw:
            return queryset, use_distinct
        scoped = self.get_queryset(request)
        code_q = Q(chunk_code__icontains=raw) | Q(chunk_code__iexact=raw)
        compact = re.sub(r"[\s_\-]+", "", raw)
        if compact:
            code_q |= Q(
                pk__in=scoped.annotate(
                    _chunk_compact=Replace("chunk_code", Value("_"), Value("")),
                ).filter(_chunk_compact__icontains=compact)
            )
        extra = scoped.filter(code_q)
        return (queryset | extra).distinct(), True

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id)
        extra_context["show_save_and_continue"] = False
        extra_context["show_save_and_add_another"] = False
        if obj:
            extra_context["title"] = f"Đoạn {obj.position} — {obj.document.title}"
            extra_context["chunk_list_url"] = reverse(
                "admin:dataset_sourcechunk_document_changelist",
                args=[obj.document_id],
            )
        return super().change_view(request, object_id, form_url, extra_context)

    def add_view(self, request, form_url="", extra_context=None):
        if not getattr(self, "_document_scope_id", None):
            return redirect("admin:dataset_chunkeddocument_changelist")
        extra_context = extra_context or {}
        extra_context["show_save_and_continue"] = False
        extra_context["show_save_and_add_another"] = False
        return super().add_view(request, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        if not change:
            document = getattr(self, "_document_scope", None)
            if document is None:
                messages.error(request, "Thiếu văn bản để thêm phân đoạn.")
                return
            created = insert_source_chunk(
                document=document,
                text=obj.text_md or "",
                page=form.cleaned_data["page_number"],
                after_chunk=form.cleaned_data.get("after_chunk"),
            )
            obj.pk = created.pk
            obj.document = created.document
            obj.position = created.position
            obj.page_number = created.page_number
            obj.page_seq = created.page_seq
            obj.chunk_code = created.chunk_code
            obj.text_md = created.text_md
            return
        text_changed = "text_md" in getattr(form, "changed_data", [])
        super().save_model(request, obj, form, change)
        if text_changed and enqueue_ragflow_chunk_update(obj):
            self._ragflow_update_queued = True

    def response_add(self, request, obj, post_url_continue=None):
        code = (obj.chunk_code or "").strip() or f"đoạn {obj.position}"
        messages.success(request, f"Đã thêm {code}.")
        return redirect("admin:dataset_sourcechunk_document_changelist", obj.document_id)

    def response_change(self, request, obj):
        if getattr(self, "_ragflow_update_queued", False):
            messages.success(request, f"Đã lưu đoạn {obj.position}. Đang cập nhật RAGFlow.")
            self._ragflow_update_queued = False
        else:
            messages.success(request, f"Đã lưu đoạn {obj.position}.")
        return redirect("admin:dataset_sourcechunk_document_changelist", obj.document_id)

    def response_delete(self, request, obj_display, obj_id):
        document_id = getattr(self, "_deleted_document_id", None)
        super_response = super().response_delete(request, obj_display, obj_id)
        if document_id:
            return redirect("admin:dataset_sourcechunk_document_changelist", document_id)
        return super_response

    def delete_model(self, request, obj):
        self._deleted_document_id = obj.document_id
        delete_source_chunk(obj)

    def delete_queryset(self, request, queryset):
        chunks = list(queryset.select_related("document").order_by("-position"))
        for chunk in chunks:
            delete_source_chunk(chunk)

    @admin.display(description="Mã", ordering="chunk_code")
    def chunk_code_display(self, obj):
        code = (obj.chunk_code or "").strip()
        synced = bool((obj.ragflow_chunk_id or "").strip())
        if synced:
            status = format_html('<span style="color:#0f766e;font-weight:600;">Đã nạp RAG</span>')
        else:
            status = format_html('<span style="color:#94a3b8;font-weight:600;">Chưa nạp RAG</span>')
        if not code:
            return format_html('<span style="color:#94a3b8;">—</span> · {}', status)
        return format_html(
            '<span style="font-family:ui-monospace,monospace;font-weight:600;">{}</span> · {}',
            code,
            status,
        )

    @admin.display(description="Đoạn", ordering="position")
    def position_label(self, obj):
        return obj.position

    @admin.display(description="Nội dung")
    def text_full(self, obj):
        return format_html('<pre class="dataset-chunk-text">{}</pre>', obj.text_md or "")
