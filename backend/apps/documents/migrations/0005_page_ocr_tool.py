# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0004_book_ocr_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="page",
            name="ocr_provider",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ocr_pages",
                to="llm.ocrprovider",
                verbose_name="Công cụ OCR đã dùng",
            ),
        ),
        migrations.AddField(
            model_name="page",
            name="ocr_tool_name",
            field=models.CharField(blank=True, max_length=160, verbose_name="Tên công cụ OCR"),
        ),
        migrations.AddField(
            model_name="page",
            name="ocr_tool_backend",
            field=models.CharField(blank=True, max_length=40, verbose_name="Loại công cụ OCR"),
        ),
        migrations.AddField(
            model_name="page",
            name="ocr_fallback_from",
            field=models.CharField(
                blank=True,
                help_text="Công cụ được chọn ban đầu nếu trang này dùng fallback.",
                max_length=160,
                verbose_name="Fallback từ",
            ),
        ),
    ]
