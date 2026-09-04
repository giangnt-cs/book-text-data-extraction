# Generated manually

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ocrprovider",
            name="backend",
            field=models.CharField(
                choices=[
                    ("google_vision", "Google Cloud Vision"),
                    ("google_document_ai", "Google Document AI"),
                    ("vlm_gemini", "Gemini (VLM)"),
                    ("vlm_openai", "ChatGPT (VLM)"),
                    ("vlm_ollama", "Ollama (VLM)"),
                    ("vlm_deepseek", "DeepSeek (VLM)"),
                    ("mineru", "MinerU"),
                    ("paddle_ocr", "PaddleOCR"),
                    ("ollama", "Ollama (vLM)"),
                ],
                max_length=32,
                verbose_name="Loại công cụ",
            ),
        ),
        migrations.AlterModelOptions(
            name="ocrprovider",
            options={
                "ordering": ["name"],
                "verbose_name": "Công cụ OCR",
                "verbose_name_plural": "2. Công cụ OCR",
            },
        ),
        migrations.AlterField(
            model_name="ocrprovider",
            name="base_url",
            field=models.CharField(blank=True, help_text="URL API (Ollama, DeepSeek, MinerU endpoint, …).", max_length=400, verbose_name="Base URL"),
        ),
        migrations.AlterField(
            model_name="ocrprovider",
            name="model",
            field=models.CharField(blank=True, help_text="Tên model (Gemini, GPT-4o, Ollama, DeepSeek, …).", max_length=200, verbose_name="Model"),
        ),
        migrations.AlterField(
            model_name="ocrprovider",
            name="api_key_env",
            field=models.CharField(blank=True, help_text="VD: GOOGLE_APPLICATION_CREDENTIALS, GEMINI_API_KEY, OPENAI_API_KEY.", max_length=120, verbose_name="Biến môi trường API key"),
        ),
        migrations.AlterField(
            model_name="ocrprovider",
            name="is_default",
            field=models.BooleanField(default=False, help_text="Công cụ dự phòng khi cấu hình box trỏ tới provider không kích hoạt.", verbose_name="Mặc định"),
        ),
        migrations.CreateModel(
            name="BoxOCRConfig",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "content_type",
                    models.CharField(
                        choices=[
                            ("text", "Text"),
                            ("table", "Bảng"),
                            ("formula", "Công thức"),
                            ("image", "Hình ảnh"),
                            ("columnar", "Cột"),
                            ("unknown", "Không xác định"),
                        ],
                        max_length=16,
                        unique=True,
                        verbose_name="Loại box",
                    ),
                ),
                ("notes", models.CharField(blank=True, max_length=255, verbose_name="Ghi chú")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "provider",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="box_configs",
                        to="llm.ocrprovider",
                        verbose_name="Công cụ OCR",
                    ),
                ),
            ],
            options={
                "verbose_name": "OCR theo loại box",
                "verbose_name_plural": "1. OCR theo loại box",
                "ordering": ["content_type"],
            },
        ),
    ]
