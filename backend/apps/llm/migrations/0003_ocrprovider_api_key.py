from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0002_box_ocr_config_and_backends"),
    ]

    operations = [
        migrations.AddField(
            model_name="ocrprovider",
            name="api_key",
            field=models.CharField(
                blank=True,
                max_length=500,
                verbose_name="API Key",
                help_text="API Key hoặc đường dẫn file credentials (Google Vision).",
            ),
        ),
        migrations.AlterField(
            model_name="ocrprovider",
            name="api_key_env",
            field=models.CharField(
                blank=True,
                max_length=120,
                verbose_name="Biến môi trường API key (legacy)",
                help_text="Chỉ dùng khi chưa điền API Key trực tiếp.",
            ),
        ),
        migrations.AlterField(
            model_name="ocrprovider",
            name="base_url",
            field=models.CharField(
                blank=True,
                max_length=400,
                verbose_name="Base URL",
                help_text="URL gốc API (Ollama, OpenAI-compatible, MinerU, …).",
            ),
        ),
        migrations.AlterField(
            model_name="ocrprovider",
            name="model",
            field=models.CharField(
                blank=True,
                max_length=200,
                verbose_name="Model",
                help_text="Chọn sau khi bấm «Tải danh sách model».",
            ),
        ),
    ]
