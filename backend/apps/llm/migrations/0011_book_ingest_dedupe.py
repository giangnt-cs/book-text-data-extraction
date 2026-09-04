import django.db.models.deletion
from django.db import migrations, models


def seed_configs(apps, schema_editor):
    LLM = apps.get_model("llm", "LLMProvider")
    Ingest = apps.get_model("llm", "BookIngestConfig")
    Dedupe = apps.get_model("llm", "BookDedupeConfig")
    default = LLM.objects.filter(is_active=True, is_default=True).first()
    if default is None:
        default = LLM.objects.filter(is_active=True).order_by("id").first()
    Ingest.objects.get_or_create(
        pk=1,
        defaults={
            "provider": default,
            "model": "",
            "prompt": (
                "Bạn nhận văn bản OCR trang đầu của một cuốn sách.\n"
                "Rút trích tên sách và đối chiếu danh sách đã có.\n"
                'Trả JSON: {"title": "", "duplicate": false, "matched_title": ""}'
            ),
        },
    )
    Dedupe.objects.get_or_create(
        pk=1,
        defaults={
            "provider": default,
            "model": "",
            "batch_size": 30,
            "prompt": (
                "Tìm các nhóm tên sách cùng một cuốn trong danh sách.\n"
                'Trả JSON: {"groups": [{"canonical": "", "titles": ["", ""]}]}'
            ),
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0010_chunking_config"),
    ]

    operations = [
        migrations.CreateModel(
            name="BookIngestConfig",
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
                ("prompt", models.TextField(verbose_name="Prompt tạo sách từ upload")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "provider",
                    models.ForeignKey(
                        blank=True,
                        help_text="Chọn LLM đã khai báo. Để trống = LLM mặc định.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="ingest_configs",
                        to="llm.llmprovider",
                        verbose_name="LLM",
                    ),
                ),
            ],
            options={
                "verbose_name": "Tạo sách từ upload",
                "verbose_name_plural": "7. Tạo sách từ upload",
            },
        ),
        migrations.CreateModel(
            name="BookDedupeConfig",
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
                    "batch_size",
                    models.PositiveSmallIntegerField(
                        default=30,
                        help_text="Mỗi lần gửi tối đa số tên này cho LLM.",
                        verbose_name="Số tên mỗi lần gọi",
                    ),
                ),
                ("prompt", models.TextField(verbose_name="Prompt kiểm tra trùng tên")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "provider",
                    models.ForeignKey(
                        blank=True,
                        help_text="Chọn LLM đã khai báo. Để trống = LLM mặc định.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dedupe_configs",
                        to="llm.llmprovider",
                        verbose_name="LLM",
                    ),
                ),
            ],
            options={
                "verbose_name": "Kiểm tra sách trùng",
                "verbose_name_plural": "8. Kiểm tra sách trùng",
            },
        ),
        migrations.RunPython(seed_configs, migrations.RunPython.noop),
    ]
