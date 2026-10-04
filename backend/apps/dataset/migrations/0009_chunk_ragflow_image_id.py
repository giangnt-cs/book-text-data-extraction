from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dataset", "0008_chunk_content_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcechunk",
            name="ragflow_image_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                max_length=128,
                verbose_name="RAGFlow image ID",
            ),
        ),
    ]
