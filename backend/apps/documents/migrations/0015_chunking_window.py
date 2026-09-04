from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0014_staged_book_and_dedupe"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookchunkingbatch",
            name="window_pages",
            field=models.PositiveSmallIntegerField(
                default=2,
                verbose_name="Số trang mỗi lần gửi",
            ),
        ),
        migrations.AddField(
            model_name="bookchunkingbatch",
            name="overlap_pages",
            field=models.PositiveSmallIntegerField(
                default=1,
                verbose_name="Số trang trùng giữa hai lần",
            ),
        ),
    ]
