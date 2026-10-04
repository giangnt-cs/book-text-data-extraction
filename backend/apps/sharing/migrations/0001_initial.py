import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("documents", "0015_chunking_window"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BookShare",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("token", models.CharField(db_index=True, max_length=32, unique=True, verbose_name="Mã link")),
                ("password_hash", models.CharField(max_length=128, verbose_name="Mật khẩu (hash)")),
                ("expires_at", models.DateTimeField(verbose_name="Hết hạn")),
                ("allow_pdf", models.BooleanField(default=True, verbose_name="Cho tải PDF")),
                ("allow_md", models.BooleanField(default=True, verbose_name="Cho tải Markdown")),
                ("is_active", models.BooleanField(default=True, verbose_name="Đang mở")),
                ("note", models.CharField(blank=True, default="", max_length=255, verbose_name="Ghi chú")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Tạo lúc")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Cập nhật lúc")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="book_shares",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Người tạo",
                    ),
                ),
            ],
            options={
                "verbose_name": "Lần chia sẻ",
                "verbose_name_plural": "Chia sẻ sách",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="BookShareItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "book",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="share_items",
                        to="documents.book",
                        verbose_name="Sách",
                    ),
                ),
                (
                    "share",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="sharing.bookshare",
                        verbose_name="Lần chia sẻ",
                    ),
                ),
            ],
            options={
                "verbose_name": "Sách trong lần chia sẻ",
                "verbose_name_plural": "Sách trong lần chia sẻ",
                "ordering": ["book__title"],
            },
        ),
        migrations.CreateModel(
            name="ShareVisitor",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=120, verbose_name="Tên")),
                ("email", models.EmailField(max_length=254, verbose_name="Email")),
                ("visit_count", models.PositiveIntegerField(default=0, verbose_name="Số lần truy cập")),
                ("first_seen_at", models.DateTimeField(auto_now_add=True, verbose_name="Lần đầu")),
                ("last_seen_at", models.DateTimeField(auto_now=True, verbose_name="Lần cuối")),
                (
                    "share",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="visitors",
                        to="sharing.bookshare",
                        verbose_name="Lần chia sẻ",
                    ),
                ),
            ],
            options={
                "verbose_name": "Người nhận",
                "verbose_name_plural": "Người nhận",
                "ordering": ["-last_seen_at"],
            },
        ),
        migrations.CreateModel(
            name="ShareDownload",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("pdf", "PDF"), ("md", "Markdown")], max_length=8, verbose_name="Loại file")),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True, verbose_name="IP")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Tải lúc")),
                (
                    "book",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="share_downloads",
                        to="documents.book",
                        verbose_name="Sách",
                    ),
                ),
                (
                    "share",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="downloads",
                        to="sharing.bookshare",
                        verbose_name="Lần chia sẻ",
                    ),
                ),
                (
                    "visitor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="downloads",
                        to="sharing.sharevisitor",
                        verbose_name="Người nhận",
                    ),
                ),
            ],
            options={
                "verbose_name": "Lượt tải",
                "verbose_name_plural": "Lượt tải",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="bookshareitem",
            constraint=models.UniqueConstraint(fields=("share", "book"), name="uniq_book_share_item"),
        ),
        migrations.AddConstraint(
            model_name="sharevisitor",
            constraint=models.UniqueConstraint(fields=("share", "email", "name"), name="uniq_book_share_visitor"),
        ),
    ]
