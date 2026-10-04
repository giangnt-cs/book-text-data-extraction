from __future__ import annotations

from django.contrib.auth.hashers import make_password
from django.db import transaction

from apps.documents.models import Book
from apps.sharing.models import BookShare, BookShareItem
from apps.sharing.tokens import new_share_token


def create_book_share(
    *,
    books: list[Book],
    password: str,
    expires_at,
    allow_pdf: bool,
    allow_md: bool,
    note: str = "",
    created_by=None,
) -> BookShare:
    if not books:
        raise ValueError("Chưa chọn sách.")
    password = (password or "").strip()
    if len(password) < 8:
        raise ValueError("Mật khẩu tối thiểu 8 ký tự.")
    token = new_share_token()
    while BookShare.objects.filter(token=token).exists():
        token = new_share_token()
    with transaction.atomic():
        share = BookShare.objects.create(
            token=token,
            password_hash=make_password(password),
            expires_at=expires_at,
            allow_pdf=bool(allow_pdf),
            allow_md=bool(allow_md),
            is_active=True,
            note=(note or "").strip()[:255],
            created_by=created_by,
        )
        BookShareItem.objects.bulk_create(
            [BookShareItem(share=share, book=book) for book in books],
            ignore_conflicts=True,
        )
    return share
