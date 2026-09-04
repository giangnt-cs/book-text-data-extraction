# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0008_page_verbose_name"),
    ]

    operations = [
        migrations.AlterField(
            model_name="book",
            name="status",
            field=models.CharField(
                choices=[
                    ("new", "Mới"),
                    ("processing", "Đang xử lý OCR"),
                    ("done", "Hoàn thành OCR"),
                    ("approved", "Đã duyệt"),
                    ("failed", "Lỗi"),
                ],
                default="new",
                max_length=16,
                verbose_name="Trạng thái",
            ),
        ),
    ]
