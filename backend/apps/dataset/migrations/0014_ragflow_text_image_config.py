from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("dataset", "0013_image_source_and_chunks"),
    ]

    operations = [
        migrations.CreateModel(
            name="RAGFlowTextConfig",
            fields=[],
            options={
                "verbose_name": "Chunk văn bản",
                "verbose_name_plural": "Chunk văn bản",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("dataset.ragflowconfig",),
        ),
        migrations.CreateModel(
            name="RAGFlowImageConfig",
            fields=[],
            options={
                "verbose_name": "Chunk hình",
                "verbose_name_plural": "Chunk hình",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("dataset.ragflowconfig",),
        ),
    ]
