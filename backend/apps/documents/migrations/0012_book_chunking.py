import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0011_cleaning_batch_llm"),
        ("llm", "0010_chunking_config"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BookChunkingBatch",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("model", models.CharField(blank=True, max_length=200, verbose_name="Model đã dùng")),
                ("prompt", models.TextField(verbose_name="Prompt đã dùng")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Chờ xử lý"),
                            ("processing", "Đang phân đoạn"),
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
                        related_name="chunking_batches",
                        to="documents.book",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="chunking_batches",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "provider",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="chunking_batches",
                        to="llm.llmprovider",
                    ),
                ),
            ],
            options={
                "verbose_name": "Lần phân đoạn",
                "verbose_name_plural": "Lần phân đoạn",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="PageChunkedText",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("text_md", models.TextField(blank=True, verbose_name="Văn bản đã phân đoạn")),
                ("break_count", models.PositiveIntegerField(default=0, verbose_name="Số mốc </break>")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="page_texts",
                        to="documents.bookchunkingbatch",
                    ),
                ),
                (
                    "page",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunked_texts",
                        to="documents.page",
                    ),
                ),
            ],
            options={
                "verbose_name": "Trang đã phân đoạn",
                "verbose_name_plural": "Trang đã phân đoạn",
                "ordering": ["page__page_number"],
            },
        ),
        migrations.AddConstraint(
            model_name="pagechunkedtext",
            constraint=models.UniqueConstraint(fields=("batch", "page"), name="uniq_chunking_batch_page"),
        ),
    ]
