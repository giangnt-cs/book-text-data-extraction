from __future__ import annotations

import re
from io import BytesIO

from django.contrib.auth.hashers import check_password
from django.db.models import F
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.documents.markdown import book_has_markdown, book_markdown_content
from apps.sharing.forms import ShareGateForm
from apps.sharing.models import BookShare, BookShareItem, ShareDownload, ShareFileKind, ShareVisitor
from apps.sharing.rate_limit import clear_gate_failures, gate_blocked, record_gate_failure
from apps.sharing.tokens import (
    COOKIE_NAME,
    COOKIE_PATH,
    cookie_secure,
    sign_share_session,
    unsign_share_session,
)

_GENERIC_DEAD = "Liên kết không còn hiệu lực."
_GENERIC_AUTH = "Không xác thực được. Kiểm tra mật khẩu rồi thử lại."


def _client_ip(request: HttpRequest) -> str:
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return forwarded or (request.META.get("REMOTE_ADDR") or "").strip()


def _load_share(token: str) -> BookShare | None:
    token = (token or "").strip()
    if not token or len(token) > 32:
        return None
    return BookShare.objects.filter(token=token).first()


def _session_pair(request: HttpRequest, token: str) -> tuple[BookShare, ShareVisitor] | None:
    parsed = unsign_share_session(request.COOKIES.get(COOKIE_NAME) or "")
    if parsed is None:
        return None
    share_id, sess_token, visitor_id = parsed
    if sess_token != token:
        return None
    share = BookShare.objects.filter(pk=share_id, token=token).first()
    if share is None or not share.is_usable:
        return None
    visitor = ShareVisitor.objects.filter(pk=visitor_id, share=share).first()
    if visitor is None:
        return None
    return share, visitor


def _attach_session(response: HttpResponse, share: BookShare, visitor: ShareVisitor) -> HttpResponse:
    value, max_age = sign_share_session(
        share_id=str(share.pk),
        token=share.token,
        visitor_id=str(visitor.pk),
        expires_at=share.expires_at,
    )
    if max_age <= 0:
        return response
    response.set_cookie(
        COOKIE_NAME,
        value,
        max_age=max_age,
        httponly=True,
        secure=cookie_secure(),
        samesite="Lax",
        path=COOKIE_PATH,
    )
    return response


def _safe_filename(book, ext: str) -> str:
    raw = (getattr(book, "slug", None) or "sach").strip()
    raw = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(".-") or "sach"
    return f"{raw[:80]}.{ext}"


def _secure_headers(response: HttpResponse) -> HttpResponse:
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "no-store"
    return response


def _error_page(request: HttpRequest, message: str, *, status: int = 404) -> HttpResponse:
    response = render(
        request,
        "sharing/error.html",
        {"title": "Liên kết chia sẻ", "message": message},
        status=status,
    )
    return _secure_headers(response)


def _catalog_rows(share: BookShare) -> list[dict]:
    rows = []
    items = BookShareItem.objects.filter(share=share).select_related("book").order_by("book__title")
    for item in items:
        book = item.book
        has_pdf = bool(share.allow_pdf and book.pdf_file and book.pdf_file.name)
        has_md = bool(share.allow_md and book_has_markdown(book))
        rows.append(
            {
                "item": item,
                "book": book,
                "has_pdf": has_pdf,
                "has_md": has_md,
            }
        )
    return rows


@require_http_methods(["GET", "POST"])
def share_gate(request: HttpRequest, token: str):
    share = _load_share(token)
    if share is None or not share.is_usable:
        return _error_page(request, _GENERIC_DEAD)

    pair = _session_pair(request, token)
    if pair and request.method == "GET":
        return redirect("share_catalog", token=token)

    form = ShareGateForm(request.POST or None)
    error = ""
    ip = _client_ip(request)
    if request.method == "POST":
        if gate_blocked(token, ip):
            error = "Quá nhiều lần thử. Đợi vài phút rồi thử lại."
        elif not form.is_valid():
            error = _GENERIC_AUTH
        elif not check_password(form.cleaned_data["password"], share.password_hash):
            record_gate_failure(token, ip)
            error = _GENERIC_AUTH
        else:
            clear_gate_failures(token, ip)
            visitor, _created = ShareVisitor.objects.get_or_create(
                share=share,
                email=form.cleaned_data["email"],
                name=form.cleaned_data["name"],
            )
            ShareVisitor.objects.filter(pk=visitor.pk).update(
                visit_count=F("visit_count") + 1,
                last_seen_at=timezone.now(),
            )
            visitor.refresh_from_db()
            response = redirect("share_catalog", token=token)
            return _attach_session(response, share, visitor)

    response = render(
        request,
        "sharing/gate.html",
        {"title": "Tài liệu được chia sẻ", "form": form, "error": error, "token": token},
    )
    return _secure_headers(response)


@require_GET
def share_catalog(request: HttpRequest, token: str):
    pair = _session_pair(request, token)
    if pair is None:
        share = _load_share(token)
        if share is None or not share.is_usable:
            return _error_page(request, _GENERIC_DEAD)
        return redirect("share_gate", token=token)
    share, _visitor = pair
    response = render(
        request,
        "sharing/catalog.html",
        {
            "title": "Tài liệu được chia sẻ",
            "share": share,
            "rows": _catalog_rows(share),
            "expires_at": share.expires_at,
        },
    )
    return _secure_headers(response)


@require_GET
def share_download(request: HttpRequest, token: str, item_id: str, kind: str):
    pair = _session_pair(request, token)
    if pair is None:
        return _error_page(request, _GENERIC_DEAD, status=403)
    share, visitor = pair
    kind = (kind or "").strip().lower()
    if kind not in {ShareFileKind.PDF, ShareFileKind.MD}:
        return _error_page(request, _GENERIC_DEAD, status=404)
    if kind == ShareFileKind.PDF and not share.allow_pdf:
        return _error_page(request, _GENERIC_DEAD, status=404)
    if kind == ShareFileKind.MD and not share.allow_md:
        return _error_page(request, _GENERIC_DEAD, status=404)

    item = (
        BookShareItem.objects.filter(pk=item_id, share=share)
        .select_related("book")
        .first()
    )
    if item is None:
        return _error_page(request, _GENERIC_DEAD, status=404)
    book = item.book

    if kind == ShareFileKind.PDF:
        if not book.pdf_file or not book.pdf_file.name:
            return _error_page(request, "File PDF không còn.", status=404)
        try:
            fh = book.pdf_file.open("rb")
        except Exception:
            return _error_page(request, "Không đọc được file PDF.", status=404)
        ShareDownload.objects.create(
            share=share,
            visitor=visitor,
            book=book,
            kind=ShareFileKind.PDF,
            ip_address=_client_ip(request) or None,
        )
        return _secure_headers(
            FileResponse(
                fh,
                content_type="application/pdf",
                as_attachment=True,
                filename=_safe_filename(book, "pdf"),
            )
        )

    if not book_has_markdown(book):
        return _error_page(request, "Chưa có Markdown.", status=404)
    try:
        text = book_markdown_content(book) or ""
    except Exception:
        return _error_page(request, "Không đọc được Markdown.", status=404)
    ShareDownload.objects.create(
        share=share,
        visitor=visitor,
        book=book,
        kind=ShareFileKind.MD,
        ip_address=_client_ip(request) or None,
    )
    return _secure_headers(
        FileResponse(
            BytesIO(text.encode("utf-8")),
            content_type="text/markdown; charset=utf-8",
            as_attachment=True,
            filename=_safe_filename(book, "md"),
        )
    )
