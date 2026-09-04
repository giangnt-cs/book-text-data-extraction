import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0007_ocrprovider_fallback"),
    ]

    operations = [
        migrations.CreateModel(
            name="BookClassifyConfig",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                (
                    "max_pages",
                    models.PositiveSmallIntegerField(
                        default=20,
                        help_text="Đọc tối đa số trang này (OCR/làm sạch, hoặc chữ trong PDF nếu chưa OCR).",
                        verbose_name="Số trang đầu đọc",
                    ),
                ),
                ("prompt", models.TextField(verbose_name="Prompt phân loại")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "provider",
                    models.ForeignKey(
                        blank=True,
                        help_text="Chọn một cấu hình trong «LLM làm sạch văn bản». Để trống = LLM mặc định.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="classify_configs",
                        to="llm.textcleaningprovider",
                        verbose_name="LLM phân loại",
                    ),
                ),
            ],
            options={
                "verbose_name": "Phân loại sách (LLM)",
                "verbose_name_plural": "4. Phân loại sách (LLM)",
            },
        ),
    ]
