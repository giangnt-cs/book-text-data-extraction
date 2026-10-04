from __future__ import annotations

import html
import io
import json
import zipfile

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.templatetags.admin_urls import add_preserved_filters
from django.contrib.admin.utils import quote
from celery import current_app
from django.core.files import File
from django.core.files.storage import default_storage
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from django.utils import timezone
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action
from unfold.enums import ActionVariant

from apps.llm.constants import TOOL_META
from apps.llm.models import LLMProvider
from apps.llm.ocr_runner import describe_page_ocr_providers
from apps.llm.text_cleaning_runner import (
    get_cleaning_config,
    resolve_cleaning_provider,
    save_cleaning_prompt,
)
from ocr_llm.prompts import default_text_cleaning_prompt

from bb_detection.pdf_validate import validate_pdf

from .admin_navigation import BookPageNavMixin, build_book_page_nav, build_page_header_parts, first_page_change_url
from .classification import SEEDED_GROUP_SLUGS, build_book_classification_stats
from .forms import BookAdminForm, PageReviewForm
from .models import (
    Book,
    BookCleaningBatch,
    BookStatus,
    Box,
    ClassificationGroup,
    ClassificationTerm,
    CleaningBatchStatus,
    CleaningCoverage,
    ContentType,
    OCR_READY_STATUSES,
    Page,
    PageCleanedText,
    PageOcrMode,
)
from .markdown import (
    book_has_cleaned_markdown,
    book_has_markdown,
    book_markdown_content,
    markdown_display_source,
    purge_book_cleaning,
    discard_page_cleaned_text,
    get_active_cleaning_batch,
    page_has_cleaned_text,
    page_text_source,
    save_page_review_text,
)
from .task_control import cancel_book_ocr, cancel_cleaning_batch
from apps.dataset.extract_images import cancel_book_image_extract, enqueue_book_image_extract

_BOX_COLORS = {
    ContentType.TEXT: "#22c55e",
    ContentType.TABLE: "#ef4444",
    ContentType.FORMULA: "#a855f7",
    ContentType.IMAGE: "#3b82f6",
    ContentType.COLUMNAR: "#f59e0b",
    ContentType.UNKNOWN: "#94a3b8",
}


def _ocr_tool_badge_html(page: Page) -> str:
    label = page.ocr_tool_display
    if not label:
        return mark_safe('<span style="color:#94a3b8;">Chưa OCR</span>')
    meta = TOOL_META.get(
        page.ocr_tool_backend or "",
        {"icon": "⚙️", "color": "#64748b", "bg": "#f1f5f9"},
    )
    fallback = (page.ocr_fallback_from or "").strip()
    name = page.ocr_tool_name or label
    badge = format_html(
        '<span style="display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;'
        'background:{};color:{};font-weight:600;font-size:12px;white-space:nowrap;">'
        '<span style="font-size:14px;line-height:1;">{}</span> {}</span>',
        meta["bg"],
        meta["color"],
        meta["icon"],
        name,
    )
    if fallback:
        return format_html(
            '{} <span style="font-size:12px;color:#64748b;">fallback từ {}</span>',
            badge,
            fallback,
        )
    return badge


def _page_image_admin_url(page: Page) -> str | None:
    if not page.pk:
        return None
    return reverse("admin:documents_page_image", args=[page.pk])


def _page_image_storage_paths(page: Page) -> list[str]:
    """Các đường dẫn có thể có trên storage (kể cả path cũ bị lặp upload_to)."""
    if not page.page_image:
        return []
    name = page.page_image.name
    paths = [name]
    if name.startswith("books/pages/books/pages/"):
        paths.append(name.replace("books/pages/books/pages/", "books/pages/", 1))
    elif name.startswith("books/pages/"):
        paths.append(name.replace("books/pages/", "books/pages/books/pages/", 1))
    return paths


def _open_page_image_file(page: Page):
    for path in _page_image_storage_paths(page):
        if default_storage.exists(path):
            return default_storage.open(path, "rb"), path
    return None, ""


def _render_page_png_from_pdf(page: Page, *, dpi: int | None = None) -> bytes | None:
    book = page.book
    if not book.pdf_file:
        return None
    if dpi is None:
        dpi = _detector_render_dpi()
    try:
        from bb_detection.pdf_render import render_page_png

        with book.pdf_file.open("rb") as f:
            pdf_bytes = f.read()
        png, _w, _h = render_page_png(
            pdf_bytes=pdf_bytes,
            page_number=int(page.page_number),
            dpi=dpi,
            use_cropbox=True,
            trim_scan=False,
        )
        return png
    except Exception:
        return None


def _detector_render_dpi() -> int:
    """DPI render PDF — phải khớp cấu hình doclayout-yolo khi re-render fallback."""
    from apps.layout.models import DetectorConfig

    cfg = (
        DetectorConfig.objects.filter(is_active=True, is_default=True).first()
        or DetectorConfig.objects.filter(is_active=True).order_by("id").first()
    )
    return int(cfg.dpi) if cfg else 150


_REVIEW_TYPE_ORDER = (
    ContentType.TEXT,
    ContentType.IMAGE,
    ContentType.TABLE,
    ContentType.FORMULA,
)

_REVIEW_TYPE_LABELS = {
    ContentType.TEXT: "Văn bản",
    ContentType.IMAGE: "Hình ảnh",
    ContentType.TABLE: "Bảng",
    ContentType.FORMULA: "Công thức",
}


def _summarize_page_content(page: Page) -> tuple[set[str], dict[str, int]]:
    """Loại nội dung trên trang và số vùng detect (columnar/unknown → văn bản)."""
    counts = {ct: 0 for ct in _REVIEW_TYPE_ORDER}
    present: set[str] = set()
    for box in page.boxes.all():
        ct = box.content_type
        if ct in {ContentType.TEXT, ContentType.COLUMNAR, ContentType.UNKNOWN}:
            present.add(ContentType.TEXT)
            counts[ContentType.TEXT] += 1
        elif ct in counts:
            present.add(ct)
            counts[ct] += 1
    return present, counts


def _page_content_types_html(page: Page) -> str:
    present, counts = _summarize_page_content(page)
    if not present:
        return '<p class="page-content-types__empty">Chưa phát hiện loại nội dung trên trang.</p>'

    items: list[str] = []
    for ct in _REVIEW_TYPE_ORDER:
        if ct not in present:
            continue
        color = _BOX_COLORS.get(ct, _BOX_COLORS[ContentType.UNKNOWN])
        label = _REVIEW_TYPE_LABELS[ct]
        items.append(
            f'<span class="page-content-types__item">'
            f'<span class="page-content-types__swatch" style="background:{color};"></span>'
            f"{html.escape(label)}"
            f'<span class="page-content-types__count">×{counts[ct]}</span>'
            f"</span>"
        )
    return mark_safe(f'<div class="page-content-types">{"".join(items)}</div>')


def _page_bbox_overlay_html(page: Page) -> str:
    img_w = float(page.width or 0)
    img_h = float(page.height or 0)
    if img_w <= 0 or img_h <= 0:
        return ""

    parts: list[str] = []
    for box in page.boxes.all().order_by("reading_order"):
        color = _BOX_COLORS.get(box.content_type, _BOX_COLORS[ContentType.UNKNOWN])
        ct = box.content_type
        if ct in {ContentType.TEXT, ContentType.COLUMNAR, ContentType.UNKNOWN}:
            label = _REVIEW_TYPE_LABELS[ContentType.TEXT]
        else:
            label = _REVIEW_TYPE_LABELS.get(ct, ct)
        left = box.x / img_w * 100
        top = box.y / img_h * 100
        width = box.width / img_w * 100
        height = box.height / img_h * 100
        title = html.escape(f"#{box.reading_order + 1} {label} ({box.confidence:.0%})")
        parts.append(
            f'<div class="page-bbox" data-order="{box.reading_order + 1}" '
            f'style="left:{left:.4f}%;top:{top:.4f}%;width:{width:.4f}%;height:{height:.4f}%;'
            f'border-color:{color};--bbox-color:{color};" title="{title}"></div>'
        )
    if not parts:
        return ""
    return f'<div class="page-image-viewer__boxes" id="page-bbox-layer">{"".join(parts)}</div>'


def _page_image_html(page: Page, *, image_url: str) -> str:
    if not page.page_image and not page.book.pdf_file:
        return '<p style="color:#64748b">Chưa có ảnh trang (chạy OCR để tạo).</p>'

    img_w = int(page.width or 0)
    img_h = int(page.height or 0)
    size_note = f"Kích thước gốc: {img_w}×{img_h} px" if img_w > 0 and img_h > 0 else ""
    overlay = _page_bbox_overlay_html(page)

    return format_html(
        '<div class="page-image-viewer">'
        '<div class="page-image-viewer__stage">'
        '<img src="{}" alt="Trang {}" class="page-image-viewer__img" />'
        "{}"
        "</div>"
        '<p class="page-image-viewer__note">{}</p>'
        "</div>",
        image_url,
        page.page_number,
        mark_safe(overlay),
        size_note,
    )


class BookWorkflowStatusFilter(admin.SimpleListFilter):
    title = "Trạng thái"
    parameter_name = "workflow"

    def lookups(self, request, model_admin):
        return (
            ("new", "Mới"),
            ("ocr_processing", "Đang xử lý OCR"),
            ("ocr_done", "Hoàn thành OCR"),
            ("cleaning", "Đang làm sạch"),
            ("cleaned_partial", "Làm sạch một phần"),
            ("cleaned", "Hoàn thành làm sạch"),
            ("approved", "Đã duyệt"),
            ("failed", "Lỗi"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        running = Exists(
            BookCleaningBatch.objects.filter(
                book_id=OuterRef("pk"),
                status__in=[CleaningBatchStatus.PENDING, CleaningBatchStatus.PROCESSING],
            )
        )
        if value == "new":
            return queryset.filter(status=BookStatus.NEW)
        if value == "ocr_processing":
            return queryset.filter(status=BookStatus.PROCESSING)
        if value == "failed":
            return queryset.filter(status=BookStatus.FAILED)
        if value == "cleaning":
            return queryset.filter(status__in=OCR_READY_STATUSES).filter(running)
        if value == "approved":
            return queryset.filter(status=BookStatus.APPROVED).exclude(running)
        if value == "cleaned":
            return queryset.filter(status=BookStatus.DONE).exclude(running).filter(
                cleaning_coverage=CleaningCoverage.FULL
            )
        if value == "cleaned_partial":
            return queryset.filter(status=BookStatus.DONE).exclude(running).filter(
                cleaning_coverage=CleaningCoverage.PARTIAL
            )
        if value == "ocr_done":
            return queryset.filter(status=BookStatus.DONE).exclude(running).filter(
                cleaning_coverage=CleaningCoverage.NONE
            )
        return queryset


class ClassificationStatusFilter(admin.SimpleListFilter):
    title = "Tình trạng phân loại"
    parameter_name = "class_status"

    def lookups(self, request, model_admin):
        return (
            ("incomplete", "Chưa phân loại (thiếu ít nhất 1 nhóm)"),
            ("complete", "Đã chọn đủ 5 nhóm"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        null_q = (
            Q(class_source__isnull=True)
            | Q(class_field__isnull=True)
            | Q(class_language__isnull=True)
            | Q(class_doc_type__isnull=True)
            | Q(class_era__isnull=True)
        )
        if value == "incomplete":
            return queryset.filter(null_q)
        if value == "complete":
            return queryset.exclude(null_q)
        return queryset


class ClassificationTermListFilter(admin.SimpleListFilter):
    group_slug = ""
    field_name = ""
    title = ""
    parameter_name = ""

    def lookups(self, request, model_admin):
        terms = ClassificationTerm.objects.filter(group__slug=self.group_slug).order_by("sort_order", "code")
        choices = [("empty", "Chưa phân loại")]
        choices.extend((str(term.pk), f"{term.code} — {term.name}") for term in terms)
        return choices

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        if value == "empty":
            return queryset.filter(**{f"{self.field_name}__isnull": True})
        return queryset.filter(**{f"{self.field_name}_id": value})


def _class_list_filter(field_name: str, slug: str, title: str):
    return type(
        f"{field_name}Filter",
        (ClassificationTermListFilter,),
        {
            "group_slug": slug,
            "field_name": field_name,
            "title": title,
            "parameter_name": field_name,
        },
    )


BOOK_CLASS_LIST_FILTERS = [
    _class_list_filter(field_name, slug, title)
    for field_name, slug, title in (
        ("class_source", "source", "Nguồn y học"),
        ("class_field", "field", "Lĩnh vực"),
        ("class_language", "language", "Ngôn ngữ"),
        ("class_doc_type", "doc_type", "Loại tài liệu"),
        ("class_era", "era", "Thời đại"),
    )
]


class ClassificationTermInline(TabularInline):
    model = ClassificationTerm
    extra = 1
    fields = ("code", "name", "description", "sort_order")
    ordering = ("sort_order", "code")


@admin.register(ClassificationGroup)
class ClassificationGroupAdmin(ModelAdmin):
    list_display = ("name", "kind_display", "slug", "term_count", "sort_order")
    list_filter = ("kind",)
    search_fields = ("name", "slug", "terms__code", "terms__name")
    inlines = [ClassificationTermInline]
    ordering = ("sort_order", "name")
    fieldsets = (
        (
            None,
            {
                "fields": ("name", "slug", "kind", "sort_order"),
                "description": (
                    "Mỗi nhóm có danh sách thuật ngữ và ký tự/mã. "
                    "Ký tự này dùng khi đặt mã sách. Năm nhóm mặc định: "
                    "3 nhóm chính (Nguồn y học, Lĩnh vực, Ngôn ngữ) và "
                    "2 nhóm phụ (Loại tài liệu, Thời đại)."
                ),
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_term_count=Count("terms"))

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.slug in SEEDED_GROUP_SLUGS:
            return ("slug",)
        return ()

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        if obj and obj.slug in SEEDED_GROUP_SLUGS:
            return False
        return False

    @admin.display(description="Loại", ordering="kind")
    def kind_display(self, obj):
        return obj.get_kind_display()

    @admin.display(description="Số thuật ngữ", ordering="_term_count")
    def term_count(self, obj):
        return getattr(obj, "_term_count", obj.terms.count())


@admin.register(Book)
class BookAdmin(ModelAdmin):
    form = BookAdminForm
    change_form_template = "admin/documents/book_change_form.html"
    list_display = (
        "title",
        "page_count_link",
        "published_year",
        "classification_codes_display",
        "status_badge",
        "pdf_link",
        "markdown_link",
        "ocr_config_list",
    )
    list_filter = (BookWorkflowStatusFilter, ClassificationStatusFilter, *BOOK_CLASS_LIST_FILTERS)
    list_filter_submit = True
    search_fields = (
        "title",
        "slug",
        "author",
        "publisher",
        "published_year",
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
        "status_badge",
        "cleaning_coverage",
        "error_message",
        "pdf_link",
        "markdown_link",
        "ocr_config_effective",
        "ocr_run_button",
        "classify_run_button",
        "ocr_review_button",
        "created_by",
        "created_at",
        "updated_at",
        "page_stats",
        "pages_link",
    )
    autocomplete_fields = ("ocr_standard_provider", "ocr_layout_provider")
    actions = [
        "start_processing",
        "cancel_processing",
        "bulk_clean_text",
        "cancel_bulk_clean_text",
        "auto_classify",
        "check_duplicate_titles",
        "bulk_change_status",
        "extract_images_bulk",
        "cancel_extract_images",
        "download_markdown_zip",
        "create_book_share",
    ]
    actions_submit_line = ["run_ocr", "cancel_ocr", "approve_ocr", "unapprove_ocr", "run_classify"]
    fieldsets = (
        (None, {"fields": ("title", "pdf_file", "pdf_link", "lang", "status_badge", "cleaning_coverage", "error_message")}),
        (
            "Thông tin xuất bản",
            {
                "fields": ("author", "publisher", "published_year"),
                "description": "Free text. Có thể điền tay hoặc bấm «Phân loại tự động» để LLM đọc các trang đầu.",
            },
        ),
        (
            "Phân loại",
            {
                "fields": (
                    "class_source",
                    "class_field",
                    "class_language",
                    "class_doc_type",
                    "class_era",
                    "classify_run_button",
                ),
                "description": (
                    "Mỗi sách chọn đủ 5 nhóm độc lập: 3 nhóm chính "
                    "(Nguồn y học, Lĩnh vực, Ngôn ngữ) và 2 nhóm phụ "
                    "(Loại tài liệu, Thời đại). Ký tự/mã của thuật ngữ dùng khi đặt mã sách. "
                    "«Phân loại tự động» đọc khoảng 20 trang đầu (OCR hoặc chữ trong PDF)."
                ),
            },
        ),
        (
            "Cấu hình OCR",
            {
                "fields": ("ocr_standard_provider", "ocr_layout_provider", "ocr_config_effective", "ocr_run_button"),
                "description": (
                    "Tùy chọn riêng cho sách này. Để trống một trường = dùng cấu hình hệ thống "
                    "(Admin → OCR → OCR theo loại trang). Bấm «Chạy OCR» ngay bên dưới sau khi chọn công cụ."
                ),
            },
        ),
        ("Kết quả", {"fields": ("markdown_link", "page_stats", "pages_link")}),
        (
            "Duyệt kết quả OCR",
            {
                "fields": ("ocr_review_button",),
                "description": (
                    "Sau khi xem Markdown/trang sách, đánh dấu «Đã duyệt» nếu OCR đủ tốt để dùng. "
                    "Chạy OCR lại sẽ đưa sách về «Hoàn thành OCR» để duyệt lại."
                ),
            },
        ),
        ("Thông tin", {"fields": ("created_by", "created_at", "updated_at")}),
    )

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        if request.user.is_authenticated:
            initial["created_by"] = request.user.pk
        return initial

    def save_model(self, request, obj, form, change):
        if request.user.is_authenticated and (not change or not obj.created_by_id):
            obj.created_by = request.user

        token = (request.POST.get("pdf_upload_token") or "").strip()
        token_name = (request.POST.get("pdf_upload_name") or "book.pdf").replace("\\", "/").rsplit("/", 1)[-1]
        token_name = token_name.strip() or "book.pdf"
        if token:
            from apps.documents.admin_staging import assembled_pdf_path, cleanup_upload_session

            assembled = assembled_pdf_path(token)
            if assembled is None:
                messages.error(request, "Phiên tải PDF đã hết hạn. Hãy chọn lại file.")
                return
            ok, err = validate_pdf(pdf_path=str(assembled))
            if not ok:
                cleanup_upload_session(token)
                messages.error(request, err)
                return
            try:
                with assembled.open("rb") as fh:
                    obj.pdf_file.save(token_name, File(fh, name=token_name), save=False)
            finally:
                cleanup_upload_session(token)
            super().save_model(request, obj, form, change)
            return

        pdf_file = form.cleaned_data.get("pdf_file")
        if pdf_file:
            temp_path = getattr(pdf_file, "temporary_file_path", None)
            if callable(temp_path):
                ok, err = validate_pdf(pdf_path=temp_path())
            else:
                try:
                    pdf_file.seek(0)
                    head = pdf_file.read(2048)
                    pdf_file.seek(0)
                except (AttributeError, OSError):
                    head = b""
                if not head.lstrip().startswith(b"%PDF"):
                    ok, err = False, "File không phải PDF hợp lệ (thiếu header %PDF)."
                else:
                    ok, err = validate_pdf(pdf_bytes=pdf_file.read())
                    try:
                        pdf_file.seek(0)
                    except (AttributeError, OSError):
                        pass
            if not ok:
                messages.error(request, err)
                return

        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        running = BookCleaningBatch.objects.filter(
            book_id=OuterRef("pk"),
            status__in=[CleaningBatchStatus.PENDING, CleaningBatchStatus.PROCESSING],
        )
        return super().get_queryset(request).select_related(
            "ocr_standard_provider",
            "ocr_layout_provider",
            "class_source",
            "class_field",
            "class_language",
            "class_doc_type",
            "class_era",
        ).annotate(
            _cleaning_running=Exists(running),
            _page_count=Count("pages", distinct=True),
            _first_page_id=Subquery(
                Page.objects.filter(book_id=OuterRef("pk")).order_by("page_number").values("pk")[:1]
            ),
        )

    @admin.display(description="Số trang", ordering="_page_count")
    def page_count_link(self, obj):
        count = int(getattr(obj, "_page_count", 0) or 0)
        if count <= 0:
            return "—"
        first_id = getattr(obj, "_first_page_id", None)
        if not first_id:
            return str(count)
        url = reverse("admin:documents_page_change", args=[first_id])
        return format_html('<a href="{}" style="font-weight:600;">{}</a>', url, count)

    @admin.display(description="Phân loại", ordering="class_source__code")
    def classification_codes_display(self, obj):
        codes = obj.classification_codes
        if not codes or all(part.strip() == "—" for part in str(codes).split(" · ")):
            return format_html('<span style="color:#94a3b8;">Chưa phân loại</span>')
        return format_html(
            '<span style="font-family:ui-monospace,monospace;font-weight:600;letter-spacing:0.02em;">{}</span>',
            codes,
        )

    @admin.display(description="Trạng thái")
    def status_badge(self, obj):
        colors = {
            "new": ("#64748b", "#f1f5f9"),
            "ocr_processing": ("#7c3aed", "#f3e8ff"),
            "ocr_done": ("#1d4ed8", "#dbeafe"),
            "cleaning": ("#0f766e", "#ccfbf1"),
            "cleaned_partial": ("#b45309", "#fef3c7"),
            "cleaned": ("#047857", "#d1fae5"),
            "approved": ("#15803d", "#dcfce7"),
            "failed": ("#b91c1c", "#fee2e2"),
        }
        code, label = obj.display_status(
            cleaning_running=getattr(obj, "_cleaning_running", None),
        )
        fg, bg = colors.get(code, ("#64748b", "#f1f5f9"))
        return format_html(
            '<span style="display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;'
            'background:{};color:{};font-weight:600;font-size:12px;white-space:nowrap;">{}</span>',
            bg,
            fg,
            label,
        )

    @admin.display(description="Cấu hình OCR")
    def ocr_config_list(self, obj):
        if not obj.pk:
            return "-"
        if obj.ocr_standard_provider_id or obj.ocr_layout_provider_id:
            return format_html(
                '<span style="color:#7c3aed;font-weight:600;">Cấu hình riêng</span>'
            )
        return format_html(
            '<span style="color:#64748b;">Công cụ mặc định</span>'
        )

    @admin.display(description="OCR sẽ dùng")
    def ocr_config_effective(self, obj):
        if not obj.pk:
            return "Lưu sách trước để xem cấu hình OCR áp dụng."
        desc = describe_page_ocr_providers(obj)
        lines = [
            f"Trang thường: {desc[PageOcrMode.STANDARD]}",
            f"Trang bảng/công thức: {desc[PageOcrMode.LAYOUT]}",
        ]
        custom = []
        if obj.ocr_standard_provider_id:
            custom.append("trang thường")
        if obj.ocr_layout_provider_id:
            custom.append("bảng/công thức")
        if custom:
            lines.append(f"(Riêng sách này: {', '.join(custom)})")
        else:
            lines.append("(Dùng cấu hình hệ thống)")
        return format_html("<br>".join(html.escape(line) for line in lines))

    def get_actions_submit_line(self, request, object_id):
        actions = super().get_actions_submit_line(request, object_id)
        book = Book.objects.filter(pk=object_id).first()
        if not book:
            return []
        shown = []
        for action in actions:
            name = action.action_name
            if name.endswith("_cancel_ocr"):
                if book.status == BookStatus.PROCESSING:
                    shown.append(action)
            elif name.endswith("_unapprove_ocr"):
                if book.status == BookStatus.APPROVED:
                    shown.append(action)
            elif name.endswith("_approve_ocr"):
                if book.status == BookStatus.DONE and book.display_status()[0] not in {"cleaning"}:
                    shown.append(action)
            elif name.endswith("_run_ocr"):
                if book.status != BookStatus.PROCESSING:
                    shown.append(action)
            elif name.endswith("_run_classify"):
                shown.append(action)
        return shown

    @admin.display(description="Chạy OCR")
    def ocr_run_button(self, obj):
        if not obj or not obj.pk:
            return "Lưu sách trước, rồi bấm «Chạy OCR»."
        if not obj.pdf_file:
            return format_html(
                '<p style="margin:0;color:#b45309;">Cần có file PDF trước khi chạy OCR.</p>'
            )
        if obj.status == BookStatus.PROCESSING:
            return format_html(
                '<p style="margin:0 0 8px;color:#7c3aed;font-weight:600;">Đang OCR cả sách…</p>'
                '<button type="submit" name="documents_book_cancel_ocr" '
                'style="display:inline-flex;align-items:center;border-radius:6px;padding:0.5rem 1.1rem;'
                'font-size:0.875rem;font-weight:600;cursor:pointer;border:1px solid #fca5a5;'
                'background:#fff;color:#dc2626;">Hủy OCR</button>'
            )
        confirm = (
            "Sách đã có kết quả OCR. Chạy lại sẽ ghi đè nội dung cũ và đưa về «Hoàn thành OCR» (cần duyệt lại nếu đã duyệt). Tiếp tục?"
            if obj.is_ocr_ready
            else "Lưu cấu hình OCR hiện tại và chạy OCR cả sách?"
        )
        return format_html(
            '<button type="submit" name="documents_book_run_ocr" '
            'onclick="return confirm(\'{}\');" '
            'style="display:inline-flex;align-items:center;border-radius:6px;padding:0.5rem 1.25rem;'
            'font-size:0.875rem;font-weight:600;cursor:pointer;border:1px solid #7c3aed;'
            'background:#7c3aed;color:#fff;">Chạy OCR</button>'
            '<p style="margin:8px 0 0;color:#64748b;font-size:12px;">'
            "Lưu cấu hình phía trên rồi detect + OCR toàn bộ sách.</p>",
            html.escape(confirm, quote=True),
        )

    @admin.display(description="Phân loại tự động")
    def classify_run_button(self, obj):
        if not obj or not obj.pk:
            return "Lưu sách trước, rồi bấm «Phân loại tự động»."
        if not obj.pdf_file and not obj.pages.exists():
            return format_html(
                '<p style="margin:0;color:#b45309;">Cần PDF hoặc trang đã OCR trước khi phân loại.</p>'
            )
        return format_html(
            '<button type="submit" name="documents_book_run_classify" '
            'onclick="return confirm(\'Đọc khoảng 20 trang đầu và điền tác giả, NXB, năm, phân loại? '
            'Các ô đang trống hoặc đã có sẽ được ghi đè nếu LLM trả giá trị.\');" '
            'style="display:inline-flex;align-items:center;border-radius:6px;padding:0.5rem 1.25rem;'
            'font-size:0.875rem;font-weight:600;cursor:pointer;border:1px solid #0f766e;'
            'background:#0f766e;color:#fff;">Phân loại tự động</button>'
            '<p style="margin:8px 0 0;color:#64748b;font-size:12px;">'
            "Dùng LLM đã chọn tại Admin → OCR → Phân loại sách (LLM). "
            "Ưu tiên chữ OCR/làm sạch; nếu chưa OCR thì lấy lớp text trong PDF.</p>"
        )

    @admin.display(description="Duyệt sách")
    def ocr_review_button(self, obj):
        if not obj or not obj.pk:
            return "Lưu sách và chạy OCR trước, rồi đánh dấu duyệt."
        if obj.status == BookStatus.PROCESSING:
            return format_html(
                '<p style="margin:0;color:#7c3aed;font-weight:600;">Đang OCR — duyệt sau khi hoàn thành.</p>'
            )
        if obj.status == BookStatus.APPROVED:
            return format_html(
                '<p style="margin:0 0 8px;color:#15803d;font-weight:700;">Đã duyệt — đủ tốt để sử dụng.</p>'
                '<button type="submit" name="documents_book_unapprove_ocr" '
                'style="display:inline-flex;align-items:center;border-radius:6px;padding:0.5rem 1.1rem;'
                'font-size:0.875rem;font-weight:600;cursor:pointer;border:1px solid #86efac;'
                'background:#fff;color:#166534;">Bỏ duyệt</button>'
                '<p style="margin:8px 0 0;color:#64748b;font-size:12px;">'
                "Đưa sách về «Hoàn thành OCR» hoặc «Hoàn thành làm sạch» nếu cần chỉnh sửa thêm.</p>"
            )
        if obj.status == BookStatus.DONE:
            code, current_label = obj.display_status()
            if code == "cleaning":
                return format_html(
                    '<p style="margin:0;color:#0f766e;font-weight:600;">Đang làm sạch — duyệt sau khi hoàn thành.</p>'
                )
            return format_html(
                '<p style="margin:0 0 8px;color:#334155;">Đang ở «{}». Xem Markdown/trang sách rồi đánh dấu nếu chất lượng đạt.</p>'
                '<button type="submit" name="documents_book_approve_ocr" '
                'style="display:inline-flex;align-items:center;border-radius:6px;padding:0.5rem 1.25rem;'
                'font-size:0.875rem;font-weight:600;cursor:pointer;border:1px solid #16a34a;'
                'background:#16a34a;color:#fff;">Đánh dấu Đã duyệt</button>',
                current_label,
            )
        return format_html(
            '<p style="margin:0;color:#b45309;">Chưa có kết quả OCR hoàn chỉnh — chạy OCR trước khi duyệt.</p>'
        )

    def _enqueue_book_ocr(self, request, book: Book, *, notify: bool = True) -> bool:
        if not book.pdf_file:
            messages.warning(request, f"Sách «{book.title}» chưa có file PDF.")
            return False
        if book.status == BookStatus.PROCESSING:
            messages.warning(request, f"Sách «{book.title}» đang được OCR — vui lòng đợi.")
            return False
        book.status = BookStatus.PROCESSING
        book.error_message = ""
        book.save(update_fields=["status", "error_message", "updated_at"])
        current_app.send_task(
            "apps.documents.tasks.process_book",
            kwargs={"book_id": str(book.id)},
            queue="gpu",
        )
        if notify:
            messages.success(request, f"Đã gửi «{book.title}» vào hàng đợi OCR.")
        return True

    def response_change(self, request, obj):
        if (
            "documents_book_run_ocr" in request.POST
            or "documents_book_cancel_ocr" in request.POST
            or "documents_book_approve_ocr" in request.POST
            or "documents_book_unapprove_ocr" in request.POST
            or "documents_book_run_classify" in request.POST
        ):
            redirect_url = add_preserved_filters(
                {"preserved_filters": self.get_preserved_filters(request), "opts": self.opts},
                request.path,
            )
            return HttpResponseRedirect(redirect_url)
        return super().response_change(request, obj)

    @action(description="Chạy OCR", icon="play_arrow", variant=ActionVariant.PRIMARY)
    def run_ocr(self, request, obj):
        self._enqueue_book_ocr(request, obj)

    @action(description="Hủy OCR", icon="stop", variant=ActionVariant.DANGER)
    def cancel_ocr(self, request, obj):
        if obj.status != BookStatus.PROCESSING:
            messages.warning(request, f"Sách «{obj.title}» không đang OCR.")
            return
        msg = cancel_book_ocr(str(obj.id))
        Book.objects.filter(pk=obj.pk).update(
            status=BookStatus.NEW,
            error_message="Người dùng đã hủy OCR.",
            updated_at=timezone.now(),
        )
        messages.info(request, f"«{obj.title}»: {msg}")

    @action(description="Đánh dấu Đã duyệt", icon="check_circle", variant=ActionVariant.SUCCESS)
    def approve_ocr(self, request, obj):
        if obj.status != BookStatus.DONE or obj.display_status()[0] in {"cleaning"}:
            messages.warning(request, f"Chỉ duyệt sách đang «Hoàn thành OCR» hoặc «Hoàn thành làm sạch». «{obj.title}» hiện là {obj.display_status()[1]}.")
            return
        if not book_has_markdown(obj) and not obj.pages.exists():
            messages.warning(request, f"Sách «{obj.title}» chưa có nội dung OCR để duyệt.")
            return
        Book.objects.filter(pk=obj.pk).update(status=BookStatus.APPROVED, updated_at=timezone.now())
        obj.status = BookStatus.APPROVED
        from apps.dataset.ingest import sync_source_from_book

        try:
            synced = sync_source_from_book(obj, created_by=request.user if request.user.is_authenticated else None)
        except Exception:
            synced = None
        if synced:
            messages.success(request, f"Đã duyệt «{obj.title}» và thêm vào Dataset.")
        else:
            messages.success(request, f"Đã duyệt «{obj.title}».")

    @action(description="Bỏ duyệt", icon="undo", variant=ActionVariant.DEFAULT)
    def unapprove_ocr(self, request, obj):
        if obj.status != BookStatus.APPROVED:
            messages.warning(request, f"Sách «{obj.title}» không ở trạng thái Đã duyệt.")
            return
        Book.objects.filter(pk=obj.pk).update(status=BookStatus.DONE, updated_at=timezone.now())
        obj.status = BookStatus.DONE
        messages.info(request, f"Đã bỏ duyệt «{obj.title}» — về «{obj.display_status()[1]}».")

    def _run_book_classify(self, request, book: Book, *, async_ok: bool = False) -> bool:
        if not book.pdf_file and not book.pages.exists():
            messages.warning(request, f"Sách «{book.title}» chưa có PDF hoặc trang OCR.")
            return False
        if async_ok:
            current_app.send_task(
                "apps.documents.tasks.classify_book",
                kwargs={"book_id": str(book.id)},
                queue="cpu",
            )
            messages.success(request, f"Đã gửi «{book.title}» vào hàng đợi phân loại.")
            return True
        try:
            from apps.llm.book_classify_runner import classify_book_metadata

            result = classify_book_metadata(book)
        except Exception as exc:
            messages.error(request, f"«{book.title}»: không phân loại được — {exc}")
            return False
        filled = result.get("filled") or []
        if filled:
            messages.success(
                request,
                f"Đã phân loại «{book.title}» bằng {result.get('provider')}: {', '.join(filled)}.",
            )
        else:
            messages.warning(
                request,
                f"LLM ({result.get('provider')}) không điền được trường nào cho «{book.title}».",
            )
        return True

    @action(description="Phân loại tự động", icon="auto_awesome", variant=ActionVariant.SUCCESS)
    def run_classify(self, request, obj):
        self._run_book_classify(request, obj, async_ok=False)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["pdf_upload_chunk_url"] = reverse("admin:documents_book_upload_chunk")
        extra_context["pdf_upload_finish_url"] = reverse("admin:documents_book_upload_finish")
        return super().changeform_view(request, object_id, form_url, extra_context)

    @method_decorator(require_POST)
    def upload_chunk_view(self, request):
        from apps.documents.admin_staging import receive_upload_chunk

        return receive_upload_chunk(request)

    @method_decorator(require_POST)
    def upload_finish_view(self, request):
        from apps.documents.admin_staging import assemble_upload_session

        session = request.POST.get("session") or ""
        try:
            total = int(request.POST.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        assembled, err = assemble_upload_session(session, total)
        if assembled is None:
            return JsonResponse({"ok": False, "error": err or "Phiên upload không hợp lệ."})
        return JsonResponse({"ok": True, "token": session})

    def get_urls(self):
        return [
            path(
                "upload-chunk/",
                self.admin_site.admin_view(self.upload_chunk_view),
                name="documents_book_upload_chunk",
            ),
            path(
                "upload-finish/",
                self.admin_site.admin_view(self.upload_finish_view),
                name="documents_book_upload_finish",
            ),
            path(
                "duplicate-titles/",
                self.admin_site.admin_view(self.duplicate_titles_view),
                name="documents_book_duplicate_titles",
            ),
            path(
                "classification-stats/",
                self.admin_site.admin_view(self.classification_stats_view),
                name="documents_book_classification_stats",
            ),
            path(
                "<path:object_id>/view-markdown/",
                self.admin_site.admin_view(self.view_markdown),
                name="documents_book_view_markdown",
            ),
            path(
                "<path:object_id>/download-markdown/",
                self.admin_site.admin_view(self.download_markdown),
                name="documents_book_download_markdown",
            ),
            path(
                "<path:object_id>/clean-text/start/",
                self.admin_site.admin_view(self.clean_text_start),
                name="documents_book_clean_text_start",
            ),
            path(
                "<path:object_id>/clean-text/revert/",
                self.admin_site.admin_view(self.clean_text_revert),
                name="documents_book_clean_text_revert",
            ),
            path(
                "<path:object_id>/clean-text/status/",
                self.admin_site.admin_view(self.clean_text_status),
                name="documents_book_clean_text_status",
            ),
            path(
                "<path:object_id>/clean-text/save-prompt/",
                self.admin_site.admin_view(self.clean_text_save_prompt),
                name="documents_book_clean_text_save_prompt",
            ),
            path(
                "<path:object_id>/clean-text/cancel/",
                self.admin_site.admin_view(self.clean_text_cancel),
                name="documents_book_clean_text_cancel",
            ),
            path(
                "<path:object_id>/view-pdf/",
                self.admin_site.admin_view(self.view_pdf),
                name="documents_book_view_pdf",
            ),
            *super().get_urls(),
        ]

    def classification_stats_view(self, request):
        stats = build_book_classification_stats()
        changelist = reverse("admin:documents_book_changelist")
        total = stats["total"]
        for group in stats["groups"]:
            for bar in group["bars"]:
                bar["pct"] = round(100.0 * bar["count"] / total, 1) if total else 0.0
                bar["url"] = f"{changelist}?{group['field']}={bar['id']}"
        chunk_changelist = reverse("admin:dataset_chunkeddocument_changelist")
        chunk_total = int(stats.get("chunk_count") or 0)
        for bar in stats.get("token_bins") or []:
            bar["pct"] = round(100.0 * bar["count"] / chunk_total, 1) if chunk_total else 0.0
            bar["url"] = (
                f"{chunk_changelist}?token_count_from={bar['start']}&token_count_to={bar['end']}"
            )
        token_bins = stats.get("token_bins") or []
        context = {
            **self.admin_site.each_context(request),
            "title": "Thống kê",
            "stats": stats,
            "changelist_url": changelist,
            "chunk_changelist_url": chunk_changelist,
            "token_chart_payload": {
                "slug": "token-bins",
                "name": "Phân đoạn theo số token",
                "horizontal": len(token_bins) > 12,
                "labels": [bar["label"] for bar in token_bins],
                "counts": [bar["count"] for bar in token_bins],
                "urls": [bar["url"] for bar in token_bins],
            },
            "chart_payload": [
                {
                    "slug": group["slug"],
                    "name": group["name"],
                    "horizontal": group["horizontal"],
                    "labels": [f"{bar['code']} — {bar['name']}" for bar in group["bars"]],
                    "counts": [bar["count"] for bar in group["bars"]],
                    "unclassified": [bar["unclassified"] for bar in group["bars"]],
                    "urls": [bar["url"] for bar in group["bars"]],
                }
                for group in stats["groups"]
            ],
            "custom_header_parts": [
                {"link": changelist, "title": "Tài liệu"},
                {
                    "link": reverse("admin:documents_book_classification_stats"),
                    "title": "Thống kê",
                },
            ],
        }
        return TemplateResponse(
            request,
            "admin/documents/book_classification_stats.html",
            context,
        )

    def _enqueue_book_cleaning(
        self,
        request,
        book,
        *,
        provider=None,
        model: str | None = None,
        prompt: str = "",
    ) -> tuple[bool, str]:
        """Gửi làm sạch một sách. Trả về (ok, thông báo lỗi hoặc batch_id)."""
        if not book.is_ocr_ready or not book_has_markdown(book):
            return False, "Sách chưa có Markdown OCR."
        running = self._book_llm_job_running(book)
        if running:
            return False, running
        try:
            config = get_cleaning_config()
            if provider is None:
                provider = resolve_cleaning_provider(config)
                if model is None:
                    model = config.model
            elif model is None:
                model = config.model if config.provider_id == provider.pk else ""
        except RuntimeError as e:
            return False, str(e)

        prompt = (prompt or "").strip() or self._cleaning_prompt_text()
        batch = BookCleaningBatch.objects.create(
            book=book,
            provider=provider,
            model=model or "",
            prompt=prompt,
            status=CleaningBatchStatus.PENDING,
            created_by=request.user if request.user.is_authenticated else None,
        )
        current_app.send_task(
            "apps.documents.tasks.clean_book_text",
            kwargs={"batch_id": str(batch.id)},
            queue="cpu",
        )
        return True, str(batch.id)

    def _cancel_book_cleaning(self, book) -> tuple[bool, str]:
        batch = book.cleaning_batches.filter(
            status__in=[CleaningBatchStatus.PENDING, CleaningBatchStatus.PROCESSING]
        ).order_by("-created_at").first()
        if not batch:
            return False, "Không có lần làm sạch đang chạy."
        msg = cancel_cleaning_batch(str(batch.id))
        BookCleaningBatch.objects.filter(pk=batch.pk).update(
            status=CleaningBatchStatus.FAILED,
            error_message="Người dùng đã hủy làm sạch văn bản.",
            updated_at=timezone.now(),
        )
        return True, msg

    def _book_llm_job_running(self, book) -> str:
        if book.cleaning_batches.filter(
            status__in=[CleaningBatchStatus.PENDING, CleaningBatchStatus.PROCESSING]
        ).exists():
            return "Đang có lần làm sạch đang chạy."
        return ""

    def _cleaning_prompt_text(self) -> str:
        config = get_cleaning_config()
        if (config.prompt or "").strip():
            return config.prompt.strip()
        return default_text_cleaning_prompt()

    def _cleaning_providers_payload(self):
        return [
            {
                "id": str(p.pk),
                "name": p.name,
                "backend": p.get_backend_display(),
                "is_default": p.is_default,
            }
            for p in LLMProvider.objects.filter(is_active=True).order_by("name")
        ]

    def view_markdown(self, request, object_id):
        book = self.get_object(request, object_id)
        if not book or not book.is_ocr_ready or not book_has_markdown(book):
            raise Http404

        content = book_markdown_content(book)
        download_url = reverse("admin:documents_book_download_markdown", args=[book.pk])
        changelist_url = reverse("admin:documents_book_changelist")

        try:
            default_provider = resolve_cleaning_provider()
        except RuntimeError:
            default_provider = None

        latest_batch = book.cleaning_batches.order_by("-created_at").first()
        source = markdown_display_source(book)

        context = {
            **self.admin_site.each_context(request),
            "title": f"Markdown — {book.title}",
            "book": book,
            "markdown_content": content,
            "download_url": download_url,
            "changelist_url": changelist_url,
            "has_cleaned_markdown": book_has_cleaned_markdown(book),
            "using_cleaned": source == "cleaned",
            "display_source": source,
            "cleaning_prompt": self._cleaning_prompt_text(),
            "cleaning_providers": self._cleaning_providers_payload(),
            "default_provider_id": str(default_provider.pk) if default_provider else "",
            "clean_text_start_url": reverse("admin:documents_book_clean_text_start", args=[book.pk]),
            "clean_text_revert_url": reverse("admin:documents_book_clean_text_revert", args=[book.pk]),
            "clean_text_status_url": reverse("admin:documents_book_clean_text_status", args=[book.pk]),
            "clean_text_save_prompt_url": reverse("admin:documents_book_clean_text_save_prompt", args=[book.pk]),
            "clean_text_cancel_url": reverse("admin:documents_book_clean_text_cancel", args=[book.pk]),
            "latest_batch_status": latest_batch.status if latest_batch else "",
        }
        return TemplateResponse(
            request,
            "admin/documents/book_markdown_view.html",
            context,
        )

    @method_decorator(require_POST)
    def clean_text_start(self, request, object_id):
        book = get_object_or_404(Book, pk=object_id)
        if not book.is_ocr_ready or not book_has_markdown(book):
            return JsonResponse({"ok": False, "error": "Sách chưa có Markdown OCR."}, status=400)

        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}

        prompt = (payload.get("prompt") or "").strip()
        if prompt:
            save_cleaning_prompt(prompt)
        else:
            prompt = self._cleaning_prompt_text()

        config = get_cleaning_config()
        provider_id = payload.get("provider_id")
        if provider_id:
            provider = get_object_or_404(LLMProvider, pk=provider_id, is_active=True)
            model = config.model if config.provider_id == provider.pk else ""
        else:
            try:
                provider = resolve_cleaning_provider(config)
            except RuntimeError as e:
                return JsonResponse({"ok": False, "error": str(e)}, status=400)
            model = config.model

        ok, detail = self._enqueue_book_cleaning(
            request,
            book,
            provider=provider,
            model=model or "",
            prompt=prompt,
        )
        if not ok:
            status = 409 if "đang chạy" in detail else 400
            return JsonResponse({"ok": False, "error": detail}, status=status)
        return JsonResponse({"ok": True, "batch_id": detail})

    @method_decorator(require_POST)
    def clean_text_revert(self, request, object_id):
        book = get_object_or_404(Book, pk=object_id)
        for batch in book.cleaning_batches.filter(
            status__in=[CleaningBatchStatus.PENDING, CleaningBatchStatus.PROCESSING]
        ):
            cancel_cleaning_batch(str(batch.id))
        deleted = purge_book_cleaning(book)
        book.refresh_cleaning_coverage()
        return JsonResponse({
            "ok": True,
            "deleted": deleted,
            "source": markdown_display_source(book),
            "reload": True,
        })

    def clean_text_status(self, request, object_id):
        book = get_object_or_404(Book, pk=object_id)
        batch = book.cleaning_batches.order_by("-created_at").first()
        total = book.pages.count()
        progress = batch.page_texts.count() if batch else 0

        source = markdown_display_source(book)
        payload = {
            "ok": True,
            "has_cleaned": book_has_cleaned_markdown(book),
            "using_cleaned": source == "cleaned",
            "source": source,
            "total_pages": total,
        }
        if batch:
            payload["batch"] = {
                "id": str(batch.id),
                "status": batch.status,
                "provider": batch.provider.name,
                "progress": progress,
                "total": total,
                "error_message": batch.error_message,
            }
        else:
            payload["batch"] = None
        if not batch or batch.status in {CleaningBatchStatus.DONE, CleaningBatchStatus.FAILED}:
            payload["markdown"] = book_markdown_content(book) if book_has_markdown(book) else ""
        return JsonResponse(payload)

    @method_decorator(require_POST)
    def clean_text_save_prompt(self, request, object_id):
        get_object_or_404(Book, pk=object_id)
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "JSON không hợp lệ."}, status=400)

        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            return JsonResponse({"ok": False, "error": "Prompt trống."}, status=400)

        try:
            saved = save_cleaning_prompt(prompt)
        except ValueError as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
        return JsonResponse({"ok": True, "prompt": saved})

    @method_decorator(require_POST)
    def clean_text_cancel(self, request, object_id):
        book = get_object_or_404(Book, pk=object_id)
        batch = book.cleaning_batches.filter(
            status__in=[CleaningBatchStatus.PENDING, CleaningBatchStatus.PROCESSING]
        ).order_by("-created_at").first()
        ok, detail = self._cancel_book_cleaning(book)
        if not ok:
            return JsonResponse({"ok": False, "error": detail}, status=404)
        return JsonResponse({"ok": True, "message": detail})

    def view_pdf(self, request, object_id):
        book = self.get_object(request, object_id)
        if not book or not book.pdf_file:
            raise Http404
        if not default_storage.exists(book.pdf_file.name):
            raise Http404
        filename = book.pdf_file.name.rsplit("/", 1)[-1]
        return FileResponse(
            book.pdf_file.open("rb"),
            content_type="application/pdf",
            as_attachment=False,
            filename=filename,
        )

    def download_markdown(self, request, object_id):
        book = self.get_object(request, object_id)
        if not book or not book.is_ocr_ready or not book_has_markdown(book):
            raise Http404

        filename = f"{book.slug}.md"
        disposition = f'attachment; filename="{filename}"'
        content = book_markdown_content(book).encode("utf-8")
        response = HttpResponse(content, content_type="text/markdown; charset=utf-8")
        response["Content-Disposition"] = disposition
        return response

    @admin.display(description="PDF")
    def pdf_link(self, obj):
        if not obj.pk or not obj.pdf_file:
            return "-"
        url = reverse("admin:documents_book_view_pdf", args=[obj.pk])
        return format_html('<a href="{}" target="_blank">Mở PDF</a>', url)

    @admin.display(description="Markdown")
    def markdown_link(self, obj):
        if not obj.is_ocr_ready or not book_has_markdown(obj):
            return "-"
        view_url = reverse("admin:documents_book_view_markdown", args=[obj.pk])
        download_url = reverse("admin:documents_book_download_markdown", args=[obj.pk])
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">Xem</a>'
            ' · <a href="{}" target="_blank" rel="noopener">Tải</a>',
            view_url,
            download_url,
        )

    @admin.display(description="Thống kê")
    def page_stats(self, obj):
        if not obj.pk:
            return "-"
        pages = obj.pages.count()
        boxes = Box.objects.filter(page__book=obj).count()
        layout = obj.pages.filter(ocr_mode=PageOcrMode.LAYOUT).count()
        return f"{pages} trang · {boxes} bbox · {layout} trang có bảng/công thức (VLM)"

    @admin.display(description="Trang sách")
    def pages_link(self, obj):
        if not obj.pk:
            return "-"
        count = obj.pages.count()
        url = first_page_change_url(obj.pk)
        if not url:
            return "Chưa có trang — chạy OCR để tạo."
        return format_html(
            '<a href="{}" style="font-weight:600;">Xem trang 1 / {} →</a>',
            url,
            count,
        )

    @admin.action(description="Chia sẻ sách đã chọn")
    def create_book_share(self, request, queryset):
        from apps.sharing.admin import render_create_share

        return render_create_share(self, request, queryset)

    @admin.action(description="Chạy OCR (detect → đánh dấu → Document AI → Markdown)")
    def start_processing(self, request, queryset):
        started = 0
        for book in queryset:
            if self._enqueue_book_ocr(request, book, notify=False):
                started += 1
        if started:
            messages.success(request, f"Đã gửi {started} sách vào hàng đợi xử lý.")

    @admin.action(description="Làm sạch văn bản hàng loạt")
    def bulk_clean_text(self, request, queryset):
        try:
            resolve_cleaning_provider()
        except RuntimeError as e:
            messages.error(request, str(e))
            return

        queued = 0
        skipped = 0
        for book in queryset:
            ok, detail = self._enqueue_book_cleaning(request, book)
            if ok:
                queued += 1
            else:
                skipped += 1
                messages.warning(request, f"«{book.title}»: {detail}")
        if queued:
            messages.success(request, f"Đã gửi {queued} sách vào hàng đợi làm sạch (cấu hình LLM hiện tại).")
        if skipped and not queued:
            messages.warning(request, "Không gửi được sách nào.")

    @admin.action(description="Ngưng làm sạch hàng loạt")
    def cancel_bulk_clean_text(self, request, queryset):
        cancelled = 0
        skipped = 0
        for book in queryset:
            ok, detail = self._cancel_book_cleaning(book)
            if ok:
                cancelled += 1
                messages.info(request, f"«{book.title}»: {detail}")
            else:
                skipped += 1
                messages.warning(request, f"«{book.title}»: {detail}")
        if cancelled:
            messages.success(request, f"Đã gửi hủy làm sạch {cancelled} sách.")
        if skipped and not cancelled:
            messages.warning(request, "Không có sách nào đang làm sạch.")

    @admin.action(description="Rút trích hình ảnh hàng loạt")
    def extract_images_bulk(self, request, queryset):
        queued = 0
        skipped = 0
        for book in queryset:
            ok, detail = enqueue_book_image_extract(book)
            if ok:
                queued += 1
            else:
                skipped += 1
                messages.warning(request, f"«{book.title}»: {detail}")
        if queued:
            messages.success(request, f"Đã gửi {queued} sách vào hàng đợi rút trích hình.")
        if skipped and not queued:
            messages.warning(request, "Không gửi được sách nào.")

    @admin.action(description="Ngưng rút trích hình")
    def cancel_extract_images(self, request, queryset):
        cancelled = 0
        skipped = 0
        for book in queryset:
            ok, detail = cancel_book_image_extract(book)
            if ok:
                cancelled += 1
                messages.info(request, f"«{book.title}»: {detail}")
            else:
                skipped += 1
                messages.warning(request, f"«{book.title}»: {detail}")
        if cancelled:
            messages.success(request, f"Đã gửi hủy rút trích hình {cancelled} sách.")
        if skipped and not cancelled:
            messages.warning(request, "Không có sách nào đang rút trích hình.")

    @admin.action(description="Đổi trạng thái hàng loạt")
    def bulk_change_status(self, request, queryset):
        if "apply" in request.POST:
            new_status = (request.POST.get("new_status") or "").strip()
            valid = {value for value, _label in BookStatus.choices}
            if new_status not in valid:
                messages.error(request, "Trạng thái không hợp lệ.")
                return None

            label = dict(BookStatus.choices)[new_status]
            updated = 0
            synced = 0
            for book in queryset:
                if book.status == BookStatus.PROCESSING and new_status != BookStatus.PROCESSING:
                    cancel_book_ocr(str(book.id))
                fields = {"status": new_status, "updated_at": timezone.now()}
                if new_status in {BookStatus.NEW, BookStatus.DONE, BookStatus.APPROVED}:
                    fields["error_message"] = ""
                Book.objects.filter(pk=book.pk).update(**fields)
                updated += 1
                if new_status == BookStatus.APPROVED:
                    book.refresh_from_db()
                    from apps.dataset.ingest import sync_source_from_book

                    try:
                        if sync_source_from_book(
                            book,
                            created_by=request.user if request.user.is_authenticated else None,
                        ):
                            synced += 1
                    except Exception:
                        messages.warning(request, f"«{book.title}»: đã duyệt nhưng không thêm được vào Dataset.")
            if updated:
                extra = f" Đã thêm {synced} sách vào Dataset." if synced else ""
                messages.success(request, f"Đã đổi {updated} sách sang «{label}».{extra}")
            return None

        context = {
            **self.admin_site.each_context(request),
            "title": "Đổi trạng thái hàng loạt",
            "queryset": queryset,
            "status_choices": BookStatus.choices,
            "opts": self.model._meta,
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
            "changelist_url": reverse("admin:documents_book_changelist"),
        }
        return TemplateResponse(request, "admin/documents/book_bulk_status.html", context)

    @admin.action(description="Kiểm tra trùng tên toàn bộ sách (LLM, mỗi lần ~30 tên)")
    def check_duplicate_titles(self, request, queryset):
        from .admin_staging import start_duplicate_title_check

        start_duplicate_title_check(request)
        messages.success(request, "Đã gửi kiểm tra trùng tên. Mở «Sách trùng tên» để xem kết quả.")
        return redirect("admin:documents_book_duplicate_titles")

    def duplicate_titles_view(self, request):
        from .admin_staging import duplicate_report_view

        return duplicate_report_view(request, extra_context=self.admin_site.each_context(request))

    @admin.action(description="Phân loại tự động (LLM, ~20 trang đầu)")
    def auto_classify(self, request, queryset):
        queued = 0
        for book in queryset:
            if self._run_book_classify(request, book, async_ok=True):
                queued += 1
        if queued:
            messages.success(request, f"Đã gửi {queued} sách vào hàng đợi phân loại.")

    @admin.action(description="Hủy OCR đang chạy")
    def cancel_processing(self, request, queryset):
        cancelled = 0
        for book in queryset:
            if book.status != BookStatus.PROCESSING:
                messages.warning(request, f"Sách «{book.title}» không đang OCR — bỏ qua.")
                continue
            msg = cancel_book_ocr(str(book.id))
            Book.objects.filter(pk=book.pk).update(
                status=BookStatus.NEW,
                error_message="Người dùng đã hủy OCR.",
                updated_at=timezone.now(),
            )
            cancelled += 1
            messages.info(request, f"«{book.title}»: {msg}")
        if cancelled:
            messages.success(request, f"Đã gửi hủy {cancelled} sách.")

    @admin.action(description="Tải Markdown đã chọn (ZIP)")
    def download_markdown_zip(self, request, queryset):
        books = [
            b for b in queryset.order_by("title")
            if b.is_ocr_ready and book_has_markdown(b)
        ]
        if not books:
            self.message_user(request, "Không có sách hoàn thành nào có Markdown để tải.", level=messages.WARNING)
            return None

        buf = io.BytesIO()
        used_names: set[str] = set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for book in books:
                content = book_markdown_content(book)
                if not content.strip():
                    continue
                entry = f"{book.slug}.md"
                if entry in used_names:
                    entry = f"{book.slug}-{book.pk}.md"
                used_names.add(entry)
                zf.writestr(entry, content.encode("utf-8"))

        if not used_names:
            self.message_user(request, "Các sách đã chọn không có nội dung Markdown.", level=messages.WARNING)
            return None

        buf.seek(0)
        response = HttpResponse(buf.read(), content_type="application/zip")
        response["Content-Disposition"] = 'attachment; filename="sach-markdown.zip"'
        return response


@admin.register(Page)
class PageAdmin(BookPageNavMixin, ModelAdmin):
    form = PageReviewForm
    change_form_template = "admin/documents/page_change_form.html"
    change_form_outer_before_template = "admin/documents/empty.html"
    show_save_and_continue = False
    show_save_and_add_another = False
    save_on_top = False
    list_display = ("page_number", "ocr_mode", "ocr_tool_badge", "has_table", "has_formula", "text_preview")
    list_display_links = ("page_number",)
    list_filter = ("ocr_mode", "has_table", "has_formula")
    search_fields = ("text_md",)
    list_per_page = 50
    change_list_template = "admin/documents/book_page_changelist.html"
    actions_submit_line = ["rerun_ocr", "clean_text", "restore_ocr"]

    def get_fieldsets(self, request, obj=None):
        return [(None, {"fields": ["text_md"]})]

    def has_module_permission(self, request):
        return False

    def changelist_view(self, request, extra_context=None):
        if not getattr(self, "_book_scope_id", None):
            return redirect("admin:documents_book_changelist")
        extra_context = extra_context or {}
        book = getattr(self, "_book_scope", None)
        if book:
            extra_context.setdefault("title", f"Các trang — {book.title}")
            extra_context.setdefault("book_scope", book)
            extra_context.setdefault(
                "book_change_url",
                reverse("admin:documents_book_change", args=[book.pk]),
            )
            extra_context.setdefault("custom_header_parts", build_page_header_parts(request, book=book))
        return super().changelist_view(request, extra_context)

    def book_changelist_view(self, request, book_id):
        book = get_object_or_404(Book, pk=book_id)
        self._book_scope_id = str(book.pk)
        self._book_scope = book
        try:
            return self.changelist_view(
                request,
                extra_context={
                    "title": f"Các trang — {book.title}",
                    "book_scope": book,
                    "book_change_url": reverse("admin:documents_book_change", args=[book.pk]),
                },
            )
        finally:
            self._book_scope_id = None
            self._book_scope = None

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("book", "ocr_provider").prefetch_related("boxes")
        book_id = getattr(self, "_book_scope_id", None)
        if book_id:
            qs = qs.filter(book_id=book_id)
        return qs

    def get_urls(self):
        return [
            path(
                "book/<path:book_id>/",
                self.admin_site.admin_view(self.book_changelist_view),
                name="documents_page_book_changelist",
            ),
            path(
                "<path:object_id>/page-image/",
                self.admin_site.admin_view(self.serve_page_image),
                name="documents_page_image",
            ),
            *super().get_urls(),
        ]

    def serve_page_image(self, request, object_id):
        page = self.get_object(request, object_id)
        if not page:
            raise Http404

        # Bbox lưu theo ảnh đã detect — phải hiển thị đúng file đó, không re-render PDF.
        f, _path = _open_page_image_file(page)
        if f:
            return FileResponse(f, content_type="image/png")

        png = _render_page_png_from_pdf(page)
        if png:
            return HttpResponse(png, content_type="image/png")

        raise Http404

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id)
        extra_context["filtered_nav"] = build_book_page_nav(request, obj)
        extra_context["filtered_nav_label"] = self.get_filtered_nav_label(obj) if obj else ""
        extra_context["filtered_nav_back_label"] = "Về sách"
        if obj:
            extra_context["custom_header_parts"] = build_page_header_parts(request, page=obj)
        extra_context["show_save_and_continue"] = False
        extra_context["show_save_and_add_another"] = False
        extra_context["page_ocr_tool_html"] = _ocr_tool_badge_html(obj) if obj else ""
        extra_context["page_content_types_html"] = _page_content_types_html(obj) if obj else ""
        extra_context["page_has_boxes"] = bool(obj.boxes.all()) if obj else False
        extra_context["rerun_ocr_action_name"] = "documents_page_rerun_ocr"
        extra_context["clean_text_action_name"] = "documents_page_clean_text"
        extra_context["restore_ocr_action_name"] = "documents_page_restore_ocr"
        extra_context["page_text_source"] = page_text_source(obj) if obj else "ocr"
        extra_context["has_cleaned_text"] = page_has_cleaned_text(obj) if obj else False
        extra_context["can_rerun_ocr"] = bool(
            obj and obj.book_id and obj.book.status != BookStatus.PROCESSING and obj.book.pdf_file
        )
        extra_context["rerun_ocr_blocked_reason"] = ""
        extra_context["can_clean_text"] = False
        extra_context["clean_text_blocked_reason"] = ""
        if obj:
            if obj.book.status == BookStatus.PROCESSING:
                extra_context["rerun_ocr_blocked_reason"] = "Sách đang OCR cả cuốn — đợi xong rồi OCR lại trang."
            elif not obj.book.pdf_file:
                extra_context["rerun_ocr_blocked_reason"] = "Sách chưa có file PDF."
            extra_context["clean_text_blocked_reason"] = self._page_clean_blocked_reason(obj)
            extra_context["can_clean_text"] = not extra_context["clean_text_blocked_reason"]
        if obj:
            image_url = _page_image_admin_url(obj)
            if image_url:
                extra_context["page_preview_html"] = mark_safe(_page_image_html(obj, image_url=image_url))
            else:
                extra_context["page_preview_html"] = mark_safe(
                    '<p style="color:#64748b">Chưa có ảnh trang.</p>'
                )
        return super(BookPageNavMixin, self).change_view(request, object_id, form_url, extra_context)

    def response_change(self, request, obj):
        action_keys = (
            "documents_page_rerun_ocr",
            "documents_page_clean_text",
            "documents_page_restore_ocr",
        )
        if any(key in request.POST for key in action_keys):
            return self.response_post_save_change(request, obj)
        return super().response_change(request, obj)

    def save_model(self, request, obj, form, change):
        posted = form.cleaned_data.get("text_md")
        if posted is None:
            posted = obj.text_md or ""

        if "documents_page_restore_ocr" in request.POST:
            if obj.pk:
                db_ocr = Page.objects.filter(pk=obj.pk).values_list("text_md", flat=True).first()
                if db_ocr is not None:
                    obj.text_md = db_ocr
            super().save_model(request, obj, form, change)
            return

        if obj.pk and page_has_cleaned_text(obj):
            save_page_review_text(obj, posted)
            db_ocr = Page.objects.filter(pk=obj.pk).values_list("text_md", flat=True).first()
            if db_ocr is not None:
                obj.text_md = db_ocr
            super().save_model(request, obj, form, change)
            return

        obj.text_md = posted
        super().save_model(request, obj, form, change)

    def _page_clean_blocked_reason(self, obj) -> str:
        if obj.book.status == BookStatus.PROCESSING:
            return "Sách đang OCR cả cuốn — đợi xong rồi làm sạch trang."
        if obj.book.cleaning_batches.filter(
            status__in=[CleaningBatchStatus.PENDING, CleaningBatchStatus.PROCESSING]
        ).exists():
            return "Đang có lần làm sạch cả sách đang chạy."
        if not (obj.text_md or "").strip():
            return "Trang chưa có nội dung OCR để làm sạch."
        if not LLMProvider.objects.filter(is_active=True).exists():
            return "Chưa có LLM. Vào Admin → LLM để khai báo."
        return ""

    def response_post_save_change(self, request, obj):
        """Sau Lưu lại, giữ nguyên màn hình review trang (slider prev/next)."""
        opts = self.model._meta
        redirect_url = reverse(
            "admin:%s_%s_change" % (opts.app_label, opts.model_name),
            args=[quote(obj.pk)],
            current_app=self.admin_site.name,
        )
        redirect_url = add_preserved_filters(
            {"preserved_filters": self.get_preserved_filters(request), "opts": opts},
            redirect_url,
        )
        return HttpResponseRedirect(redirect_url)

    @action(description="OCR lại trang này", icon="refresh", variant=ActionVariant.PRIMARY)
    def rerun_ocr(self, request, obj):
        if obj.book.status == BookStatus.PROCESSING:
            messages.warning(request, "Sách đang OCR cả cuốn — đợi xong rồi OCR lại trang này.")
            return
        if not obj.book.pdf_file:
            messages.error(request, "Sách chưa có file PDF.")
            return
        current_app.send_task(
            "apps.documents.tasks.ocr_single_page",
            kwargs={"page_id": str(obj.id)},
            queue="gpu",
        )
        messages.success(
            request,
            f"Đã gửi OCR lại trang {obj.page_number}. Tải lại trang sau vài giây để xem kết quả.",
        )

    @action(description="Làm sạch trang này", icon="wand_stars", variant=ActionVariant.DEFAULT)
    def clean_text(self, request, obj):
        reason = self._page_clean_blocked_reason(obj)
        if reason:
            messages.warning(request, reason)
            return
        try:
            config = get_cleaning_config()
            provider = resolve_cleaning_provider(config)
        except RuntimeError as e:
            messages.error(request, str(e))
            return

        batch = get_active_cleaning_batch(obj.book)
        if batch is None:
            prompt = (config.prompt or "").strip() or default_text_cleaning_prompt()
            batch = BookCleaningBatch.objects.create(
                book=obj.book,
                provider=provider,
                model=config.model or "",
                prompt=prompt,
                status=CleaningBatchStatus.PENDING,
                created_by=request.user if request.user.is_authenticated else None,
            )
        current_app.send_task(
            "apps.documents.tasks.clean_single_page",
            kwargs={"page_id": str(obj.id), "batch_id": str(batch.id)},
            queue="cpu",
        )
        messages.success(
            request,
            f"Đã gửi làm sạch trang {obj.page_number}. Tải lại trang sau vài giây để xem kết quả.",
        )

    @action(description="Dùng lại OCR", icon="undo", variant=ActionVariant.DEFAULT)
    def restore_ocr(self, request, obj):
        if discard_page_cleaned_text(obj):
            messages.success(
                request,
                f"Đã bỏ bản làm sạch trang {obj.page_number}. Đang hiện kết quả OCR gốc.",
            )
        else:
            messages.info(request, "Trang này đang là OCR gốc — không có bản làm sạch để bỏ.")

    @admin.display(description="Công cụ OCR", ordering="ocr_tool_name")
    def ocr_tool_badge(self, obj):
        return mark_safe(_ocr_tool_badge_html(obj))

    @admin.display(description="OCR")
    def text_preview(self, obj):
        t = (obj.text_md or "").strip()
        return t[:80] + "..." if len(t) > 80 else t

    def get_filtered_nav_label(self, obj):
        return f"Trang {obj.page_number}"


from . import admin_staging  # noqa: E402, F401
