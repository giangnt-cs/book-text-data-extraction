from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0007_book_status_approved"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="page",
            options={
                "ordering": ["page_number"],
                "verbose_name": "Trang",
                "verbose_name_plural": "Các trang",
            },
        ),
    ]
