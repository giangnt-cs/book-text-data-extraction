from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="page",
            name="has_formula",
            field=models.BooleanField(default=False, verbose_name="Có công thức"),
        ),
        migrations.AddField(
            model_name="page",
            name="has_table",
            field=models.BooleanField(default=False, verbose_name="Có bảng"),
        ),
        migrations.AddField(
            model_name="page",
            name="ocr_mode",
            field=models.CharField(
                choices=[("standard", "Enterprise OCR"), ("layout", "Layout Parser")],
                default="standard",
                max_length=16,
                verbose_name="Chế độ OCR",
            ),
        ),
        migrations.AddField(
            model_name="page",
            name="text_md",
            field=models.TextField(blank=True, verbose_name="Nội dung OCR trang"),
        ),
    ]
