import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dataset", "0015_ragflow_match_status"),
        ("llm", "0009_unify_llm_provider"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcechunkingbatch",
            name="method",
            field=models.CharField(default="llm", max_length=16, verbose_name="Phương pháp"),
        ),
        migrations.AlterField(
            model_name="sourcechunkingbatch",
            name="provider",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="dataset_chunking_batches",
                to="llm.llmprovider",
            ),
        ),
    ]
