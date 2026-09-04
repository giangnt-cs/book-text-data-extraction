"""Admin: upload hàng loạt, danh sách tạm, kiểm tra trùng tên."""

from __future__ import annotations

from django.contrib import admin, messages
from django.core.files.base import ContentFile
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from celery import current_app
from unfold.admin import ModelAdmin

from bb_detection.pdf_validate import validate_pdf

from .models import (
    Book,
    BookDuplicateReport,
    DuplicateReportStatus,
    StagedBook,
    StagedBookStatus,
)
from .task_control import cancel_ingest


@admin.register(StagedBook)
class StagedBookAdmin(ModelAdmin):
    list_display = (
        "original_name",
        "extracted_title",
        "status",
        "matched_or_created",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("original_name", "extracted_title", "error_message")
    readonly_fields = (
        "original_name",
        "pdf_file",
        "status",
        "extracted_title",
        "first_page_text",
        "matched_book",
        "created_book",
        "error_message",
        "created_by",
        "created_at",
        "updated_at",
    )
    actions = ["start_ingest", "retry_failed", "cancel_ingest_run", "delete_finished"]
    list_before_template = "admin/documents/staged_book_upload.html"
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff

    @admin.display(description="Kết quả")
    def matched_or_created(self, obj):
        if obj.created_book_id:
            url = reverse("admin:documents_book_change", args=[obj.created_book_id])
            return format_html('<a href="{}">Đã tạo: {}</a>', url, obj.created_book.title)
        if obj.matched_book_id:
            url = reverse("admin:documents_book_change", args=[obj.matched_book_id])
            return format_html('<a href="{}">Trùng: {}</a>', url, obj.matched_book.title)
        if obj.error_message:
            return obj.error_message[:80]
        return "—"

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["upload_url"] = reverse("admin:documents_stagedbook_upload")
        extra_context["pending_count"] = StagedBook.objects.filter(status=StagedBookStatus.PENDING).count()
        extra_context["processing_count"] = StagedBook.objects.filter(
            status=StagedBookStatus.PROCESSING
        ).count()
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        return [
            path(
                "upload/",
                self.admin_site.admin_view(self.upload_view),
                name="documents_stagedbook_upload",
            ),
            *super().get_urls(),
        ]

    @method_decorator(require_POST)
    def upload_view(self, request):
        files = request.FILES.getlist("pdfs")
        if not files:
            messages.warning(request, "Chưa chọn file PDF.")
            return redirect("admin:documents_stagedbook_changelist")

        saved = 0
        skipped = 0
        for uploaded in files:
            name = uploaded.name or "book.pdf"
            if not name.lower().endswith(".pdf"):
                skipped += 1
                messages.warning(request, f"Bỏ qua «{name}» — không phải PDF.")
                continue
            try:
                data = uploaded.read()
            except OSError:
                skipped += 1
                continue
            ok, err = validate_pdf(pdf_bytes=data)
            if not ok:
                skipped += 1
                messages.warning(request, f"«{name}»: {err}")
                continue
            row = StagedBook(
                original_name=name[:255],
                status=StagedBookStatus.PENDING,
                created_by=request.user if request.user.is_authenticated else None,
            )
            row.pdf_file.save(name, ContentFile(data), save=False)
            row.save()
            saved += 1

        if saved:
            messages.success(request, f"Đã thêm {saved} file vào danh sách tạm.")
        if skipped and not saved:
            messages.warning(request, "Không thêm được file nào.")
        return redirect("admin:documents_stagedbook_changelist")

    def _enqueue_ingest(self, request, queryset):
        ids = [str(pk) for pk in queryset.values_list("pk", flat=True)]
        if not ids:
            messages.warning(request, "Không có mục nào để xử lý.")
            return
        current_app.send_task(
            "apps.documents.tasks.ingest_staged_books",
            kwargs={"staged_ids": ids},
            queue="cpu",
        )
        messages.success(request, f"Đã gửi {len(ids)} file vào hàng đợi (OCR trang đầu + LLM).")

    @admin.action(description="Tạo sách từ danh sách tạm (OCR trang đầu + LLM)")
    def start_ingest(self, request, queryset):
        pending = queryset.filter(
            status__in=[StagedBookStatus.PENDING, StagedBookStatus.FAILED]
        )
        self._enqueue_ingest(request, pending)

    @admin.action(description="Thử lại các mục lỗi")
    def retry_failed(self, request, queryset):
        failed = queryset.filter(status=StagedBookStatus.FAILED)
        self._enqueue_ingest(request, failed)

    @admin.action(description="Hủy lần nhập đang chạy")
    def cancel_ingest_run(self, request, queryset):
        msg = cancel_ingest()
        messages.info(request, msg)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            obj.delete_pdf()
        queryset.delete()

    @admin.action(description="Xóa mục đã tạo / trùng (không còn file)")
    def delete_finished(self, request, queryset):
        done = queryset.filter(status__in=[StagedBookStatus.CREATED, StagedBookStatus.DUPLICATE])
        n = done.count()
        self.delete_queryset(request, done)
        messages.success(request, f"Đã xóa {n} mục đã xử lý xong.")


def start_duplicate_title_check(request):
    report = BookDuplicateReport.objects.create(
        status=DuplicateReportStatus.PENDING,
        created_by=request.user if request.user.is_authenticated else None,
    )
    current_app.send_task(
        "apps.documents.tasks.check_duplicate_book_titles",
        kwargs={"report_id": str(report.id)},
        queue="cpu",
    )
    return report


def duplicate_report_view(request, extra_context=None):
    report = BookDuplicateReport.objects.order_by("-created_at").first()
    if request.method == "POST" and request.POST.get("start_check"):
        report = start_duplicate_title_check(request)
        messages.success(request, "Đã gửi kiểm tra trùng tên (LLM, mỗi lần ~30 sách).")
        return redirect("admin:documents_book_duplicate_titles")

    if request.method == "POST" and request.POST.get("delete_books"):
        ids = request.POST.getlist("book_id")
        deleted = 0
        for book in Book.objects.filter(pk__in=ids):
            if book.pdf_file:
                book.pdf_file.delete(save=False)
            book.delete()
            deleted += 1
        if deleted:
            messages.success(request, f"Đã xóa {deleted} sách.")
        if report and report.groups:
            remaining = []
            idset = set(ids)
            for group in report.groups:
                books = [b for b in group.get("books") or [] if b.get("id") not in idset]
                if len(books) >= 2:
                    remaining.append({**group, "books": books})
            BookDuplicateReport.objects.filter(pk=report.pk).update(groups=remaining)
        return redirect("admin:documents_book_duplicate_titles")

    context = {
        **(extra_context or {}),
        "title": "Sách trùng tên",
        "report": report,
        "changelist_url": reverse("admin:documents_book_changelist"),
        "is_running": bool(
            report and report.status in {DuplicateReportStatus.PENDING, DuplicateReportStatus.PROCESSING}
        ),
    }
    return TemplateResponse(request, "admin/documents/book_duplicate_titles.html", context)
