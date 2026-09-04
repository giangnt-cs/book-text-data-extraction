# Generated manually

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0005_text_cleaning"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents", "0002_page_ocr_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="BookCleaningBatch",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("prompt", models.TextField(verbose_name="Prompt đã dùng")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Chờ xử lý"),
                            ("processing", "Đang làm sạch"),
                            ("done", "Hoàn thành"),
                            ("failed", "Lỗi"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("error_message", models.TextField(blank=True, verbose_name="Lỗi")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "book",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cleaning_batches",
                        to="documents.book",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="cleaning_batches",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "provider",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="cleaning_batches",
                        to="llm.textcleaningprovider",
                    ),
                ),
            ],
            options={
                "verbose_name": "Lần làm sạch văn bản",
                "verbose_name_plural": "Lần làm sạch văn bản",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="PageCleanedText",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("text_md", models.TextField(blank=True, verbose_name="Văn bản đã làm sạch")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="page_texts",
                        to="documents.bookcleaningbatch",
                    ),
                ),
                (
                    "page",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cleaned_texts",
                        to="documents.page",
                    ),
                ),
            ],
            options={
                "verbose_name": "Trang đã làm sạch",
                "verbose_name_plural": "Trang đã làm sạch",
                "ordering": ["page__page_number"],
            },
        ),
        migrations.AddConstraint(
            model_name="pagecleanedtext",
            constraint=models.UniqueConstraint(fields=("batch", "page"), name="uniq_cleaning_batch_page"),
        ),
    ]
