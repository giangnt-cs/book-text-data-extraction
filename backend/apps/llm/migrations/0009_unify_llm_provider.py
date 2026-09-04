import django.db.models.deletion
from django.db import migrations, models


def seed_cleaning_config(apps, schema_editor):
    LLM = apps.get_model("llm", "LLMProvider")
    Prompt = apps.get_model("llm", "TextCleaningPrompt")
    Config = apps.get_model("llm", "TextCleaningConfig")

    default = LLM.objects.filter(is_active=True, is_default=True).first()
    if default is None:
        default = LLM.objects.filter(is_active=True).order_by("id").first()

    prompt_row = Prompt.objects.filter(pk=1).first()
    prompt = (prompt_row.prompt if prompt_row else "") or "Làm sạch văn bản OCR."

    Config.objects.get_or_create(
        pk=1,
        defaults={
            "provider": default,
            "model": "",
            "prompt": prompt,
        },
    )

    mapping = {
        "Gemini (làm sạch văn bản)": "Gemini",
        "ChatGPT (làm sạch văn bản)": "ChatGPT",
        "Ollama (làm sạch văn bản)": "Ollama",
        "DeepSeek (làm sạch văn bản)": "DeepSeek",
    }
    for old, new in mapping.items():
        if not LLM.objects.filter(name=new).exists():
            LLM.objects.filter(name=old).update(name=new)

    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    ct = ContentType.objects.filter(app_label="llm", model="llmprovider").first()
    if ct is None:
        return
    perm_map = {
        "add_textcleaningprovider": "add_llmprovider",
        "change_textcleaningprovider": "change_llmprovider",
        "delete_textcleaningprovider": "delete_llmprovider",
        "view_textcleaningprovider": "view_llmprovider",
    }
    for old, new in perm_map.items():
        Permission.objects.filter(content_type=ct, codename=old).update(codename=new)


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0008_book_classify_config"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="TextCleaningProvider",
            new_name="LLMProvider",
        ),
        migrations.AlterModelOptions(
            name="llmprovider",
            options={"ordering": ["name"], "verbose_name": "LLM", "verbose_name_plural": "3. LLM"},
        ),
        migrations.AddField(
            model_name="ocrprovider",
            name="llm",
            field=models.ForeignKey(
                blank=True,
                help_text="Với Gemini / ChatGPT / Ollama / DeepSeek: chọn LLM đã khai báo để dùng chung API key.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ocr_providers",
                to="llm.llmprovider",
                verbose_name="LLM",
            ),
        ),
        migrations.AddField(
            model_name="bookclassifyconfig",
            name="model",
            field=models.CharField(
                blank=True,
                help_text="Để trống = model mặc định của LLM.",
                max_length=200,
                verbose_name="Model",
            ),
        ),
        migrations.AlterField(
            model_name="bookclassifyconfig",
            name="provider",
            field=models.ForeignKey(
                blank=True,
                help_text="Chọn LLM đã khai báo. Để trống = LLM mặc định.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="classify_configs",
                to="llm.llmprovider",
                verbose_name="LLM",
            ),
        ),
        migrations.AlterModelOptions(
            name="bookclassifyconfig",
            options={"verbose_name": "Phân loại sách", "verbose_name_plural": "5. Phân loại sách"},
        ),
        migrations.CreateModel(
            name="TextCleaningConfig",
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
                ("prompt", models.TextField(verbose_name="Prompt làm sạch")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "provider",
                    models.ForeignKey(
                        blank=True,
                        help_text="Chọn LLM đã khai báo. Để trống = LLM mặc định.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="cleaning_configs",
                        to="llm.llmprovider",
                        verbose_name="LLM",
                    ),
                ),
            ],
            options={
                "verbose_name": "Làm sạch văn bản",
                "verbose_name_plural": "4. Làm sạch văn bản",
            },
        ),
        migrations.RunPython(seed_cleaning_config, migrations.RunPython.noop),
    ]
