from __future__ import annotations

import re

from django.contrib import admin, messages
from django.db.models import Count, Q, Sum, Value
from django.db.models.functions import Replace
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from apps.dataset.admin_chunks import (
    ChunkRagflowStatusFilter,
    DocumentRagflowMatchFilter,
    DocumentRagflowStatusFilter,
    TokenCountSliderFilter,
    _ragflow_match_html,
    _ragflow_status_html,
)
from apps.dataset.extract_images import (
    delete_image_chunk,
    enqueue_filter_text_only_chunks,
    relink_missing_image_files,
    resequence_image_chunks,
)
from apps.dataset.forms import ImageChunkAdminForm
from apps.dataset.models import ImageChunk, ImageChunkedDocument, ImageSourceDocument, RagflowMatchStatus
from apps.dataset.ragflow import (
    can_enqueue_image_ragflow_sync,
    cancel_image_ragflow_sync,
    enqueue_image_ragflow_chunk_update,
    enqueue_image_ragflow_sync,
    verify_image_ragflow_match,
)

_EXTRACT_STATUS_COLOR = {
    "idle": "#94a3b8",
    "pending": "#d97706",
    "processing": "#d97706",
    "done": "#0f766e",
    "failed": "#b91c1c",
}


def _extract_status_html(document: ImageSourceDocument) -> str:
    key, label = document.extract_status_label()
    color = _EXTRACT_STATUS_COLOR.get(key, "#94a3b8")
    extra = ""
    error = (document.extract_error or "").strip()
    if key == "failed" and error:
        extra = format_html(' title="{}"', error)
    return format_html(
        '<span style="color:{};font-weight:600;"{}>{}</span>',
        color,
        extra,
        label,
    )


@admin.register(ImageChunkedDocument)
class ImageChunkedDocumentAdmin(ModelAdmin):
    list_display = (
        "title_display",
        "extract_status_display",
        "ragflow_status_display",
        "ragflow_match_display",
        "catalog_code_display",
        "chunk_count",
        "token_total_display",
        "classification_codes_display",
    )
    list_display_links = ()
    list_filter = (DocumentRagflowStatusFilter, DocumentRagflowMatchFilter)
    list_filter_submit = True
    search_help_text = "Tên văn bản, mã DSG_fff."
    search_fields = ("title", "catalog_code")
    ordering = ("title",)
    actions = [
        "sync_to_ragflow",
        "cancel_ragflow_sync_selected",
        "check_ragflow_match_selected",
        "filter_text_only_chunks",
        "relink_missing_images",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return True

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("book", "class_source", "class_field", "class_language", "class_doc_type", "class_era")
            .annotate(_chunk_count=Count("chunks"), _token_total=Sum("chunks__token_count"))
        )

    def _document_chunks_url(self, obj):
        return reverse("admin:dataset_imagechunk_document_changelist", args=[obj.pk])

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return redirect("admin:dataset_imagechunk_document_changelist", object_id)

    @admin.display(description="Tên văn bản", ordering="title")
    def title_display(self, obj):
        return format_html('<a href="{}">{}</a>', self._document_chunks_url(obj), obj.title)

    @admin.display(description="Rút trích", ordering="extract_status")
    def extract_status_display(self, obj):
        return _extract_status_html(obj)

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
            ok, detail = enqueue_image_ragflow_sync(document)
            if ok:
                queued += 1
            else:
                messages.warning(request, f"«{document.title}»: {detail}")
        if queued:
            messages.success(request, f"Đã gửi {queued} văn bản hình vào hàng đợi nạp RAGFlow.")
        if not queued:
            messages.warning(request, "Không gửi được văn bản nào.")

    @admin.action(description="Ngưng nạp RAGFlow hàng loạt")
    def cancel_ragflow_sync_selected(self, request, queryset):
        cancelled = 0
        for document in queryset:
            ok, detail = cancel_image_ragflow_sync(document)
            if ok:
                cancelled += 1
            else:
                messages.warning(request, f"«{document.title}»: {detail}")
        if cancelled:
            messages.success(request, f"Đã ngưng nạp RAGFlow {cancelled} văn bản hình.")
        if not cancelled:
            messages.warning(request, "Không có văn bản nào đang nạp RAGFlow.")

    @admin.action(description="Kiểm tra đồng bộ RAGFlow hàng loạt")
    def check_ragflow_match_selected(self, request, queryset):
        matched = 0
        mismatched = 0
        for document in queryset:
            status, detail = verify_image_ragflow_match(document)
            if status == RagflowMatchStatus.MATCHED:
                matched += 1
            else:
                mismatched += 1
                if detail:
                    messages.warning(request, f"«{document.title}»: {detail}")
        if matched:
            messages.success(request, f"{matched} văn bản hình khớp với RAGFlow.")
        if mismatched:
            messages.warning(request, f"{mismatched} văn bản hình không khớp với RAGFlow.")
        if not matched and not mismatched:
            messages.warning(request, "Không kiểm tra được văn bản nào.")

    @admin.action(description="Lọc khung chữ (Google Vision)")
    def filter_text_only_chunks(self, request, queryset):
        chunk_ids = list(
            ImageChunk.objects.filter(document__in=queryset).values_list("pk", flat=True)
        )
        ok, detail = enqueue_filter_text_only_chunks([str(pk) for pk in chunk_ids])
        if ok:
            messages.success(request, detail)
        else:
            messages.warning(request, detail)

    @admin.action(description="Khôi phục đường dẫn hình bị gãy")
    def relink_missing_images(self, request, queryset):
        fixed = 0
        missing = 0
        for document in queryset:
            result = relink_missing_image_files(document)
            resequence_image_chunks(document)
            fixed += int(result.get("fixed") or 0)
            missing += int(result.get("missing") or 0)
        if fixed:
            messages.success(request, f"Đã gắn lại {fixed} đường dẫn hình.")
        if missing:
            messages.warning(
                request,
                f"{missing} phân đoạn vẫn không tìm thấy file — có thể bị ghi đè lúc lỗi, cần rút trích lại box đó.",
            )
        if not fixed and not missing:
            messages.info(request, "Không có hình nào bị gãy đường dẫn.")


@admin.register(ImageChunk)
class ImageChunkAdmin(ModelAdmin):
    form = ImageChunkAdminForm
    list_display = (
        "chunk_code_display",
        "position_label",
        "thumbnail",
        "token_count",
        "ragflow_status_display",
        "text_full",
    )
    list_display_links = ("chunk_code_display", "position_label")
    list_filter = (ChunkRagflowStatusFilter, ("token_count", TokenCountSliderFilter))
    list_filter_submit = True
    list_per_page = 20
    search_fields = ("chunk_code", "text_md")
    search_help_text = "Mã phân đoạn (…_img) hoặc nội dung."
    ordering = ("position",)
    actions = ["delete_selected", "filter_text_only_selected", "relink_missing_images"]
    change_list_template = "admin/dataset/image_chunk_changelist.html"
    change_form_template = "admin/dataset/image_chunk_change_form.html"
    readonly_fields = ("chunk_code", "token_count", "image_preview")
    show_save_and_continue = False
    show_save_and_add_another = False

    def get_fieldsets(self, request, obj=None):
        return ((None, {"fields": ("chunk_code", "token_count", "image_preview", "text_md")}),)

    def has_add_permission(self, request):
        return False

    def has_module_permission(self, request):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        if "delete_selected" in actions:
            action, name, _label = actions["delete_selected"]
            actions["delete_selected"] = (action, name, "Xóa phân đoạn hình đã chọn")
        return actions

    def get_urls(self):
        return [
            path(
                "document/<uuid:document_id>/sync-ragflow/",
                self.admin_site.admin_view(self.document_sync_ragflow_view),
                name="dataset_imagechunk_document_sync_ragflow",
            ),
            path(
                "document/<uuid:document_id>/",
                self.admin_site.admin_view(self.document_changelist_view),
                name="dataset_imagechunk_document_changelist",
            ),
            path(
                "<uuid:object_id>/image/",
                self.admin_site.admin_view(self.serve_image),
                name="dataset_imagechunk_image",
            ),
            *super().get_urls(),
        ]

    def document_changelist_view(self, request, document_id):
        document = get_object_or_404(ImageSourceDocument, pk=document_id)
        self._document_scope_id = str(document.pk)
        self._document_scope = document
        try:
            sync_ok, sync_detail = can_enqueue_image_ragflow_sync(document)
            return self.changelist_view(
                request,
                extra_context={
                    "title": f"Phân đoạn hình — {document.title}",
                    "chunk_document": document,
                    "chunk_documents_url": reverse("admin:dataset_imagechunkeddocument_changelist"),
                    "chunk_sync_url": reverse(
                        "admin:dataset_imagechunk_document_sync_ragflow",
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
        document = get_object_or_404(ImageSourceDocument, pk=document_id)
        list_url = reverse("admin:dataset_imagechunk_document_changelist", args=[document.pk])
        if request.method != "POST":
            return redirect(list_url)
        ok, detail = enqueue_image_ragflow_sync(document)
        if ok:
            messages.success(request, f"Đã gửi «{document.title}» vào hàng đợi nạp RAGFlow.")
        else:
            messages.warning(request, detail)
        return redirect(list_url)

    def changelist_view(self, request, extra_context=None):
        if not getattr(self, "_document_scope_id", None):
            return redirect("admin:dataset_imagechunkeddocument_changelist")
        extra_context = extra_context or {}
        document = getattr(self, "_document_scope", None)
        if document:
            extra_context.setdefault("title", f"Phân đoạn hình — {document.title}")
            extra_context.setdefault("chunk_document", document)
            extra_context.setdefault(
                "chunk_documents_url",
                reverse("admin:dataset_imagechunkeddocument_changelist"),
            )
            extra_context.setdefault(
                "chunk_sync_url",
                reverse("admin:dataset_imagechunk_document_sync_ragflow", args=[document.pk]),
            )
            if "chunk_sync_enabled" not in extra_context:
                sync_ok, sync_detail = can_enqueue_image_ragflow_sync(document)
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

    @admin.action(description="Lọc khung chữ đã chọn (Google Vision)")
    def filter_text_only_selected(self, request, queryset):
        ok, detail = enqueue_filter_text_only_chunks([str(pk) for pk in queryset.values_list("pk", flat=True)])
        if ok:
            messages.success(request, detail)
        else:
            messages.warning(request, detail)

    @admin.action(description="Khôi phục đường dẫn hình bị gãy")
    def relink_missing_images(self, request, queryset):
        docs = {chunk.document_id: chunk.document for chunk in queryset.select_related("document")}
        if not docs and getattr(self, "_document_scope", None) is not None:
            docs = {self._document_scope.pk: self._document_scope}
        fixed = 0
        missing = 0
        for document in docs.values():
            result = relink_missing_image_files(document)
            resequence_image_chunks(document)
            fixed += int(result.get("fixed") or 0)
            missing += int(result.get("missing") or 0)
        if fixed:
            messages.success(request, f"Đã gắn lại {fixed} đường dẫn hình.")
        if missing:
            messages.warning(
                request,
                f"{missing} phân đoạn vẫn không tìm thấy file — có thể bị ghi đè lúc lỗi, cần rút trích lại box đó.",
            )
        if not fixed and not missing:
            messages.info(request, "Không có hình nào bị gãy đường dẫn.")

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id)
        extra_context["show_save_and_continue"] = False
        extra_context["show_save_and_add_another"] = False
        if obj:
            extra_context["title"] = f"{obj.chunk_code or obj.position} — {obj.document.title}"
            extra_context["chunk_list_url"] = reverse(
                "admin:dataset_imagechunk_document_changelist",
                args=[obj.document_id],
            )
        return super().change_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        text_changed = "text_md" in getattr(form, "changed_data", [])
        super().save_model(request, obj, form, change)
        if text_changed and enqueue_image_ragflow_chunk_update(obj):
            self._ragflow_update_queued = True

    def response_change(self, request, obj):
        if getattr(self, "_ragflow_update_queued", False):
            messages.success(request, f"Đã lưu {obj.chunk_code or obj.position}. Đang cập nhật RAGFlow.")
            self._ragflow_update_queued = False
        else:
            messages.success(request, f"Đã lưu {obj.chunk_code or obj.position}.")
        return redirect("admin:dataset_imagechunk_document_changelist", obj.document_id)

    def response_delete(self, request, obj_display, obj_id):
        document_id = getattr(self, "_deleted_document_id", None)
        super_response = super().response_delete(request, obj_display, obj_id)
        if document_id:
            return redirect("admin:dataset_imagechunk_document_changelist", document_id)
        return super_response

    def delete_model(self, request, obj):
        self._deleted_document_id = obj.document_id
        delete_image_chunk(obj)

    def delete_queryset(self, request, queryset):
        chunks = list(queryset.select_related("document").order_by("-position"))
        docs = {chunk.document_id: chunk.document for chunk in chunks}
        for chunk in chunks:
            if chunk.image:
                chunk.image.delete(save=False)
            chunk.delete()
        from apps.dataset.extract_images import resequence_image_chunks

        for document in docs.values():
            resequence_image_chunks(document)

    def serve_image(self, request, object_id):
        chunk = self.get_object(request, str(object_id))
        if not chunk or not chunk.image:
            raise Http404
        try:
            fh = chunk.image.open("rb")
        except Exception as exc:
            raise Http404 from exc
        response = FileResponse(fh, content_type="image/png")
        response["Cache-Control"] = "private, max-age=3600"
        return response

    def _image_url(self, obj) -> str:
        return reverse("admin:dataset_imagechunk_image", args=[obj.pk])

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

    @admin.display(description="Hình")
    def thumbnail(self, obj):
        if not obj.image:
            return format_html('<span style="color:#94a3b8;">—</span>')
        return format_html(
            '<img src="{}" alt="{}" style="max-height:96px;max-width:160px;object-fit:contain;border-radius:6px;" />',
            self._image_url(obj),
            obj.chunk_code or "",
        )

    @admin.display(description="Hình")
    def image_preview(self, obj):
        if not obj or not obj.image:
            return "—"
        return format_html(
            '<img src="{}" alt="{}" style="max-width:28rem;max-height:28rem;object-fit:contain;border-radius:8px;" />',
            self._image_url(obj),
            obj.chunk_code or "",
        )

    @admin.display(description="Trạng thái", ordering="ragflow_chunk_id")
    def ragflow_status_display(self, obj):
        if (obj.ragflow_chunk_id or "").strip():
            return format_html('<span style="color:#0f766e;font-weight:600;">Đã nạp RAG</span>')
        return format_html('<span style="color:#94a3b8;font-weight:600;">Chưa nạp RAG</span>')

    @admin.display(description="Nội dung")
    def text_full(self, obj):
        return format_html('<pre class="dataset-chunk-text">{}</pre>', obj.text_md or "")
