from __future__ import annotations

from datetime import timedelta

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin, TabularInline

from apps.sharing.forms import CreateShareForm
from apps.sharing.models import BookShare, BookShareItem
from apps.sharing.services import create_book_share
from apps.sharing.tokens import new_share_password, share_public_url


class BookShareItemInline(TabularInline):
    model = BookShareItem
    extra = 0
    can_delete = False
    autocomplete_fields = ()
    readonly_fields = ("book",)
    fields = ("book",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(BookShare)
class BookShareAdmin(ModelAdmin):
    list_display = (
        "token_link",
        "status_badge",
        "expires_at",
        "content_label",
        "book_count",
        "visitor_count",
        "download_count",
        "created_by",
        "created_at",
    )
    list_filter = ("is_active",)
    list_display_links = None
    search_fields = ("token", "note", "items__book__title", "visitors__email", "visitors__name")
    readonly_fields = (
        "token",
        "public_link",
        "password_note",
        "content_label",
        "created_by",
        "created_at",
        "updated_at",
        "visitor_table",
        "download_table",
    )
    fields = (
        "token",
        "public_link",
        "password_note",
        "note",
        "expires_at",
        "allow_pdf",
        "allow_md",
        "is_active",
        "content_label",
        "created_by",
        "created_at",
        "updated_at",
        "visitor_table",
        "download_table",
    )
    inlines = [BookShareItemInline]
    actions = ["revoke_shares", "reactivate_shares"]
    ordering = ("-created_at",)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _book_count=Count("items", distinct=True),
                _visitor_count=Count("visitors", distinct=True),
                _download_count=Count("downloads", distinct=True),
            )
            .select_related("created_by")
        )

    def has_add_permission(self, request):
        return False

    def get_urls(self):
        return [
            path(
                "created/",
                self.admin_site.admin_view(self.created_view),
                name="sharing_bookshare_created",
            ),
            *super().get_urls(),
        ]

    def created_view(self, request):
        payload = request.session.pop("share_created", None)
        if not payload:
            raise Http404
        share = get_object_or_404(BookShare, pk=payload.get("id"))
        context = {
            **self.admin_site.each_context(request),
            "title": "Đã tạo liên kết chia sẻ",
            "share": share,
            "public_url": payload.get("url") or share.public_url,
            "password": payload.get("password") or "",
            "changelist_url": reverse("admin:sharing_bookshare_changelist"),
            "share_url": reverse("admin:sharing_bookshare_change", args=[share.pk]),
        }
        return TemplateResponse(request, "admin/sharing/share_created.html", context)

    @admin.display(description="Mã / link", ordering="token")
    def token_link(self, obj):
        url = reverse("admin:sharing_bookshare_change", args=[obj.pk])
        return format_html('<a href="{}" style="font-family:ui-monospace,monospace;font-weight:600;">{}</a>', url, obj.token)

    @admin.display(description="Link")
    def public_link(self, obj):
        url = obj.public_url
        return format_html(
            '<input type="text" readonly value="{}" '
            'style="width:min(32rem,70%);font-family:ui-monospace,monospace;" onclick="this.select()">'
            '<button type="button" class="button" style="margin-left:8px" '
            'onclick="navigator.clipboard.writeText(this.previousElementSibling.value)">Copy</button>',
            url,
        )

    @admin.display(description="Mật khẩu")
    def password_note(self, obj):
        return "Đã hash — không xem lại được."

    @admin.display(description="Trạng thái")
    def status_badge(self, obj):
        if not obj.is_active:
            return format_html('<span style="color:#b91c1c;font-weight:600;">Đã thu hồi</span>')
        if obj.is_expired:
            return format_html('<span style="color:#b45309;font-weight:600;">Hết hạn</span>')
        return format_html('<span style="color:#0f766e;font-weight:600;">Đang mở</span>')

    @admin.display(description="Nội dung")
    def content_label(self, obj):
        parts = []
        if obj.allow_pdf:
            parts.append("PDF")
        if obj.allow_md:
            parts.append("Markdown")
        return " · ".join(parts) or "—"

    @admin.display(description="Sách", ordering="_book_count")
    def book_count(self, obj):
        return int(getattr(obj, "_book_count", 0) or 0)

    @admin.display(description="Người nhận", ordering="_visitor_count")
    def visitor_count(self, obj):
        return int(getattr(obj, "_visitor_count", 0) or 0)

    @admin.display(description="Lượt tải", ordering="_download_count")
    def download_count(self, obj):
        return int(getattr(obj, "_download_count", 0) or 0)

    @admin.display(description="Truy cập theo email + tên")
    def visitor_table(self, obj):
        if not obj or not obj.pk:
            return "—"
        rows = list(obj.visitors.all()[:200])
        if not rows:
            return format_html('<span style="color:#94a3b8;">Chưa có lượt truy cập.</span>')
        body = format_html_join(
            "",
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
            (
                (
                    row.name,
                    row.email,
                    row.visit_count,
                    timezone.localtime(row.first_seen_at).strftime("%d/%m/%Y %H:%M"),
                    timezone.localtime(row.last_seen_at).strftime("%d/%m/%Y %H:%M"),
                )
                for row in rows
            ),
        )
        return format_html(
            '<table class="table"><thead><tr>'
            "<th>Tên</th><th>Email</th><th>Số lần</th><th>Lần đầu</th><th>Lần cuối</th>"
            "</tr></thead><tbody>{}</tbody></table>",
            body,
        )

    @admin.display(description="Lượt tải")
    def download_table(self, obj):
        if not obj or not obj.pk:
            return "—"
        rows = list(obj.downloads.select_related("visitor", "book")[:300])
        if not rows:
            return format_html('<span style="color:#94a3b8;">Chưa có lượt tải.</span>')
        body = format_html_join(
            "",
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
            (
                (
                    f"{row.visitor.name} <{row.visitor.email}>",
                    row.book.title,
                    row.get_kind_display(),
                    timezone.localtime(row.created_at).strftime("%d/%m/%Y %H:%M"),
                )
                for row in rows
            ),
        )
        return format_html(
            '<table class="table"><thead><tr>'
            "<th>Người nhận</th><th>Sách</th><th>File</th><th>Lúc</th>"
            "</tr></thead><tbody>{}</tbody></table>",
            body,
        )

    @admin.action(description="Thu hồi lần chia sẻ")
    def revoke_shares(self, request, queryset):
        n = queryset.update(is_active=False, updated_at=timezone.now())
        messages.success(request, f"Đã thu hồi {n} lần chia sẻ.")

    @admin.action(description="Mở lại lần chia sẻ")
    def reactivate_shares(self, request, queryset):
        n = queryset.filter(expires_at__gt=timezone.now()).update(is_active=True, updated_at=timezone.now())
        skipped = queryset.count() - n
        if n:
            messages.success(request, f"Đã mở lại {n} lần chia sẻ.")
        if skipped:
            messages.warning(request, f"Bỏ qua {skipped} lần đã hết hạn — gia hạn trước khi mở lại.")


def render_create_share(model_admin, request, queryset):
    books = list(queryset)
    if not books:
        messages.warning(request, "Chưa chọn sách.")
        return None
    if "apply" in request.POST:
        form = CreateShareForm(request.POST)
        if form.is_valid():
            share = create_book_share(
                books=books,
                password=form.cleaned_data["password"],
                expires_at=form.cleaned_data["expires_at"],
                allow_pdf=form.cleaned_data["allow_pdf"],
                allow_md=form.cleaned_data["allow_md"],
                note=form.cleaned_data.get("note") or "",
                created_by=request.user if request.user.is_authenticated else None,
            )
            request.session["share_created"] = {
                "id": str(share.pk),
                "password": form.cleaned_data["password"],
                "url": share_public_url(share.token),
            }
            messages.success(request, f"Đã tạo liên kết chia sẻ {share.token}.")
            return redirect("admin:sharing_bookshare_created")
    else:
        default_expiry = timezone.localtime(timezone.now() + timedelta(days=7)).replace(second=0, microsecond=0)
        form = CreateShareForm(
            initial={
                "expires_at": default_expiry,
                "allow_pdf": True,
                "allow_md": True,
                "password": new_share_password(),
            }
        )
    context = {
        **model_admin.admin_site.each_context(request),
        "title": "Chia sẻ sách",
        "form": form,
        "queryset": books,
        "opts": model_admin.model._meta,
        "action_checkbox_name": ACTION_CHECKBOX_NAME,
        "changelist_url": reverse("admin:documents_book_changelist"),
        "expires_local": timezone.localtime(
            form.initial.get("expires_at") or timezone.now() + timedelta(days=7)
        ).strftime("%Y-%m-%dT%H:%M")
        if not form.is_bound
        else (request.POST.get("expires_at") or ""),
        "password_value": form.data.get("password") if form.is_bound else form.initial.get("password", ""),
    }
    return TemplateResponse(request, "admin/sharing/create_share.html", context)
