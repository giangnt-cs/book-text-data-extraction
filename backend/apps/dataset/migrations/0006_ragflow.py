from django.db import migrations, models


def seed_ragflow_config(apps, schema_editor):
    RAGFlowConfig = apps.get_model("dataset", "RAGFlowConfig")
    RAGFlowConfig.objects.get_or_create(
        pk=1,
        defaults={
            "base_url": "",
            "api_key": "",
            "dataset_id": "",
            "meta_timeout": 30,
            "chunk_timeout": 300,
            "embed_prefix": "Passage: ",
        },
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("dataset", "0005_chunk_code"),
    ]

    operations = [
        migrations.CreateModel(
            name="RAGFlowConfig",
            fields=[
                ("id", models.PositiveSmallIntegerField(primary_key=True, serialize=False, editable=False, default=1)),
                (
                    "base_url",
                    models.CharField(
                        blank=True,
                        help_text="Ví dụ http://host:80 — không kèm /api/v1.",
                        max_length=400,
                        verbose_name="Base URL",
                    ),
                ),
                ("api_key", models.CharField(blank=True, max_length=500, verbose_name="API Key")),
                (
                    "dataset_id",
                    models.CharField(
                        blank=True,
                        help_text="ID knowledge base trên RAGFlow.",
                        max_length=64,
                        verbose_name="Dataset ID",
                    ),
                ),
                (
                    "meta_timeout",
                    models.PositiveIntegerField(
                        default=30,
                        help_text="Tạo document rỗng và PATCH metadata.",
                        verbose_name="Timeout tạo document (giây)",
                    ),
                ),
                (
                    "chunk_timeout",
                    models.PositiveIntegerField(
                        default=300,
                        help_text="Mỗi chunk RAGFlow embed rồi ghi vector DB — có thể rất chậm.",
                        verbose_name="Timeout Add/PATCH chunk (giây)",
                    ),
                ),
                (
                    "embed_prefix",
                    models.CharField(
                        default="Passage: ",
                        help_text=(
                            "Gắn trước nội dung khi gửi RAGFlow (jina-embeddings-v4-text-retrieval). "
                            "Không lưu vào phân đoạn local."
                        ),
                        max_length=64,
                        verbose_name="Tiền tố embed",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "RAGFlow",
                "verbose_name_plural": "RAGFlow",
            },
        ),
        migrations.AddField(
            model_name="sourcedocument",
            name="ragflow_document_id",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="RAGFlow document ID"),
        ),
        migrations.AddField(
            model_name="sourcedocument",
            name="ragflow_status",
            field=models.CharField(
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
        migrations.AddField(
            model_name="sourcedocument",
            name="ragflow_error",
            field=models.TextField(blank=True, default="", verbose_name="Lỗi RAGFlow"),
        ),
        migrations.AddField(
            model_name="sourcedocument",
            name="ragflow_synced_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Nạp RAGFlow lúc"),
        ),
        migrations.AddField(
            model_name="sourcechunk",
            name="ragflow_chunk_id",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="RAGFlow chunk ID"),
        ),
        migrations.RunPython(seed_ragflow_config, noop_reverse),
    ]
