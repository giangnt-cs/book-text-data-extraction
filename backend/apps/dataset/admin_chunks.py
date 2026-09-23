from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from urllib.parse import urlencode

from django.contrib import admin, messages
from django.db.models import Count, Exists, Max, Min, OuterRef, Q, Sum, Value
from django.db.models.functions import Replace
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import RangeNumericListFilter, SliderNumericFilter
from unfold.contrib.filters.forms import SliderNumericForm

from apps.dataset.forms import (
    RAGFlowConfigForm,
    RAGFlowImageConfigForm,
    RAGFlowTextConfigForm,
    SourceChunkAddForm,
    SourceChunkAdminForm,
)
from apps.dataset.markdown import (
    assemble_chunks_markdown,
    delete_matching_source_chunks,
    delete_source_chunk,
    insert_source_chunk,
)
from apps.dataset.models import (
    RAGFlowConfig,
    RAGFlowImageConfig,
    RAGFlowTextConfig,
    ChunkedDocument,
    RagflowMatchStatus,
    SourceChunk,
    SourceDocument,
)
from apps.dataset.ragflow import (
    can_enqueue_ragflow_sync,
    cancel_ragflow_sync,
    enqueue_ragflow_chunk_update,
    enqueue_ragflow_sync,
    verify_text_ragflow_match,
)
from apps.dataset.resize import enqueue_resize_chunks, enqueue_resize_document

_RAGFLOW_STATUS_COLOR = {
    "idle": "#94a3b8",
    "pending": "#d97706",
    "processing": "#d97706",
    "done": "#0f766e",
    "failed": "#b91c1c",
}
_RAGFLOW_MATCH_COLOR = {
    "idle": "#94a3b8",
    "matched": "#0f766e",
    "mismatched": "#b91c1c",
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


def _ragflow_match_html(document) -> str:
    key, label = document.ragflow_match_label()
    color = _RAGFLOW_MATCH_COLOR.get(key, "#94a3b8")
    extra = ""
    detail = (getattr(document, "ragflow_match_detail", None) or "").strip()
    if key == "mismatched" and detail:
        extra = format_html(' title="{}"', detail)
    return format_html(
        '<span style="color:{};font-weight:600;"{}>{}</span>',
        color,
        extra,
        label,
    )


def _parse_token_bound(raw):
    if raw in (None, ""):
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _chunk_token_filter_q(request) -> Q:
    lo = _parse_token_bound(request.GET.get("token_count_from"))
    hi = _parse_token_bound(request.GET.get("token_count_to"))
    q = Q()
    if lo is not None:
        q &= Q(chunks__token_count__gte=lo)
    if hi is not None:
        q &= Q(chunks__token_count__lte=hi)
    return q


class DocumentRagflowStatusFilter(admin.SimpleListFilter):
    title = "Trạng thái"
    parameter_name = "ragflow_status"

    def lookups(self, request, model_admin):
        return (
            ("idle", "Chưa nạp RAG"),
            ("pending", "Chờ nạp RAG"),
            ("processing", "Đang nạp RAG"),
            ("done", "Đã nạp RAG"),
            ("failed", "Lỗi nạp RAG"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "idle":
            return queryset.filter(Q(ragflow_status="") | Q(ragflow_status__isnull=True))
        if value in {"pending", "processing", "done", "failed"}:
            return queryset.filter(ragflow_status=value)
        return queryset


class DocumentRagflowMatchFilter(admin.SimpleListFilter):
    title = "Đối chiếu RAGFlow"
    parameter_name = "ragflow_match"

    def lookups(self, request, model_admin):
        return (
            ("idle", "Chưa đối chiếu"),
            (RagflowMatchStatus.MATCHED, "Khớp với RAGFlow"),
            (RagflowMatchStatus.MISMATCHED, "Không khớp với RAGFlow"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "idle":
            return queryset.filter(Q(ragflow_match_status="") | Q(ragflow_match_status__isnull=True))
        if value in {RagflowMatchStatus.MATCHED, RagflowMatchStatus.MISMATCHED}:
            return queryset.filter(ragflow_match_status=value)
        return queryset


class _RAGFlowSingletonAdmin(ModelAdmin):
    def has_add_permission(self, request):
        return not RAGFlowConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = self.model.objects.first()
        if obj:
            return redirect(
                f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_change",
                obj.pk,
            )
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(RAGFlowConfig)
class RAGFlowConfigAdmin(_RAGFlowSingletonAdmin):
    form = RAGFlowConfigForm

    def has_module_permission(self, request):
        return False


@admin.register(RAGFlowTextConfig)
class RAGFlowTextConfigAdmin(_RAGFlowSingletonAdmin):
    form = RAGFlowTextConfigForm
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
                ),
                "description": (
                    "Knowledge base chữ trên RAGFlow. Nạp phân đoạn Dataset - Văn bản: "
                    "tạo document rỗng, gắn metadata, rồi Add chunk text."
                ),
            },
        ),
    )


@admin.register(RAGFlowImageConfig)
class RAGFlowImageConfigAdmin(_RAGFlowSingletonAdmin):
    form = RAGFlowImageConfigForm
    list_display = ("__str__", "base_url", "image_dataset_id", "updated_at")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "base_url",
                    "api_key",
                    "image_dataset_id",
                    "meta_timeout",
                    "chunk_timeout",
                ),
                "description": (
                    "Knowledge base hình minh họa trên RAGFlow. Bắt buộc khi nạp "
                    "phân đoạn Dataset - Hình ảnh (ảnh crop + mô tả)."
                ),
            },
        ),
    )


class TokenCountSliderForm(SliderNumericForm):
    def __init__(self, name, min=None, max=None, *args, **kwargs):
        super().__init__(name, min=min, max=max, *args, **kwargs)
        self.fields[f"{name}_from"].widget.attrs["placeholder"] = "Tối thiểu"
        self.fields[f"{name}_to"].widget.attrs["placeholder"] = "Tối đa"


class DocumentChunkTokenSliderFilter(RangeNumericListFilter):
    """Lọc văn bản có phân đoạn nằm trong khoảng số token."""

    title = "Số token"
    parameter_name = "token_count"
    template = "admin/dataset/token_count_filter.html"
    form_class = TokenCountSliderForm

    def queryset(self, request, queryset):
        lo = _parse_token_bound(self.used_parameters.get(f"{self.parameter_name}_from"))
        hi = _parse_token_bound(self.used_parameters.get(f"{self.parameter_name}_to"))
        if lo is None and hi is None:
            return queryset
        chunks = SourceChunk.objects.filter(document_id=OuterRef("pk"))
        if lo is not None:
            chunks = chunks.filter(token_count__gte=lo)
        if hi is not None:
            chunks = chunks.filter(token_count__lte=hi)
        return queryset.filter(Exists(chunks))

    def choices(self, changelist):
        agg = SourceChunk.objects.aggregate(lo=Min("token_count"), hi=Max("token_count"))
        min_value = agg.get("lo")
        max_value = agg.get("hi")
        if min_value is None:
            max_value = None
        else:
            min_value = int(min_value)
            max_value = int(max_value if max_value is not None else min_value)
            if max_value <= min_value:
                max_value = min_value + 1
        value_from = self.used_parameters.get(f"{self.parameter_name}_from", min_value)
        value_to = self.used_parameters.get(f"{self.parameter_name}_to", max_value)
        return (
            {
                "decimals": 0,
                "step": 1,
                "parameter_name": self.parameter_name,
                "request": self.request,
                "min": min_value,
                "max": max_value,
                "value_from": value_from,
                "value_to": value_to,
                "form": self.form_class(
                    name=self.parameter_name,
                    min=min_value,
                    max=max_value,
                    data={
                        f"{self.parameter_name}_from": value_from,
                        f"{self.parameter_name}_to": value_to,
                    },
                ),
            },
        )


@admin.register(ChunkedDocument)
class ChunkedDocumentAdmin(ModelAdmin):
    list_display = (
        "title_display",
        "ragflow_status_display",
        "ragflow_match_display",
        "catalog_code_display",
        "chunk_count",
        "token_total_display",
        "classification_codes_display",
        "source_type",
    )
    list_display_links = ()
    list_filter = (
        DocumentRagflowStatusFilter,
        DocumentRagflowMatchFilter,
        "source_type",
        DocumentChunkTokenSliderFilter,
    )
    list_filter_submit = True
    search_help_text = "Tên văn bản, mã DSG_fff."
    search_fields = ("title", "catalog_code", "original_name")
    ordering = ("title",)
    actions = [
        "sync_to_ragflow",
        "cancel_ragflow_sync_selected",
        "check_ragflow_match_selected",
        "resize_oversized_chunks",
        "download_chunks_markdown_zip",
        "purge_selected_chunking",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        token_q = _chunk_token_filter_q(request)
        count_kw = {"filter": token_q} if token_q else {}
        return (
            super()
            .get_queryset(request)
            .select_related("class_source", "class_field", "class_language", "class_doc_type", "class_era")
            .annotate(
                _chunk_count=Count("chunks", **count_kw),
                _token_total=Sum("chunks__token_count", **count_kw),
            )
            .filter(_chunk_count__gt=0)
        )

    def _document_chunks_url(self, obj):
        url = reverse("admin:dataset_sourcechunk_document_changelist", args=[obj.pk])
        request = getattr(self, "request", None)
        if request is None:
            return url
        params = {}
        for key in ("token_count_from", "token_count_to"):
            val = request.GET.get(key)
            if val not in (None, ""):
                params[key] = val
        if params:
            return f"{url}?{urlencode(params)}"
        return url

    def change_view(self, request, object_id, form_url="", extra_context=None):
        url = reverse("admin:dataset_sourcechunk_document_changelist", args=[object_id])
        params = {}
        for key in ("token_count_from", "token_count_to"):
            val = request.GET.get(key)
            if val not in (None, ""):
                params[key] = val
        if params:
            return redirect(f"{url}?{urlencode(params)}")
        return redirect(url)

    @admin.display(description="Tên văn bản", ordering="title")
    def title_display(self, obj):
        return format_html('<a href="{}">{}</a>', self._document_chunks_url(obj), obj.title)

    @admin.display(description="Trạng thái", ordering="ragflow_status")
    def ragflow_status_display(self, obj):
        return _ragflow_status_html(obj)

    @admin.display(description="Đối chiếu", ordering="ragflow_match_status")
    def ragflow_match_display(self, obj):
        return _ragflow_match_html(obj)

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
        return format_html(
            '<a href="{}" style="font-weight:600;">{}</a>',
            self._document_chunks_url(obj),
            count,
        )

    @admin.display(description="Số token", ordering="_token_total")
    def token_total_display(self, obj):
        total = int(getattr(obj, "_token_total", 0) or 0)
        return format_html(
            '<a href="{}" style="font-weight:600;">{}</a>',
            self._document_chunks_url(obj),
            total,
        )

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

    @admin.action(description="Kiểm tra đồng bộ RAGFlow hàng loạt")
    def check_ragflow_match_selected(self, request, queryset):
        matched = 0
        mismatched = 0
        for document in queryset:
            status, detail = verify_text_ragflow_match(document)
            if status == RagflowMatchStatus.MATCHED:
                matched += 1
            else:
                mismatched += 1
                if detail:
                    messages.warning(request, f"«{document.title}»: {detail}")
        if matched:
            messages.success(request, f"{matched} văn bản khớp với RAGFlow.")
        if mismatched:
            messages.warning(request, f"{mismatched} văn bản không khớp với RAGFlow.")
        if not matched and not mismatched:
            messages.warning(request, "Không kiểm tra được văn bản nào.")

    @admin.action(description="Ngưng nạp RAGFlow hàng loạt")
    def cancel_ragflow_sync_selected(self, request, queryset):
        cancelled = 0
        for document in queryset:
            ok, detail = cancel_ragflow_sync(document)
            if ok:
                cancelled += 1
            else:
                messages.warning(request, f"«{document.title}»: {detail}")
        if cancelled:
            messages.success(request, f"Đã ngưng nạp RAGFlow {cancelled} văn bản.")
        if not cancelled:
            messages.warning(request, "Không có văn bản nào đang nạp RAGFlow.")

    @admin.action(description="Giảm kích thước phân đoạn hàng loạt")
    def resize_oversized_chunks(self, request, queryset):
        queued = 0
        for document in queryset:
            ok, detail = enqueue_resize_document(document)
            if ok:
                queued += 1
            else:
                messages.warning(request, f"«{document.title}»: {detail}")
        if queued:
            messages.success(
                request,
                f"Đã gửi {queued} văn bản vào hàng đợi giảm kích thước phân đoạn.",
            )
        if not queued:
            messages.warning(request, "Không có văn bản nào cần giảm kích thước.")

    @admin.action(description="Tải Markdown phân đoạn (ZIP)")
    def download_chunks_markdown_zip(self, request, queryset):
        documents = list(queryset.order_by("title"))
        if not documents:
            messages.warning(request, "Chưa chọn văn bản.")
            return None

        buf = io.BytesIO()
        used_names: set[str] = set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for document in documents:
                content = assemble_chunks_markdown(document)
                if not (content or "").strip():
                    continue
                stem = (document.catalog_code or "").strip() or Path(
                    document.original_name or document.title or "phan-doan"
                ).stem
                safe = re.sub(r"[^\w\-.]+", "_", stem, flags=re.UNICODE).strip("._") or "phan-doan"
                entry = f"{safe}.md"
                if entry in used_names:
                    entry = f"{safe}-{document.pk}.md"
                used_names.add(entry)
                zf.writestr(entry, content.encode("utf-8"))

        if not used_names:
            messages.warning(request, "Các văn bản đã chọn không có phân đoạn để tải.")
            return None

        buf.seek(0)
        response = HttpResponse(buf.read(), content_type="application/zip")
        response["Content-Disposition"] = 'attachment; filename="phan-doan-markdown.zip"'
        return response

    @admin.action(description="Xóa phân đoạn hàng loạt")
    def purge_selected_chunking(self, request, queryset):
        token_from = _parse_token_bound(request.GET.get("token_count_from"))
        token_to = _parse_token_bound(request.GET.get("token_count_to"))
        if token_from is None and token_to is None:
            messages.warning(
                request,
                "Hãy lọc theo số token trước, rồi xóa — chỉ các đoạn trong khoảng lọc bị xóa, không xóa cả văn bản.",
            )
            return
        deleted_docs = 0
        deleted_chunks = 0
        for document in queryset:
            n = delete_matching_source_chunks(
                document,
                token_from=token_from,
                token_to=token_to,
            )
            if n:
                deleted_docs += 1
                deleted_chunks += n
        if deleted_chunks:
            range_label = f"{token_from if token_from is not None else '…'}–{token_to if token_to is not None else '…'}"
            messages.success(
                request,
                f"Đã xóa {deleted_chunks} phân đoạn (dải {range_label} token) trong {deleted_docs} văn bản. "
                "Các đoạn còn lại đã được đánh lại mã.",
            )
        else:
            messages.warning(request, "Không có phân đoạn nào trong khoảng lọc để xóa.")


class TokenCountSliderFilter(SliderNumericFilter):
    """Thanh trượt min–max; vẫn hiện khi mọi đoạn cùng số token."""

    form_class = TokenCountSliderForm
    template = "admin/dataset/token_count_filter.html"

    def choices(self, changelist):
        result = list(super().choices(changelist))
        if not result:
            return tuple(result)
        choice = result[0]
        lo, hi = choice.get("min"), choice.get("max")
        if lo is None:
            return tuple(result)
        lo = int(lo)
        hi = lo if hi is None else int(hi)
        if hi <= lo:
            hi = lo + 1
            choice["max"] = hi
            choice["value_to"] = self.used_parameters.get(f"{self.parameter_name}_to", hi)
            choice["form"] = self.form_class(
                name=self.parameter_name,
                min=lo,
                max=hi,
                data={
                    f"{self.parameter_name}_from": self.used_parameters.get(
                        f"{self.parameter_name}_from", lo
                    ),
                    f"{self.parameter_name}_to": self.used_parameters.get(
                        f"{self.parameter_name}_to", hi
                    ),
                },
            )
        return tuple(result)


class ChunkRagflowStatusFilter(admin.SimpleListFilter):
    title = "Trạng thái"
    parameter_name = "ragflow_status"

    def lookups(self, request, model_admin):
        return (
            ("synced", "Đã nạp RAG"),
            ("pending", "Chưa nạp RAG"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "synced":
            return queryset.exclude(ragflow_chunk_id="")
        if value == "pending":
            return queryset.filter(ragflow_chunk_id="")
        return queryset


@admin.register(SourceChunk)
class SourceChunkAdmin(ModelAdmin):
    form = SourceChunkAdminForm
    list_display = (
        "chunk_code_display",
        "position_label",
        "content_type_display",
        "token_count",
        "ragflow_status_display",
        "text_full",
    )
    list_display_links = ("chunk_code_display", "position_label")
    list_filter = (ChunkRagflowStatusFilter, "content_type", ("token_count", TokenCountSliderFilter))
    list_filter_submit = True
    list_per_page = 20
    search_fields = ("chunk_code", "text_md")
    search_help_text = "Mã phân đoạn (DSG_fff_ccc_ppp) hoặc nội dung."
    ordering = ("position",)
    actions = ["delete_selected", "resize_selected_chunks"]
    change_list_template = "admin/dataset/chunk_changelist.html"
    change_form_template = "admin/dataset/chunk_change_form.html"
    show_save_and_continue = False
    show_save_and_add_another = False

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ("chunk_code", "content_type", "token_count")
        return ()

    def get_fieldsets(self, request, obj=None):
        if obj:
            return ((None, {"fields": ("chunk_code", "content_type", "token_count", "text_md")}),)
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
                "document/<uuid:document_id>/resize/",
                self.admin_site.admin_view(self.document_resize_view),
                name="dataset_sourcechunk_document_resize",
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
                    "chunk_resize_url": reverse(
                        "admin:dataset_sourcechunk_document_resize",
                        args=[document.pk],
                    ),
                    "chunk_source_url": reverse(
                        "admin:dataset_sourcedocument_change",
                        args=[document.pk],
                    ),
                    "chunk_markdown_url": reverse(
                        "admin:dataset_sourcedocument_view_markdown",
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

    def document_resize_view(self, request, document_id):
        document = get_object_or_404(SourceDocument, pk=document_id)
        list_url = reverse("admin:dataset_sourcechunk_document_changelist", args=[document.pk])
        if request.method != "POST":
            return redirect(list_url)
        ok, detail = enqueue_resize_document(document)
        if ok:
            messages.success(request, f"Đã gửi «{document.title}» vào hàng đợi giảm kích thước ({detail}).")
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
            extra_context.setdefault(
                "chunk_resize_url",
                reverse("admin:dataset_sourcechunk_document_resize", args=[document.pk]),
            )
            extra_context.setdefault(
                "chunk_source_url",
                reverse("admin:dataset_sourcedocument_change", args=[document.pk]),
            )
            extra_context.setdefault(
                "chunk_markdown_url",
                reverse("admin:dataset_sourcedocument_view_markdown", args=[document.pk]),
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

    @admin.action(description="Giảm kích thước phân đoạn đã chọn")
    def resize_selected_chunks(self, request, queryset):
        ids = [str(pk) for pk in queryset.values_list("pk", flat=True)]
        ok, detail = enqueue_resize_chunks(ids)
        if ok:
            messages.success(request, f"Đã gửi hàng đợi giảm kích thước ({detail}).")
        else:
            messages.warning(request, detail)

    @admin.display(description="Mã", ordering="chunk_code")
    def chunk_code_display(self, obj):
        code = (obj.chunk_code or "").strip()
        if not code:
            return format_html('<span style="color:#94a3b8;">—</span>')
        return format_html(
            '<span style="font-family:ui-monospace,monospace;font-weight:600;">{}</span>',
            code,
        )

    @admin.display(description="Đoạn", ordering="position")
    def position_label(self, obj):
        return obj.position

    @admin.display(description="Loại", ordering="content_type")
    def content_type_display(self, obj):
        return obj.get_content_type_display()

    @admin.display(description="Trạng thái", ordering="ragflow_chunk_id")
    def ragflow_status_display(self, obj):
        if (obj.ragflow_chunk_id or "").strip():
            return format_html('<span style="color:#0f766e;font-weight:600;">Đã nạp RAG</span>')
        return format_html('<span style="color:#94a3b8;font-weight:600;">Chưa nạp RAG</span>')

    @admin.display(description="Nội dung")
    def text_full(self, obj):
        return format_html('<pre class="dataset-chunk-text">{}</pre>', obj.text_md or "")
