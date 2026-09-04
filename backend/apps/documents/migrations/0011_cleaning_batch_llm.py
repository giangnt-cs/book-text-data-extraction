import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0010_book_publication_meta"),
        ("llm", "0009_unify_llm_provider"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookcleaningbatch",
            name="model",
            field=models.CharField(blank=True, max_length=200, verbose_name="Model đã dùng"),
        ),
        migrations.AlterField(
            model_name="bookcleaningbatch",
            name="provider",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cleaning_batches",
                to="llm.llmprovider",
            ),
        ),
    ]
