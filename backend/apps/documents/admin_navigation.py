"""Điều hướng prev/next + slider trên trang chi tiết Page (trong phạm vi 1 sách)."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode

from django.db.models import Model
from django.http import HttpRequest
from django.urls import reverse

PAGE_FILTER_FIELDS = ("ocr_mode", "has_table", "has_formula")


def parse_page_filters(request: HttpRequest) -> dict[str, str]:
    """Đọc bộ lọc trang từ query string hoặc _changelist_filters của Admin."""
    active: dict[str, str] = {}

    for key in PAGE_FILTER_FIELDS:
        val = request.GET.get(key)
        if val is None or val == "":
            val = request.GET.get(f"{key}__exact")
        if val is not None and val != "":
            active[key] = val

    preserved = request.GET.get("_changelist_filters", "")
    if preserved:
        for key, value in parse_qsl(preserved, keep_blank_values=True):
            for field in PAGE_FILTER_FIELDS:
                if key in (field, f"{field}__exact") and value != "":
                    active[field] = value

    return active


def page_filter_qs(filters: dict[str, str]) -> dict[str, Any]:
    qs: dict[str, Any] = {}
    if ocr_mode := filters.get("ocr_mode"):
        qs["ocr_mode__exact"] = ocr_mode
    for key in ("has_table", "has_formula"):
        if key in filters:
            qs[f"{key}__exact"] = filters[key] in ("1", "True", "true", "yes")
    return qs


def page_filter_admin_query_string(filters: dict[str, str]) -> str:
    """Query string cho changelist Admin (ocr_mode__exact=...)."""
    params: dict[str, str] = {}
    if ocr_mode := filters.get("ocr_mode"):
        params["ocr_mode__exact"] = ocr_mode
    for key in ("has_table", "has_formula"):
        if key in filters:
            params[f"{key}__exact"] = "1" if filters[key] in ("1", "True", "true", "yes") else "0"
    return urlencode(params)


def get_book_page_ids(book_id, filters: dict[str, str]) -> list:
    from apps.documents.models import Page

    return list(
        Page.objects.filter(book_id=book_id, **page_filter_qs(filters))
        .order_by("page_number")
        .values_list("pk", flat=True)
    )


def _page_change_url(pk, filters: dict[str, str]) -> str:
    url = reverse("admin:documents_page_change", args=[pk])
    admin_qs = page_filter_admin_query_string(filters)
    if admin_qs:
        url = f"{url}?{urlencode({'_changelist_filters': admin_qs})}"
    return url


def first_page_change_url(book_id) -> str | None:
    from apps.documents.models import Page

    pk = (
        Page.objects.filter(book_id=book_id)
        .order_by("page_number")
        .values_list("pk", flat=True)
        .first()
    )
    if not pk:
        return None
    return reverse("admin:documents_page_change", args=[pk])


def book_pages_list_url(book_id, filters: dict[str, str] | None = None) -> str:
    url = reverse("admin:documents_page_book_changelist", args=[book_id])
    if filters:
        admin_qs = page_filter_admin_query_string(filters)
        if admin_qs:
            url = f"{url}?{admin_qs}"
    return url


def build_page_header_parts(request: HttpRequest, *, page: Model | None = None, book=None) -> list[dict[str, Any]]:
    """Breadcrumb Admin: Tài liệu → Sách → trang hiện tại (không qua danh sách trang)."""
    from apps.documents.models import Book, Page

    current_app = getattr(request, "current_app", None) or "admin"
    page_opts = Page._meta
    book_opts = Book._meta
    filters = parse_page_filters(request) if page else {}

    if page is not None:
        book = page.book

    parts: list[dict[str, Any]] = [
        {
            "link": reverse(f"{current_app}:documents_book_changelist"),
            "title": page_opts.app_config.verbose_name,
        },
    ]
    if book is not None:
        parts.append(
            {
                "link": reverse(f"{current_app}:documents_book_change", args=[book.pk]),
                "title": book_opts.verbose_name,
            }
        )
        if page is None:
            parts.append(
                {
                    "link": book_pages_list_url(book.pk, filters),
                    "title": page_opts.verbose_name_plural,
                }
            )

    if page is not None:
        change_url = reverse(f"{current_app}:documents_page_change", args=[page.pk])
        admin_qs = page_filter_admin_query_string(filters)
        if admin_qs:
            change_url = f"{change_url}?{urlencode({'_changelist_filters': admin_qs})}"
        parts.append({"link": change_url, "title": str(page)})

    return parts


def build_book_page_nav(request: HttpRequest, page: Model | None) -> dict[str, Any] | None:
    """Prev/next/slider chỉ trong các trang cùng sách + cùng bộ lọc."""
    if page is None or not page.pk:
        return None

    filters = parse_page_filters(request)
    ids = get_book_page_ids(page.book_id, filters)

    try:
        idx = ids.index(page.pk)
    except ValueError:
        return None

    urls = [_page_change_url(pk, filters) for pk in ids]

    return {
        "prev_url": urls[idx - 1] if idx > 0 else "",
        "next_url": urls[idx + 1] if idx < len(ids) - 1 else "",
        "current": idx + 1,
        "total": len(ids),
        "urls": urls,
        "changelist_url": reverse("admin:documents_book_change", args=[page.book_id]),
        "has_filters": bool(filters),
    }


class BookPageNavMixin:
    """Mixin PageAdmin: prev/next + slider trong phạm vi sách."""

    change_form_outer_before_template = "admin/documents/filtered_nav.html"

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id)
        extra_context["filtered_nav"] = build_book_page_nav(request, obj)
        extra_context["filtered_nav_label"] = self.get_filtered_nav_label(obj) if obj else ""
        extra_context["filtered_nav_back_label"] = "Về sách"
        return super().change_view(request, object_id, form_url, extra_context)

    def get_filtered_nav_label(self, obj: Model) -> str:
        return str(obj)
