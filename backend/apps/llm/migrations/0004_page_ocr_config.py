# Generated manually

import uuid

import django.db.models.deletion
from django.db import migrations, models


def migrate_box_config_to_page_config(apps, schema_editor):
    BoxOCRConfig = apps.get_model("llm", "BoxOCRConfig")
    PageOCRConfig = apps.get_model("llm", "PageOCRConfig")
    OCRProvider = apps.get_model("llm", "OCRProvider")

    fallback = OCRProvider.objects.filter(is_default=True).first()
    if not fallback:
        fallback = OCRProvider.objects.filter(is_active=True).order_by("id").first()

    vision_cfg = BoxOCRConfig.objects.filter(content_type="text").select_related("provider").first()
    vlm_cfg = (
        BoxOCRConfig.objects.filter(content_type="table").select_related("provider").first()
        or BoxOCRConfig.objects.filter(content_type="formula").select_related("provider").first()
    )

    standard_provider = vision_cfg.provider if vision_cfg else fallback
    layout_provider = vlm_cfg.provider if vlm_cfg else fallback

    if standard_provider:
        PageOCRConfig.objects.update_or_create(
            page_mode="standard",
            defaults={"provider": standard_provider},
        )
    if layout_provider:
        PageOCRConfig.objects.update_or_create(
            page_mode="layout",
            defaults={"provider": layout_provider},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0003_ocrprovider_api_key"),
    ]

    operations = [
        migrations.CreateModel(
            name="PageOCRConfig",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "page_mode",
                    models.CharField(
                        choices=[
                            ("standard", "Trang thường (không bảng/công thức)"),
                            ("layout", "Trang có bảng hoặc công thức"),
                        ],
                        max_length=16,
                        unique=True,
                        verbose_name="Loại trang",
                    ),
                ),
                ("notes", models.CharField(blank=True, max_length=255, verbose_name="Ghi chú")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "provider",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="page_configs",
                        to="llm.ocrprovider",
                        verbose_name="Công cụ OCR",
                    ),
                ),
            ],
            options={
                "verbose_name": "OCR theo loại trang",
                "verbose_name_plural": "1. OCR theo loại trang",
                "ordering": ["page_mode"],
            },
        ),
        migrations.RunPython(migrate_box_config_to_page_config, migrations.RunPython.noop),
        migrations.DeleteModel(
            name="BoxOCRConfig",
        ),
        migrations.AlterField(
            model_name="ocrprovider",
            name="is_default",
            field=models.BooleanField(
                default=False,
                help_text="Công cụ dự phòng khi cấu hình trang trỏ tới provider không kích hoạt.",
                verbose_name="Mặc định",
            ),
        ),
    ]
