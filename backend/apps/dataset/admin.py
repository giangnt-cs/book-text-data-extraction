from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from pathlib import Path

from celery import current_app
from django.contrib import admin, messages
from django.contrib.admin.templatetags.admin_urls import add_preserved_filters
from django.db.models import Count, Exists, OuterRef, Q, Sum, Value
from django.db.models.functions import Replace
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from unfold.admin import ModelAdmin
from unfold.decorators import action
from unfold.enums import ActionVariant

from apps.dataset.catalog import assign_catalog_code
from apps.dataset.forms import SourceDocumentAdminForm
from apps.dataset.ingest import create_source_from_upload
from apps.documents.admin import BOOK_CLASS_LIST_FILTERS, ClassificationStatusFilter
from apps.dataset.markdown import (
    document_has_chunked_markdown,
    markdown_display_source,
    purge_source_chunking,
    source_markdown_content,
)
from apps.dataset.models import (
    ChunkingBatchStatus,
    SourceChunkingBatch,
    SourceDocument,
)
from apps.documents.task_control import cancel_chunking_batch
from apps.llm.chunking_runner import (
    get_chunking_config,
    normalize_chunk_window,
    resolve_chunking_provider,
    save_chunking_settings,
)
from apps.llm.models import ChunkingMethod, LLMProvider
from ocr_llm.prompts import default_page_chunking_prompt
from apps.dataset import admin_chunks as _admin_chunks  # noqa: F401
from apps.dataset import admin_images as _admin_images  # noqa: F401

logger = logging.getLogger(__name__)


class SourceWorkflowFilter(admin.SimpleListFilter):
    title = "Trạng thái"
    parameter_name = "workflow"

    def lookups(self, request, model_admin):
        return (
            ("ready", "Chưa phân đoạn"),
            ("chunking", "Đang phân đoạn"),
            ("chunked", "Đã phân đoạn"),
            ("failed", "Lỗi phân đoạn"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        running = Exists(
            SourceChunkingBatch.objects.filter(
                document_id=OuterRef("pk"),
                status__in=[ChunkingBatchStatus.PENDING, ChunkingBatchStatus.PROCESSING],
            )
        )
        has_chunked = Exists(
            SourceChunkingBatch.objects.filter(
                document_id=OuterRef("pk"),
                status=ChunkingBatchStatus.DONE,
            )
        )
        last_failed = Exists(
            SourceChunkingBatch.objects.filter(
                document_id=OuterRef("pk"),
                status=ChunkingBatchStatus.FAILED,
            )
        )
        if value == "chunking":
            return queryset.filter(running)
        if value == "chunked":
            return queryset.exclude(running).filter(has_chunked)
        if value == "failed":
            return queryset.exclude(running).exclude(has_chunked).filter(last_failed)
        if value == "ready":
            return queryset.exclude(running).exclude(has_chunked)
        return queryset


@admin.register(SourceDocument)
class SourceDocumentAdmin(ModelAdmin):
    form = SourceDocumentAdminForm
    list_display = (
        "title",
        "catalog_code_display",
        "classification_codes_display",
        "source_type",
        "book_link",
        "segment_count",
        "status_badge",
        "markdown_link",
        "updated_at",
    )
    ordering = ("-updated_at",)
    list_filter = (SourceWorkflowFilter, "source_type", ClassificationStatusFilter, *BOOK_CLASS_LIST_FILTERS)
    list_filter_submit = True
    search_help_text = "Tên văn bản, mã DSG_fff (ví dụ MAV_001), mã nhóm phân loại."
    search_fields = (
        "catalog_code",
        "title",
        "author",
        "publisher",
        "published_year",
        "original_name",
        "book__title",
        "class_source__code",
        "class_source__name",
        "class_field__code",
        "class_field__name",
        "class_language__code",
        "class_language__name",
        "class_doc_type__code",
        "class_doc_type__name",
        "class_era__code",
        "class_era__name",
    )
    readonly_fields = (
        "source_type",
        "book",
        "original_name",
        "markdown_file",
        "catalog_code",
        "classify_run_button",
        "created_by",
        "created_at",
        "updated_at",
    )
    list_before_template = "admin/dataset/source_upload.html"
    actions = [
        "bulk_chunk_text",
        "cancel_bulk_chunk_text",
        "bulk_purge_chunking",
        "auto_classify",
        "download_markdown_zip",
    ]
    actions_submit_line = ["run_classify"]
    fieldsets = (
        (None, {"fields": ("title", "source_type", "book", "original_name", "markdown_file")}),
        (
            "Thông tin xuất bản",
            {
                "fields": ("author", "publisher", "published_year"),
                "description": (
                    "Sao chép từ sách đã duyệt, điền tay, hoặc bấm «Phân loại tự động» "
                    "để LLM đọc các đoạn Markdown đầu. Gửi kèm khi nạp RAGFlow."
                ),
            },
        ),
        (
            "Phân loại",
            {
                "fields": (
                    "catalog_code",
                    "class_source",
                    "class_field",
                    "class_language",
                    "class_doc_type",
                    "class_era",
                    "classify_run_button",
                ),
                "description": (
                    "Ba nhóm chính (Nguồn y học, Lĩnh vực, Ngôn ngữ) tạo mã DSG_fff. "
                    "Hai nhóm phụ không vào mã. «Phân loại tự động» đọc các đoạn Markdown đầu "
                    "(cấu hình LLM tại Admin → OCR → Phân loại sách)."
                ),
            },
        ),
        ("Hệ thống", {"fields": ("created_by", "created_at", "updated_at")}),
    )

    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        assign_catalog_code(obj)

    def get_search_results(self, request, queryset, search_term):
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        raw = (search_term or "").strip()
        if not raw:
            return queryset, use_distinct

        code_q = Q(catalog_code__icontains=raw) | Q(catalog_code__iexact=raw)
        compact = re.sub(r"[\s_\-]+", "", raw)
        if compact:
            code_q |= Q(
                pk__in=self.model.objects.annotate(
                    _catalog_compact=Replace("catalog_code", Value("_"), Value("")),
                ).filter(_catalog_compact__icontains=compact)
            )
        extra = self.model.objects.filter(code_q)
        return (queryset | extra).distinct(), True

    def get_queryset(self, request):
        running = SourceChunkingBatch.objects.filter(
            document_id=OuterRef("pk"),
            status__in=[ChunkingBatchStatus.PENDING, ChunkingBatchStatus.PROCESSING],
        )
        has_chunked = SourceChunkingBatch.objects.filter(
            document_id=OuterRef("pk"),
            status=ChunkingBatchStatus.DONE,
        )
        return (
            super()
            .get_queryset(request)
            .select_related(
                "book",
                "class_source",
                "class_field",
                "class_language",
                "class_doc_type",
                "class_era",
            )
            .annotate(
                _segment_count=Count("segments", distinct=True),
                _chunking_running=Exists(running),
                _has_chunked=Exists(has_chunked),
            )
        )

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["upload_url"] = reverse("admin:dataset_sourcedocument_upload")
        extra_context["source_count"] = self.get_queryset(request).count()
        extra_context["can_upload"] = self.has_change_permission(request)
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        return [
            path(
                "upload/",
                self.admin_site.admin_view(self.upload_view),
                name="dataset_sourcedocument_upload",
            ),
            path(
                "<path:object_id>/view-markdown/",
                self.admin_site.admin_view(self.view_markdown),
                name="dataset_sourcedocument_view_markdown",
            ),
            path(
                "<path:object_id>/download-markdown/",
                self.admin_site.admin_view(self.download_markdown),
                name="dataset_sourcedocument_download_markdown",
            ),
            path(
                "<path:object_id>/chunk-text/start/",
                self.admin_site.admin_view(self.chunk_text_start),
                name="dataset_sourcedocument_chunk_text_start",
            ),
            path(
                "<path:object_id>/chunk-text/revert/",
                self.admin_site.admin_view(self.chunk_text_revert),
                name="dataset_sourcedocument_chunk_text_revert",
            ),
            path(
                "<path:object_id>/chunk-text/status/",
                self.admin_site.admin_view(self.chunk_text_status),
                name="dataset_sourcedocument_chunk_text_status",
            ),
            path(
                "<path:object_id>/chunk-text/save-prompt/",
                self.admin_site.admin_view(self.chunk_text_save_prompt),
                name="dataset_sourcedocument_chunk_text_save_prompt",
            ),
            path(
                "<path:object_id>/chunk-text/cancel/",
                self.admin_site.admin_view(self.chunk_text_cancel),
                name="dataset_sourcedocument_chunk_text_cancel",
            ),
            *super().get_urls(),
        ]

    def upload_view(self, request):
        if request.method != "POST":
            return redirect("admin:dataset_sourcedocument_changelist")
        if not self.has_change_permission(request):
            messages.error(request, "Không có quyền thêm văn bản.")
            return redirect("admin:dataset_sourcedocument_changelist")
        files = request.FILES.getlist("files")
        if not files:
            messages.warning(request, "Chưa chọn file.")
            return redirect("admin:dataset_sourcedocument_changelist")
        created = 0
        for uploaded in files:
            name = Path(uploaded.name or "").name
            try:
                data = uploaded.read()
                create_source_from_upload(
                    filename=name,
                    data=data,
                    created_by=request.user if request.user.is_authenticated else None,
                )
                created += 1
            except ValueError as e:
                messages.warning(request, f"«{name}»: {e}")
            except Exception:
                logger.exception("Upload Dataset «%s» lỗi.", name)
                messages.error(request, f"«{name}»: không đọc được file.")
        if created:
            messages.success(request, f"Đã thêm {created} văn bản (đã chuyển thành Markdown).")
        return redirect("admin:dataset_sourcedocument_changelist")

    @admin.display(description="Sách")
    def book_link(self, obj):
        if not obj.book_id:
            return "—"
        url = reverse("admin:documents_book_change", args=[obj.book_id])
        return format_html('<a href="{}">{}</a>', url, obj.book.title)

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
            '<span style="font-family:ui-monospace,monospace;font-weight:600;letter-spacing:0.02em;">{}</span>',
            codes,
        )

    @admin.display(description="Số trang", ordering="_segment_count")
    def segment_count(self, obj):
        return int(getattr(obj, "_segment_count", 0) or 0) or "—"

    @admin.display(description="Trạng thái")
    def status_badge(self, obj):
        colors = {
            "ready": ("#334155", "#e2e8f0"),
            "chunking": ("#6d28d9", "#ede9fe"),
            "chunked": ("#5b21b6", "#ddd6fe"),
            "failed": ("#b91c1c", "#fee2e2"),
        }
        code, label = obj.display_status(
            chunking_running=getattr(obj, "_chunking_running", None),
            has_chunked=getattr(obj, "_has_chunked", None),
        )
        fg, bg = colors.get(code, ("#64748b", "#f1f5f9"))
        return format_html(
            '<span style="display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;'
            'background:{};color:{};font-weight:600;font-size:12px;white-space:nowrap;">{}</span>',
            bg,
            fg,
            label,
        )

    @admin.display(description="Markdown")
    def markdown_link(self, obj):
        if not obj.pk:
            return "-"
        view_url = reverse("admin:dataset_sourcedocument_view_markdown", args=[obj.pk])
        download_url = reverse("admin:dataset_sourcedocument_download_markdown", args=[obj.pk])
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">Xem</a>'
            ' · <a href="{}" target="_blank" rel="noopener">Tải</a>',
            view_url,
            download_url,
        )

    def _chunking_prompt_text(self) -> str:
        config = get_chunking_config()
        if (config.prompt or "").strip():
            return config.prompt.strip()
        return default_page_chunking_prompt()

    def _providers_payload(self):
        return [
            {
                "id": str(p.pk),
                "name": p.name,
                "backend": p.get_backend_display(),
                "is_default": p.is_default,
            }
            for p in LLMProvider.objects.filter(is_active=True).order_by("name")
        ]

    def _job_running(self, document: SourceDocument) -> str:
        if document.chunking_batches.filter(
            status__in=[ChunkingBatchStatus.PENDING, ChunkingBatchStatus.PROCESSING]
        ).exists():
            return "Đang có lần phân đoạn đang chạy."
        return ""

    def _enqueue_chunking(
        self,
        request,
        document: SourceDocument,
        *,
        provider=None,
        model: str | None = None,
        prompt: str = "",
        window_pages: int | None = None,
        overlap_pages: int | None = None,
    ) -> tuple[bool, str]:
        if not document.segments.exists() and not (document.markdown_file and document.markdown_file.name):
            return False, "Văn bản chưa có nội dung Markdown."
        running = self._job_running(document)
        if running:
            return False, running
        try:
            config = get_chunking_config()
            method = config.method or ChunkingMethod.LLM
            if method == ChunkingMethod.POINCARE:
                from apps.dataset.poincare_runner import poincare_settings_summary

                if not (config.embed_host or "").strip() or not (config.embed_model or "").strip():
                    return False, "Chưa cấu hình host/model embedding Poincaré."
                batch = SourceChunkingBatch.objects.create(
                    document=document,
                    method=ChunkingMethod.POINCARE,
                    provider=None,
                    model=config.embed_model or "",
                    prompt=poincare_settings_summary(config),
                    window_pages=config.window_pages,
                    overlap_pages=config.overlap_pages,
                    status=ChunkingBatchStatus.PENDING,
                    created_by=request.user if request.user.is_authenticated else None,
                )
                current_app.send_task(
                    "apps.dataset.tasks.chunk_source_document",
                    kwargs={"batch_id": str(batch.id)},
                    queue="cpu",
                )
                document.touch()
                return True, str(batch.id)
            if provider is None:
                provider = resolve_chunking_provider(config)
                if model is None:
                    model = config.model
            elif model is None:
                model = config.model if config.provider_id == provider.pk else ""
        except RuntimeError as e:
            return False, str(e)

        prompt = (prompt or "").strip() or self._chunking_prompt_text()
        if window_pages is None:
            window_pages = config.window_pages
        if overlap_pages is None:
            overlap_pages = config.overlap_pages
        window_pages, overlap_pages = normalize_chunk_window(window_pages, overlap_pages)
        batch = SourceChunkingBatch.objects.create(
            document=document,
            method=ChunkingMethod.LLM,
            provider=provider,
            model=model or "",
            prompt=prompt,
            window_pages=window_pages,
            overlap_pages=overlap_pages,
            status=ChunkingBatchStatus.PENDING,
            created_by=request.user if request.user.is_authenticated else None,
        )
        current_app.send_task(
            "apps.dataset.tasks.chunk_source_document",
            kwargs={"batch_id": str(batch.id)},
            queue="cpu",
        )
        document.touch()
        return True, str(batch.id)

    def _cancel_chunking(self, document: SourceDocument) -> tuple[bool, str]:
        batch = (
            document.chunking_batches.filter(
                status__in=[ChunkingBatchStatus.PENDING, ChunkingBatchStatus.PROCESSING]
            )
            .order_by("-created_at")
            .first()
        )
        if not batch:
            return False, "Không có lần phân đoạn đang chạy."
        msg = cancel_chunking_batch(str(batch.id))
        SourceChunkingBatch.objects.filter(pk=batch.pk).update(
            status=ChunkingBatchStatus.FAILED,
            error_message="Người dùng đã hủy phân đoạn.",
            updated_at=timezone.now(),
        )
        document.touch()
        return True, msg

    def view_markdown(self, request, object_id):
        document = self.get_object(request, object_id)
        if not document:
            raise Http404
        content = source_markdown_content(document)
        source = markdown_display_source(document)
        try:
            default_provider = resolve_chunking_provider()
        except RuntimeError:
            default_provider = None
        latest = document.chunking_batches.order_by("-created_at").first()
        chunk_config = get_chunking_config()
        context = {
            **self.admin_site.each_context(request),
            "title": f"Markdown — {document.title}",
            "document": document,
            "markdown_content": content,
            "download_url": reverse("admin:dataset_sourcedocument_download_markdown", args=[document.pk]),
            "changelist_url": reverse("admin:dataset_sourcedocument_changelist"),
            "has_chunked_markdown": document_has_chunked_markdown(document),
            "using_chunked": source == "chunked",
            "display_source": source,
            "chunking_prompt": self._chunking_prompt_text(),
            "chunking_method": chunk_config.method or ChunkingMethod.LLM,
            "chunking_method_label": chunk_config.get_method_display(),
            "chunk_window_pages": chunk_config.window_pages,
            "chunk_overlap_pages": chunk_config.overlap_pages,
            "cleaning_providers": self._providers_payload(),
            "default_chunk_provider_id": str(default_provider.pk) if default_provider else "",
            "chunk_text_start_url": reverse("admin:dataset_sourcedocument_chunk_text_start", args=[document.pk]),
            "chunk_text_revert_url": reverse("admin:dataset_sourcedocument_chunk_text_revert", args=[document.pk]),
            "chunk_text_status_url": reverse("admin:dataset_sourcedocument_chunk_text_status", args=[document.pk]),
            "chunk_text_save_prompt_url": reverse("admin:dataset_sourcedocument_chunk_text_save_prompt", args=[document.pk]),
            "chunk_text_cancel_url": reverse("admin:dataset_sourcedocument_chunk_text_cancel", args=[document.pk]),
            "latest_chunk_batch_status": latest.status if latest else "",
        }
        return TemplateResponse(request, "admin/dataset/source_markdown_view.html", context)

    def download_markdown(self, request, object_id):
        document = self.get_object(request, object_id)
        if not document:
            raise Http404
        filename = Path(document.original_name or document.title).stem + ".md"
        content = source_markdown_content(document).encode("utf-8")
        response = HttpResponse(content, content_type="text/markdown; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @method_decorator(require_POST)
    def chunk_text_start(self, request, object_id):
        document = get_object_or_404(SourceDocument, pk=object_id)
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        prompt = (payload.get("prompt") or "").strip()
        config = get_chunking_config()
        if (config.method or ChunkingMethod.LLM) == ChunkingMethod.POINCARE:
            running = self._job_running(document)
            if running:
                return JsonResponse({"ok": False, "error": running}, status=409)
            ok, detail = self._enqueue_chunking(request, document)
            if not ok:
                status = 409 if "đang chạy" in detail else 400
                return JsonResponse({"ok": False, "error": detail}, status=status)
            return JsonResponse({"ok": True, "batch_id": detail})
        try:
            saved = save_chunking_settings(
                prompt=prompt or None,
                window_pages=payload.get("window_pages"),
                overlap_pages=payload.get("overlap_pages"),
            )
        except ValueError as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
        prompt = saved.prompt
        provider_id = payload.get("provider_id")
        if provider_id:
            provider = get_object_or_404(LLMProvider, pk=provider_id, is_active=True)
            model = config.model if config.provider_id == provider.pk else ""
        else:
            try:
                provider = resolve_chunking_provider(config)
            except RuntimeError as e:
                return JsonResponse({"ok": False, "error": str(e)}, status=400)
            model = config.model
        running = self._job_running(document)
        if running:
            return JsonResponse({"ok": False, "error": running}, status=409)
        ok, detail = self._enqueue_chunking(
            request,
            document,
            provider=provider,
            model=model or "",
            prompt=prompt,
            window_pages=saved.window_pages,
            overlap_pages=saved.overlap_pages,
        )
        if not ok:
            status = 409 if "đang chạy" in detail else 400
            return JsonResponse({"ok": False, "error": detail}, status=status)
        return JsonResponse({"ok": True, "batch_id": detail})

    @method_decorator(require_POST)
    def chunk_text_revert(self, request, object_id):
        document = get_object_or_404(SourceDocument, pk=object_id)
        for batch in document.chunking_batches.filter(
            status__in=[ChunkingBatchStatus.PENDING, ChunkingBatchStatus.PROCESSING]
        ):
            cancel_chunking_batch(str(batch.id))
        try:
            deleted = purge_source_chunking(document)
        except RuntimeError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        source = markdown_display_source(document)
        if source == "chunked":
            return JsonResponse(
                {"ok": False, "error": "Đã gọi xóa nhưng bản phân đoạn vẫn còn. Thử lại hoặc tải lại trang."},
                status=500,
            )
        return JsonResponse({
            "ok": True,
            "deleted": deleted,
            "source": source,
            "reload": True,
        })

    def chunk_text_status(self, request, object_id):
        document = get_object_or_404(SourceDocument, pk=object_id)
        batch = document.chunking_batches.order_by("-created_at").first()
        total = document.segments.count()
        progress = batch.page_texts.count() if batch else 0
        source = markdown_display_source(document)
        payload = {
            "ok": True,
            "has_chunked": document_has_chunked_markdown(document),
            "using_chunked": source == "chunked",
            "source": source,
            "total_pages": total,
        }
        if batch:
            breaks = 0
            if batch.status == ChunkingBatchStatus.DONE:
                breaks = batch.page_texts.aggregate(total=Sum("break_count")).get("total") or 0
            payload["batch"] = {
                "id": str(batch.id),
                "status": batch.status,
                "provider": batch.provider.name if batch.provider_id else (batch.method or "llm"),
                "progress": progress,
                "total": total,
                "breaks": breaks,
                "window_pages": batch.window_pages,
                "overlap_pages": batch.overlap_pages,
                "error_message": batch.error_message,
            }
        else:
            payload["batch"] = None
        if not batch or batch.status in {ChunkingBatchStatus.DONE, ChunkingBatchStatus.FAILED}:
            payload["markdown"] = source_markdown_content(document)
        return JsonResponse(payload)

    @method_decorator(require_POST)
    def chunk_text_save_prompt(self, request, object_id):
        get_object_or_404(SourceDocument, pk=object_id)
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "JSON không hợp lệ."}, status=400)
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            return JsonResponse({"ok": False, "error": "Prompt trống."}, status=400)
        try:
            saved = save_chunking_settings(
                prompt=prompt,
                window_pages=payload.get("window_pages"),
                overlap_pages=payload.get("overlap_pages"),
            )
        except ValueError as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
        return JsonResponse({
            "ok": True,
            "prompt": saved.prompt,
            "window_pages": saved.window_pages,
            "overlap_pages": saved.overlap_pages,
        })

    @method_decorator(require_POST)
    def chunk_text_cancel(self, request, object_id):
        document = get_object_or_404(SourceDocument, pk=object_id)
        ok, detail = self._cancel_chunking(document)
        if not ok:
            return JsonResponse({"ok": False, "error": detail}, status=404)
        return JsonResponse({"ok": True, "message": detail})

    def _has_classify_text(self, document: SourceDocument) -> bool:
        if document.segments.exists():
            return True
        return bool(document.markdown_file and document.markdown_file.name)

    def _run_source_classify(self, request, document: SourceDocument, *, async_ok: bool = False) -> bool:
        if not self._has_classify_text(document):
            messages.warning(request, f"Văn bản «{document.title}» chưa có Markdown.")
            return False
        if async_ok:
            current_app.send_task(
                "apps.dataset.tasks.classify_source_document",
                kwargs={"document_id": str(document.id)},
                queue="cpu",
            )
            messages.success(request, f"Đã gửi «{document.title}» vào hàng đợi phân loại.")
            return True
        try:
            from apps.llm.source_classify_runner import classify_source_metadata

            result = classify_source_metadata(document)
        except Exception as exc:
            messages.error(request, f"«{document.title}»: không phân loại được — {exc}")
            return False
        filled = result.get("filled") or []
        code = result.get("catalog_code") or ""
        extra = f" Mã {code}." if code else ""
        if filled:
            messages.success(
                request,
                f"Đã phân loại «{document.title}» bằng {result.get('provider')}: {', '.join(filled)}.{extra}",
            )
        else:
            messages.warning(
                request,
                f"LLM ({result.get('provider')}) không điền được trường nào cho «{document.title}».",
            )
        return True

    def response_change(self, request, obj):
        if "dataset_sourcedocument_run_classify" in request.POST:
            redirect_url = add_preserved_filters(
                {"preserved_filters": self.get_preserved_filters(request), "opts": self.opts},
                request.path,
            )
            return HttpResponseRedirect(redirect_url)
        return super().response_change(request, obj)

    @admin.display(description="Phân loại tự động")
    def classify_run_button(self, obj):
        if not obj or not obj.pk:
            return "Lưu văn bản trước, rồi bấm «Phân loại tự động»."
        if not self._has_classify_text(obj):
            return format_html(
                '<p style="margin:0;color:#b45309;">Cần có Markdown trước khi phân loại.</p>'
            )
        return format_html(
            '<button type="submit" name="dataset_sourcedocument_run_classify" '
            'onclick="return confirm(\'Đọc các đoạn Markdown đầu và điền 5 nhóm phân loại? '
            'Các ô đã có sẽ được ghi đè nếu LLM trả giá trị.\');" '
            'style="display:inline-flex;align-items:center;border-radius:6px;padding:0.5rem 1.25rem;'
            'font-size:0.875rem;font-weight:600;cursor:pointer;border:1px solid #0f766e;'
            'background:#0f766e;color:#fff;">Phân loại tự động</button>'
            '<p style="margin:8px 0 0;color:#64748b;font-size:12px;">'
            "Dùng LLM đã chọn tại Admin → OCR → Phân loại sách. "
            "Mã DSG_fff được gán khi đủ 3 nhóm chính.</p>"
        )

    @action(description="Phân loại tự động", icon="auto_awesome", variant=ActionVariant.SUCCESS)
    def run_classify(self, request, obj):
        self._run_source_classify(request, obj, async_ok=False)

    @admin.action(description="Phân loại tự động (LLM, đoạn MD đầu)")
    def auto_classify(self, request, queryset):
        queued = 0
        for document in queryset:
            if self._run_source_classify(request, document, async_ok=True):
                queued += 1
        if queued:
            messages.success(request, f"Đã gửi {queued} văn bản vào hàng đợi phân loại.")

    @admin.action(description="Phân đoạn hàng loạt")
    def bulk_chunk_text(self, request, queryset):
        config = get_chunking_config()
        if (config.method or ChunkingMethod.LLM) == ChunkingMethod.LLM:
            try:
                resolve_chunking_provider()
            except RuntimeError as e:
                messages.error(request, str(e))
                return
        queued = 0
        skipped = 0
        for document in queryset:
            ok, detail = self._enqueue_chunking(request, document)
            if ok:
                queued += 1
            else:
                skipped += 1
                messages.warning(request, f"«{document.title}»: {detail}")
        if queued:
            if (config.method or "") == ChunkingMethod.POINCARE:
                messages.success(
                    request,
                    f"Đã gửi {queued} văn bản vào hàng đợi phân đoạn Poincaré "
                    f"(window {config.poincare_window}, step {config.poincare_step}).",
                )
            else:
                messages.success(
                    request,
                    f"Đã gửi {queued} văn bản vào hàng đợi phân đoạn "
                    f"(cửa sổ {config.window_pages} trang, trùng {config.overlap_pages}).",
                )
        if skipped and not queued:
            messages.warning(request, "Không gửi được văn bản nào.")

    @admin.action(description="Ngưng phân đoạn hàng loạt")
    def cancel_bulk_chunk_text(self, request, queryset):
        cancelled = 0
        skipped = 0
        for document in queryset:
            ok, detail = self._cancel_chunking(document)
            if ok:
                cancelled += 1
                messages.info(request, f"«{document.title}»: {detail}")
            else:
                skipped += 1
                messages.warning(request, f"«{document.title}»: {detail}")
        if cancelled:
            messages.success(request, f"Đã gửi hủy phân đoạn {cancelled} văn bản.")
        if skipped and not cancelled:
            messages.warning(request, "Không có văn bản nào đang phân đoạn.")

    @admin.action(description="Xóa phân đoạn hàng loạt")
    def bulk_purge_chunking(self, request, queryset):
        deleted_docs = 0
        total_batches = 0
        for document in queryset:
            for batch in document.chunking_batches.filter(
                status__in=[ChunkingBatchStatus.PENDING, ChunkingBatchStatus.PROCESSING]
            ):
                cancel_chunking_batch(str(batch.id))
            n = purge_source_chunking(document)
            if n:
                deleted_docs += 1
                total_batches += n
        if deleted_docs:
            messages.success(
                request,
                f"Đã xóa phân đoạn của {deleted_docs} văn bản ({total_batches} lần chạy).",
            )
        else:
            messages.warning(request, "Không có phân đoạn nào để xóa.")

    @admin.action(description="Tải Markdown đã chọn (ZIP)")
    def download_markdown_zip(self, request, queryset):
        documents = list(queryset.order_by("title"))
        if not documents:
            messages.warning(request, "Chưa chọn văn bản.")
            return None

        buf = io.BytesIO()
        used_names: set[str] = set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for document in documents:
                content = source_markdown_content(document)
                if not (content or "").strip():
                    continue
                stem = Path(document.original_name or document.title or "van-ban").stem
                safe = re.sub(r"[^\w\-.]+", "_", stem, flags=re.UNICODE).strip("._") or "van-ban"
                entry = f"{safe}.md"
                if entry in used_names:
                    entry = f"{safe}-{document.pk}.md"
                used_names.add(entry)
                zf.writestr(entry, content.encode("utf-8"))

        if not used_names:
            messages.warning(request, "Các văn bản đã chọn không có nội dung Markdown.")
            return None

        buf.seek(0)
        response = HttpResponse(buf.read(), content_type="application/zip")
        response["Content-Disposition"] = 'attachment; filename="van-ban-nguon-markdown.zip"'
        return response
