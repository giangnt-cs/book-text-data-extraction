from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0011_book_ingest_dedupe"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmprovider",
            name="temperature",
            field=models.FloatField(
                default=0.0,
                help_text="0 = ổn định (OCR, rút tên, làm sạch). Tăng dần nếu muốn đa dạng hơn. Chỉ Ollama và vLLM.",
                verbose_name="Temperature",
            ),
        ),
        migrations.AddField(
            model_name="llmprovider",
            name="enable_thinking",
            field=models.BooleanField(
                default=False,
                help_text="Chuỗi suy nghĩ (Ollama think / Qwen trên vLLM). Tắt thì nhanh hơn.",
                verbose_name="Bật thinking",
            ),
        ),
    ]
