from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.sharing.tokens import share_public_url


class ShareFileKind(models.TextChoices):
    PDF = "pdf", "PDF"
    MD = "md", "Markdown"


class BookShare(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token = models.CharField(max_length=32, unique=True, db_index=True, verbose_name="Mã link")
    password_hash = models.CharField(max_length=128, verbose_name="Mật khẩu (hash)")
    expires_at = models.DateTimeField(verbose_name="Hết hạn")
    allow_pdf = models.BooleanField(default=True, verbose_name="Cho tải PDF")
    allow_md = models.BooleanField(default=True, verbose_name="Cho tải Markdown")
    is_active = models.BooleanField(default=True, verbose_name="Đang mở")
    note = models.CharField(max_length=255, blank=True, default="", verbose_name="Ghi chú")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="book_shares",
        verbose_name="Người tạo",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Tạo lúc")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Cập nhật lúc")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Lần chia sẻ"
        verbose_name_plural = "Chia sẻ sách"

    def __str__(self) -> str:
        return self.token

    @property
    def public_url(self) -> str:
        return share_public_url(self.token)

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_usable(self) -> bool:
        return bool(self.is_active) and not self.is_expired


class BookShareItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    share = models.ForeignKey(BookShare, on_delete=models.CASCADE, related_name="items", verbose_name="Lần chia sẻ")
    book = models.ForeignKey(
        "documents.Book",
        on_delete=models.CASCADE,
        related_name="share_items",
        verbose_name="Sách",
    )

    class Meta:
        ordering = ["book__title"]
        verbose_name = "Sách trong lần chia sẻ"
        verbose_name_plural = "Sách trong lần chia sẻ"
        constraints = [
            models.UniqueConstraint(fields=["share", "book"], name="uniq_book_share_item"),
        ]

    def __str__(self) -> str:
        return f"{self.share.token} · {self.book.title}"


class ShareVisitor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    share = models.ForeignKey(BookShare, on_delete=models.CASCADE, related_name="visitors", verbose_name="Lần chia sẻ")
    name = models.CharField(max_length=120, verbose_name="Tên")
    email = models.EmailField(verbose_name="Email")
    visit_count = models.PositiveIntegerField(default=0, verbose_name="Số lần truy cập")
    first_seen_at = models.DateTimeField(auto_now_add=True, verbose_name="Lần đầu")
    last_seen_at = models.DateTimeField(auto_now=True, verbose_name="Lần cuối")

    class Meta:
        ordering = ["-last_seen_at"]
        verbose_name = "Người nhận"
        verbose_name_plural = "Người nhận"
        constraints = [
            models.UniqueConstraint(fields=["share", "email", "name"], name="uniq_book_share_visitor"),
        ]

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"


class ShareDownload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    share = models.ForeignKey(BookShare, on_delete=models.CASCADE, related_name="downloads", verbose_name="Lần chia sẻ")
    visitor = models.ForeignKey(
        ShareVisitor,
        on_delete=models.CASCADE,
        related_name="downloads",
        verbose_name="Người nhận",
    )
    book = models.ForeignKey(
        "documents.Book",
        on_delete=models.CASCADE,
        related_name="share_downloads",
        verbose_name="Sách",
    )
    kind = models.CharField(max_length=8, choices=ShareFileKind.choices, verbose_name="Loại file")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Tải lúc")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Lượt tải"
        verbose_name_plural = "Lượt tải"

    def __str__(self) -> str:
        return f"{self.visitor} · {self.book} · {self.kind}"
