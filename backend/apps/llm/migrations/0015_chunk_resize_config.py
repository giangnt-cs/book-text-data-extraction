import django.db.models.deletion
from django.db import migrations, models


def seed_chunk_resize_config(apps, schema_editor):
    ChunkingConfig = apps.get_model("llm", "ChunkingConfig")
    ChunkResizeConfig = apps.get_model("llm", "ChunkResizeConfig")
    from ocr_llm.prompts import default_page_chunking_prompt

    chunking = ChunkingConfig.objects.filter(pk=1).first()
    prompt = ""
    provider_id = None
    model = ""
    if chunking is not None:
        prompt = (chunking.prompt or "").strip()
        provider_id = chunking.provider_id
        model = chunking.model or ""
    ChunkResizeConfig.objects.get_or_create(
        pk=1,
        defaults={
            "provider_id": provider_id,
            "model": model,
            "max_tokens": 5000,
            "prompt": prompt or default_page_chunking_prompt(),
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0014_chunking_stitch_prompt"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChunkResizeConfig",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                (
                    "model",
                    models.CharField(
                        blank=True,
                        help_text="Để trống = model mặc định của LLM.",
                        max_length=200,
                        verbose_name="Model",
                    ),
                ),
                (
                    "max_tokens",
                    models.PositiveIntegerField(
                        default=5000,
                        help_text="Đoạn dài hơn mức này sẽ được gọi phân đoạn lại.",
                        verbose_name="Giới hạn token mỗi đoạn",
                    ),
                ),
                ("prompt", models.TextField(verbose_name="Prompt giảm kích thước")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "provider",
                    models.ForeignKey(
                        blank=True,
                        help_text="Chọn LLM đã khai báo. Để trống = LLM mặc định.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="chunk_resize_configs",
                        to="llm.llmprovider",
                        verbose_name="LLM",
                    ),
                ),
            ],
            options={
                "verbose_name": "Giảm kích thước phân đoạn",
                "verbose_name_plural": "6b. Giảm kích thước phân đoạn",
            },
        ),
        migrations.RunPython(seed_chunk_resize_config, migrations.RunPython.noop),
    ]
