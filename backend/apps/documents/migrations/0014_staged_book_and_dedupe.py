import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0013_book_cleaning_coverage"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StagedBook",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("original_name", models.CharField(max_length=255, verbose_name="Tên file")),
                ("pdf_file", models.FileField(blank=True, upload_to="staging/pdfs/", verbose_name="File PDF")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Chờ xử lý"),
                            ("processing", "Đang xử lý"),
                            ("created", "Đã tạo sách"),
                            ("duplicate", "Trùng — đã xóa file"),
                            ("failed", "Lỗi"),
                        ],
                        default="pending",
                        max_length=16,
                        verbose_name="Trạng thái",
                    ),
                ),
                ("extracted_title", models.CharField(blank=True, max_length=255, verbose_name="Tên rút trích")),
                ("first_page_text", models.TextField(blank=True, verbose_name="OCR trang đầu")),
                ("error_message", models.TextField(blank=True, verbose_name="Lỗi")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_book",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="staging_sources",
                        to="documents.book",
                        verbose_name="Sách đã tạo",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="staged_books",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "matched_book",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="staging_matches",
                        to="documents.book",
                        verbose_name="Trùng với sách",
                    ),
                ),
            ],
            options={
                "verbose_name": "Sách chờ nhập",
                "verbose_name_plural": "Sách chờ nhập",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="BookDuplicateReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Chờ xử lý"),
                            ("processing", "Đang kiểm tra"),
                            ("done", "Hoàn thành"),
                            ("failed", "Lỗi"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                (
                    "groups",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="[{canonical, books: [{id, title}]}]",
                        verbose_name="Nhóm trùng",
                    ),
                ),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="duplicate_reports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Báo cáo sách trùng",
                "verbose_name_plural": "Báo cáo sách trùng",
                "ordering": ["-created_at"],
            },
        ),
    ]
