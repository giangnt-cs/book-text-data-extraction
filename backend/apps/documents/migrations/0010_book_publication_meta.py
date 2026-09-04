from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0009_book_status_ocr_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="book",
            name="author",
            field=models.CharField(blank=True, max_length=400, verbose_name="Tên tác giả"),
        ),
        migrations.AddField(
            model_name="book",
            name="publisher",
            field=models.CharField(blank=True, max_length=400, verbose_name="Nhà xuất bản"),
        ),
        migrations.AddField(
            model_name="book",
            name="published_year",
            field=models.CharField(blank=True, max_length=32, verbose_name="Năm xuất bản"),
        ),
    ]
