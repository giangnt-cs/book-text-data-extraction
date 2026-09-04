# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0006_document_ai_ocr_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="ocrprovider",
            name="fallback_provider",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Khi công cụ này lỗi hoặc bị Gemini chặn (RECITATION/SAFETY), "
                    "chuyển sang công cụ này. Có thể xâu chuỗi, ví dụ Gemini → Ollama → Document AI."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="fallback_sources",
                to="llm.ocrprovider",
                verbose_name="Fallback",
            ),
        ),
    ]
