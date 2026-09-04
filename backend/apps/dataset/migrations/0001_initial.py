import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("documents", "0015_chunking_window"),
        ("llm", "0013_chunking_window"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SourceDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=500, verbose_name="Tên văn bản")),
                (
                    "source_type",
                    models.CharField(
                        choices=[("book", "Sách đã duyệt"), ("upload", "Tải lên")],
                        default="upload",
                        max_length=16,
                        verbose_name="Nguồn",
                    ),
                ),
                ("markdown_file", models.FileField(blank=True, upload_to="dataset/sources/", verbose_name="File Markdown")),
                ("original_name", models.CharField(blank=True, max_length=255, verbose_name="Tên file gốc")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "book",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dataset_source",
                        to="documents.book",
                        verbose_name="Sách",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dataset_sources",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Văn bản nguồn",
                "verbose_name_plural": "Văn bản nguồn",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="SourceSegment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("page_number", models.PositiveIntegerField(verbose_name="Thứ tự")),
                ("text_md", models.TextField(blank=True, verbose_name="Nội dung")),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="segments",
                        to="dataset.sourcedocument",
                    ),
                ),
            ],
            options={
                "verbose_name": "Trang nguồn",
                "verbose_name_plural": "Trang nguồn",
                "ordering": ["page_number"],
            },
        ),
        migrations.CreateModel(
            name="SourceChunkingBatch",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("model", models.CharField(blank=True, max_length=200, verbose_name="Model đã dùng")),
                ("window_pages", models.PositiveSmallIntegerField(default=2, verbose_name="Số trang mỗi lần gửi")),
                ("overlap_pages", models.PositiveSmallIntegerField(default=1, verbose_name="Số trang trùng giữa hai lần")),
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
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dataset_chunking_batches",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunking_batches",
                        to="dataset.sourcedocument",
                    ),
                ),
                (
                    "provider",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="dataset_chunking_batches",
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
            name="SourceChunkedText",
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
                        to="dataset.sourcechunkingbatch",
                    ),
                ),
                (
                    "segment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunked_texts",
                        to="dataset.sourcesegment",
                    ),
                ),
            ],
            options={
                "verbose_name": "Trang đã phân đoạn",
                "verbose_name_plural": "Trang đã phân đoạn",
            },
        ),
        migrations.AddConstraint(
            model_name="sourcesegment",
            constraint=models.UniqueConstraint(fields=("document", "page_number"), name="uniq_dataset_segment_page"),
        ),
        migrations.AddConstraint(
            model_name="sourcechunkedtext",
            constraint=models.UniqueConstraint(fields=("batch", "segment"), name="uniq_dataset_chunk_batch_segment"),
        ),
    ]
