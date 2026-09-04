# Generated manually

import uuid

from django.db import migrations, models

import ocr_llm.prompts


def seed_cleaning_prompt(apps, schema_editor):
    TextCleaningPrompt = apps.get_model("llm", "TextCleaningPrompt")
    TextCleaningPrompt.objects.update_or_create(
        pk=1,
        defaults={"prompt": ocr_llm.prompts.default_text_cleaning_prompt()},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0004_page_ocr_config"),
    ]

    operations = [
        migrations.CreateModel(
            name="TextCleaningProvider",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "name",
                    models.CharField(max_length=120, unique=True, verbose_name="Tên cấu hình"),
                ),
                (
                    "backend",
                    models.CharField(
                        choices=[
                            ("llm_gemini", "Gemini"),
                            ("llm_openai", "ChatGPT"),
                            ("llm_ollama", "Ollama"),
                            ("llm_deepseek", "DeepSeek"),
                        ],
                        max_length=32,
                        verbose_name="Loại LLM",
                    ),
                ),
                ("model", models.CharField(blank=True, max_length=200, verbose_name="Model")),
                ("base_url", models.CharField(blank=True, max_length=400, verbose_name="Base URL")),
                ("api_key", models.CharField(blank=True, max_length=500, verbose_name="API Key")),
                ("api_key_env", models.CharField(blank=True, max_length=120, verbose_name="Biến môi trường API key")),
                ("params", models.JSONField(blank=True, default=dict, verbose_name="Tham số bổ sung")),
                ("is_active", models.BooleanField(default=True, verbose_name="Kích hoạt")),
                ("is_default", models.BooleanField(default=False, verbose_name="Mặc định")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "LLM làm sạch văn bản",
                "verbose_name_plural": "3. LLM làm sạch văn bản",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="TextCleaningPrompt",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("prompt", models.TextField(verbose_name="Prompt làm sạch")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Prompt làm sạch văn bản",
                "verbose_name_plural": "Prompt làm sạch văn bản",
            },
        ),
        migrations.RunPython(seed_cleaning_prompt, migrations.RunPython.noop),
    ]
