from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dataset", "0007_source_publication_meta"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcechunk",
            name="content_type",
            field=models.CharField(
                choices=[("text", "Chữ"), ("image", "Hình")],
                db_index=True,
                default="text",
                help_text="Chữ (text) hoặc hình (image). Phân đoạn tách từ văn bản là chữ.",
                max_length=16,
                verbose_name="Loại",
            ),
        ),
    ]
