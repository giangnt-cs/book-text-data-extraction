"""Admin: upload hàng loạt, danh sách tạm, kiểm tra trùng tên."""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.core.files import File
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import escape, format_html
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

logger = logging.getLogger(__name__)

_CHUNK_ROOT = Path(tempfile.gettempdir()) / "ocr_staged_uploads"
_MAX_STAGED_BYTES = 2 * 1024 * 1024 * 1024
_MAX_CHUNKS = 8192
_INBOX_LIST_LIMIT = 2000


def inbox_root() -> Path | None:
    raw = (getattr(settings, "STAGED_PDF_INBOX", None) or "").strip()
    if not raw:
        return None
    root = Path(raw).expanduser()
    try:
        root = root.resolve()
    except OSError:
        return None
    if not root.is_dir():
        return None
    return root


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def list_inbox_pdfs() -> list[dict]:
    root = inbox_root()
    if root is None:
        return []
    items: list[dict] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        rel = path.relative_to(root).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        items.append({"rel": rel, "name": path.name, "size": size, "size_label": _format_size(size)})
        if len(items) >= _INBOX_LIST_LIMIT:
            break
    return items


def resolve_inbox_pdf(rel: str) -> Path | None:
    root = inbox_root()
    if root is None:
        return None
    rel = (rel or "").replace("\\", "/").strip().lstrip("/")
    if not rel or any(part in {".", ".."} for part in Path(rel).parts):
        return None
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if path.is_file() and path.suffix.lower() == ".pdf":
        return path
    return None


def _validate_uploaded_pdf(uploaded) -> tuple[bool, str]:
    """Kiểm tra PDF từ file tạm trên đĩa — không đọc cả file vào RAM."""
    temp_path = getattr(uploaded, "temporary_file_path", None)
    if callable(temp_path):
        return validate_pdf(pdf_path=temp_path())
    uploaded.seek(0)
    head = uploaded.read(2048)
    uploaded.seek(0)
    if not head.lstrip().startswith(b"%PDF"):
        return (
            False,
            "File không phải PDF hợp lệ (thiếu header %PDF). "
            "Có thể file bị đổi tên sai, upload lỗi, hoặc là định dạng khác (Word, ảnh…).",
        )
    return validate_pdf(pdf_bytes=uploaded.read())


def _session_dir(session_id: str) -> Path | None:
    try:
        key = str(uuid.UUID(str(session_id).strip()))
    except (ValueError, AttributeError, TypeError):
        return None
    return _CHUNK_ROOT / key


def receive_upload_chunk(request) -> JsonResponse:
    session_dir = _session_dir(request.POST.get("session") or "")
    if session_dir is None:
        logger.warning(
            "upload-chunk phiên không hợp lệ post=%s files=%s",
            list(request.POST.keys()),
            list(request.FILES.keys()),
        )
        return JsonResponse({"ok": False, "error": "Phiên upload không hợp lệ."})
    try:
        index = int(request.POST.get("index", "-1"))
        total = int(request.POST.get("total", "0"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Thiếu số thứ tự chunk."})
    if index < 0 or total < 1 or total > _MAX_CHUNKS or index >= total:
        return JsonResponse({"ok": False, "error": "Chunk ngoài phạm vi."})
    chunk = request.FILES.get("chunk") or next(iter(request.FILES.values()), None)
    if chunk is None:
        logger.warning(
            "upload-chunk thiếu file post=%s files=%s",
            list(request.POST.keys()),
            list(request.FILES.keys()),
        )
        return JsonResponse({"ok": False, "error": "Thiếu dữ liệu chunk."})
    size = int(getattr(chunk, "size", 0) or 0)
    if size <= 0 or size > 2 * 1024 * 1024:
        return JsonResponse({"ok": False, "error": "Chunk quá lớn."})
    session_dir.mkdir(parents=True, exist_ok=True)
    used = sum(p.stat().st_size for p in session_dir.glob("*.part") if p.is_file())
    if used + size > _MAX_STAGED_BYTES:
        shutil.rmtree(session_dir, ignore_errors=True)
        return JsonResponse({"ok": False, "error": "File vượt 2GB."})
    dest = session_dir / f"{index:05d}.part"
    with dest.open("wb") as fh:
        for block in chunk.chunks():
            fh.write(block)
    logger.info("Nhận chunk %s/%s session=%s (%s bytes)", index + 1, total, session_dir.name, size)
    return JsonResponse({"ok": True, "index": index})


def assemble_upload_session(session_id: str, total: int) -> tuple[Path | None, str]:
    session_dir = _session_dir(session_id)
    if session_dir is None or not session_dir.is_dir() or total < 1:
        return None, "Phiên upload không hợp lệ."
    parts = [session_dir / f"{i:05d}.part" for i in range(total)]
    if any(not part.is_file() for part in parts):
        shutil.rmtree(session_dir, ignore_errors=True)
        return None, "Thiếu chunk, hãy tải lại."
    assembled = session_dir / "assembled.pdf"
    with assembled.open("wb") as out:
        for part in parts:
            with part.open("rb") as fh:
                shutil.copyfileobj(fh, out)
    return assembled, ""


def assembled_pdf_path(session_id: str) -> Path | None:
    session_dir = _session_dir(session_id)
    if session_dir is None:
        return None
    assembled = session_dir / "assembled.pdf"
    return assembled if assembled.is_file() else None


def cleanup_upload_session(session_id: str) -> None:
    session_dir = _session_dir(session_id)
    if session_dir is not None:
        shutil.rmtree(session_dir, ignore_errors=True)


def _save_staged_pdf(*, name: str, source, user) -> tuple[bool, str]:
    if not name.lower().endswith(".pdf"):
        return False, "Không phải PDF."
    try:
        ok, err = _validate_uploaded_pdf(source) if not isinstance(source, (str, Path)) else validate_pdf(pdf_path=str(source))
    except Exception as exc:
        logger.exception("Kiểm tra PDF «%s» lỗi", name)
        return False, f"không kiểm tra được PDF ({exc})"
    if not ok:
        return False, err
    try:
        if hasattr(source, "seek"):
            source.seek(0)
        row = StagedBook(
            original_name=name[:255],
            status=StagedBookStatus.PENDING,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        if isinstance(source, (str, Path)):
            path = Path(source)
            with path.open("rb") as fh:
                row.pdf_file.save(name, File(fh, name=name), save=False)
        else:
            row.pdf_file.save(name, source, save=False)
        row.save()
        logger.info("Đã lưu PDF tạm «%s» → %s", name, row.pk)
        return True, ""
    except Exception as exc:
        logger.exception("Lưu PDF tạm «%s» lỗi", name)
        return False, f"không lưu được file ({exc})"


def _upload_done_response(request) -> HttpResponse:
    """Trả HTML 200 thay vì 302 — nginx HTTP/1.0 hay nuốt redirect rỗng."""
    url = reverse("admin:documents_stagedbook_changelist")
    safe = escape(url)
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta http-equiv='refresh' content='0;url={safe}'>"
        f"<script>location.replace({url!r});</script>"
        f"</head><body><p>Đã xử lý xong. <a href='{safe}'>Mở danh sách tạm</a></p></body></html>"
    )
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store"
    return response


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
        extra_context["upload_chunk_url"] = reverse("admin:documents_stagedbook_upload_chunk")
        extra_context["upload_finish_url"] = reverse("admin:documents_stagedbook_upload_finish")
        extra_context["pending_count"] = StagedBook.objects.filter(status=StagedBookStatus.PENDING).count()
        extra_context["processing_count"] = StagedBook.objects.filter(
            status=StagedBookStatus.PROCESSING
        ).count()
        root = inbox_root()
        extra_context["inbox_import_url"] = reverse("admin:documents_stagedbook_import_inbox")
        extra_context["inbox_enabled"] = root is not None
        extra_context["inbox_path"] = str(root) if root else (getattr(settings, "STAGED_PDF_INBOX", "") or "").strip()
        extra_context["inbox_pdfs"] = list_inbox_pdfs() if root else []
        extra_context["inbox_truncated"] = bool(root) and len(extra_context["inbox_pdfs"]) >= _INBOX_LIST_LIMIT
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        return [
            path(
                "upload/",
                self.admin_site.admin_view(self.upload_view),
                name="documents_stagedbook_upload",
            ),
            path(
                "upload-chunk/",
                self.admin_site.admin_view(self.upload_chunk_view),
                name="documents_stagedbook_upload_chunk",
            ),
            path(
                "upload-finish/",
                self.admin_site.admin_view(self.upload_finish_view),
                name="documents_stagedbook_upload_finish",
            ),
            path(
                "import-inbox/",
                self.admin_site.admin_view(self.import_inbox_view),
                name="documents_stagedbook_import_inbox",
            ),
            *super().get_urls(),
        ]

    @method_decorator(require_POST)
    def upload_view(self, request):
        logger.info(
            "upload_view bắt đầu content-length=%s",
            request.META.get("CONTENT_LENGTH") or "?",
        )
        files = request.FILES.getlist("pdfs")
        if not files:
            messages.warning(request, "Chưa chọn file PDF.")
            return _upload_done_response(request)

        saved = 0
        skipped = 0
        for uploaded in files:
            name = uploaded.name or "book.pdf"
            size = int(getattr(uploaded, "size", 0) or 0)
            logger.info("Nhận PDF tạm «%s» (%s bytes)", name, size)
            ok, err = _save_staged_pdf(name=name, source=uploaded, user=request.user)
            if ok:
                saved += 1
            else:
                skipped += 1
                messages.warning(request, f"«{name}»: {err}")

        if saved:
            messages.success(request, f"Đã thêm {saved} file vào danh sách tạm.")
        if skipped and not saved:
            messages.warning(request, "Không thêm được file nào.")
        return _upload_done_response(request)

    @method_decorator(require_POST)
    def upload_chunk_view(self, request):
        return receive_upload_chunk(request)

    @method_decorator(require_POST)
    def upload_finish_view(self, request):
        session = request.POST.get("session") or ""
        name = Path(request.POST.get("filename") or "book.pdf").name
        try:
            total = int(request.POST.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        assembled, err = assemble_upload_session(session, total)
        if assembled is None:
            return JsonResponse({"ok": False, "error": err or "Phiên upload không hợp lệ."})
        try:
            ok, save_err = _save_staged_pdf(name=name, source=assembled, user=request.user)
        finally:
            cleanup_upload_session(session)
        if not ok:
            return JsonResponse({"ok": False, "error": save_err or "Không lưu được file."})
        messages.success(request, f"Đã thêm «{name}» vào danh sách tạm.")
        return JsonResponse({"ok": True})

    @method_decorator(require_POST)
    def import_inbox_view(self, request):
        if inbox_root() is None:
            messages.warning(request, "Chưa cấu hình hoặc không thấy folder PDF trên server.")
            return _upload_done_response(request)
        rels = [item for item in request.POST.getlist("inbox_pdfs") if item]
        if not rels:
            messages.warning(request, "Chưa chọn PDF trong folder trên server.")
            return _upload_done_response(request)
        pending_names = set(
            StagedBook.objects.filter(
                status__in=[StagedBookStatus.PENDING, StagedBookStatus.PROCESSING]
            ).values_list("original_name", flat=True)
        )
        saved = 0
        skipped = 0
        for rel in rels:
            path = resolve_inbox_pdf(rel)
            name = Path(rel).name if path is None else path.name
            if path is None:
                skipped += 1
                messages.warning(request, f"«{rel}»: không hợp lệ hoặc không còn trên server.")
                continue
            if name in pending_names:
                skipped += 1
                messages.warning(request, f"«{name}»: đã có trong danh sách tạm.")
                continue
            ok, err = _save_staged_pdf(name=name, source=path, user=request.user)
            if ok:
                saved += 1
                pending_names.add(name)
            else:
                skipped += 1
                messages.warning(request, f"«{name}»: {err}")
        if saved:
            messages.success(request, f"Đã thêm {saved} file từ folder trên server vào danh sách tạm.")
        if skipped and not saved:
            messages.warning(request, "Không thêm được file nào từ folder trên server.")
        return _upload_done_response(request)

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
