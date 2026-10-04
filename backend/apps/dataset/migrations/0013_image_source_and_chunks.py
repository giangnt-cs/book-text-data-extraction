import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q

import apps.dataset.models


class Migration(migrations.Migration):

    dependencies = [
        ("dataset", "0012_remove_ragflow_embed_prefix"),
        ("documents", "0015_chunking_window"),
    ]

    operations = [
        migrations.AddField(
            model_name="ragflowconfig",
            name="image_dataset_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="ID knowledge base hình minh họa trên RAGFlow. Bắt buộc khi nạp phân đoạn hình.",
                max_length=64,
                verbose_name="Dataset ID hình",
            ),
        ),
        migrations.AlterField(
            model_name="ragflowconfig",
            name="dataset_id",
            field=models.CharField(
                blank=True,
                help_text="ID knowledge base chữ trên RAGFlow.",
                max_length=64,
                verbose_name="Dataset ID",
            ),
        ),
        migrations.CreateModel(
            name="ImageSourceDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=500, verbose_name="Tên văn bản")),
                ("author", models.CharField(blank=True, default="", max_length=400, verbose_name="Tên tác giả")),
                ("publisher", models.CharField(blank=True, default="", max_length=400, verbose_name="Nhà xuất bản")),
                ("published_year", models.CharField(blank=True, default="", max_length=32, verbose_name="Năm xuất bản")),
                (
                    "catalog_code",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Sao chép mã văn bản chữ cùng sách khi có; không thì tự gán DSG_fff.",
                        max_length=32,
                        verbose_name="Mã văn bản",
                    ),
                ),
                (
                    "extract_status",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("pending", "Chờ rút trích"),
                            ("processing", "Đang rút trích"),
                            ("done", "Đã rút trích"),
                            ("failed", "Lỗi rút trích"),
                        ],
                        default="",
                        max_length=16,
                        verbose_name="Trạng thái rút trích",
                    ),
                ),
                ("extract_error", models.TextField(blank=True, default="", verbose_name="Lỗi rút trích")),
                ("ragflow_document_id", models.CharField(blank=True, default="", max_length=64, verbose_name="RAGFlow document ID")),
                (
                    "ragflow_status",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("pending", "Chờ nạp RAG"),
                            ("processing", "Đang nạp RAG"),
                            ("done", "Đã nạp RAG"),
                            ("failed", "Lỗi nạp RAG"),
                        ],
                        default="",
                        max_length=16,
                        verbose_name="Trạng thái RAGFlow",
                    ),
                ),
                ("ragflow_error", models.TextField(blank=True, default="", verbose_name="Lỗi RAGFlow")),
                ("ragflow_synced_at", models.DateTimeField(blank=True, null=True, verbose_name="Nạp RAGFlow lúc")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "book",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dataset_image_source",
                        to="documents.book",
                        verbose_name="Sách",
                    ),
                ),
                (
                    "class_doc_type",
                    models.ForeignKey(
                        blank=True,
                        limit_choices_to={"group__slug": "doc_type"},
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="dataset_image_sources_doc_type",
                        to="documents.classificationterm",
                        verbose_name="Loại tài liệu",
                    ),
                ),
                (
                    "class_era",
                    models.ForeignKey(
                        blank=True,
                        limit_choices_to={"group__slug": "era"},
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="dataset_image_sources_era",
                        to="documents.classificationterm",
                        verbose_name="Thời đại",
                    ),
                ),
                (
                    "class_field",
                    models.ForeignKey(
                        blank=True,
                        limit_choices_to={"group__slug": "field"},
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="dataset_image_sources_field",
                        to="documents.classificationterm",
                        verbose_name="Lĩnh vực",
                    ),
                ),
                (
                    "class_language",
                    models.ForeignKey(
                        blank=True,
                        limit_choices_to={"group__slug": "language"},
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="dataset_image_sources_language",
                        to="documents.classificationterm",
                        verbose_name="Ngôn ngữ",
                    ),
                ),
                (
                    "class_source",
                    models.ForeignKey(
                        blank=True,
                        limit_choices_to={"group__slug": "source"},
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="dataset_image_sources_source",
                        to="documents.classificationterm",
                        verbose_name="Nguồn y học",
                    ),
                ),
            ],
            options={
                "verbose_name": "Văn bản hình",
                "verbose_name_plural": "Văn bản hình",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ImageChunk",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("image", models.FileField(blank=True, upload_to=apps.dataset.models.image_chunk_upload_to, verbose_name="Hình")),
                ("position", models.PositiveIntegerField(verbose_name="Thứ tự")),
                ("page_number", models.PositiveIntegerField(default=1, verbose_name="Trang")),
                ("page_seq", models.PositiveIntegerField(default=1, verbose_name="Thứ tự trong trang")),
                (
                    "chunk_code",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Dạng DSG_fff_ccc_ppp_img.",
                        max_length=64,
                        verbose_name="Mã phân đoạn",
                    ),
                ),
                ("text_md", models.TextField(blank=True, verbose_name="Nội dung")),
                ("token_count", models.PositiveIntegerField(db_index=True, default=0, verbose_name="Số token")),
                ("ragflow_chunk_id", models.CharField(blank=True, default="", max_length=64, verbose_name="RAGFlow chunk ID")),
                (
                    "ragflow_image_id",
                    models.CharField(blank=True, db_index=True, default="", max_length=128, verbose_name="RAGFlow image ID"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "box",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="image_chunk",
                        to="documents.box",
                        verbose_name="Bounding box",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunks",
                        to="dataset.imagesourcedocument",
                        verbose_name="Văn bản",
                    ),
                ),
            ],
            options={
                "verbose_name": "Phân đoạn hình",
                "verbose_name_plural": "Phân đoạn hình",
                "ordering": ["position"],
            },
        ),
        migrations.CreateModel(
            name="ImageChunkedDocument",
            fields=[],
            options={
                "verbose_name": "Phân đoạn",
                "verbose_name_plural": "Phân đoạn",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("dataset.imagesourcedocument",),
        ),
        migrations.AddConstraint(
            model_name="imagesourcedocument",
            constraint=models.UniqueConstraint(
                condition=~Q(catalog_code=""),
                fields=("catalog_code",),
                name="uniq_dataset_image_catalog_code",
            ),
        ),
        migrations.AddConstraint(
            model_name="imagechunk",
            constraint=models.UniqueConstraint(fields=("document", "position"), name="uniq_dataset_image_chunk_position"),
        ),
        migrations.AddConstraint(
            model_name="imagechunk",
            constraint=models.UniqueConstraint(
                condition=~Q(chunk_code=""),
                fields=("chunk_code",),
                name="uniq_dataset_image_chunk_code",
            ),
        ),
    ]
