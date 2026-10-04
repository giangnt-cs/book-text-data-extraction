from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("dataset", "0011_backfill_completed_chunks"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="ragflowconfig",
            name="embed_prefix",
        ),
    ]
