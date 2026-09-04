# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0005_text_cleaning"),
        ("documents", "0003_text_cleaning"),
    ]

    operations = [
        migrations.AddField(
            model_name="book",
            name="ocr_layout_provider",
            field=models.ForeignKey(
                blank=True,
                help_text="Để trống = dùng cấu hình hệ thống.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="books_layout",
                to="llm.ocrprovider",
                verbose_name="OCR trang bảng/công thức",
            ),
        ),
        migrations.AddField(
            model_name="book",
            name="ocr_standard_provider",
            field=models.ForeignKey(
                blank=True,
                help_text="Để trống = dùng cấu hình hệ thống.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="books_standard",
                to="llm.ocrprovider",
                verbose_name="OCR trang thường",
            ),
        ),
    ]
