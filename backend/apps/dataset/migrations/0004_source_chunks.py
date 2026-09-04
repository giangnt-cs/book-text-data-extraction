import uuid

import django.db.models.deletion
from django.db import migrations, models


def backfill_source_chunks(apps, schema_editor):
    from apps.dataset.markdown import rebuild_document_chunks
    from apps.dataset.models import ChunkingBatchStatus, SourceDocument

    done_ids = (
        SourceDocument.objects.filter(chunking_batches__status=ChunkingBatchStatus.DONE)
        .values_list("pk", flat=True)
        .distinct()
    )
    for document in SourceDocument.objects.filter(pk__in=done_ids).iterator():
        rebuild_document_chunks(document)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("dataset", "0003_source_catalog_code"),
    ]

    operations = [
        migrations.CreateModel(
            name="SourceChunk",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("position", models.PositiveIntegerField(verbose_name="Thứ tự")),
                ("text_md", models.TextField(blank=True, verbose_name="Nội dung")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunks",
                        to="dataset.sourcedocument",
                        verbose_name="Văn bản",
                    ),
                ),
            ],
            options={
                "verbose_name": "Khối phân đoạn",
                "verbose_name_plural": "Khối phân đoạn",
                "ordering": ["position"],
            },
        ),
        migrations.AddConstraint(
            model_name="sourcechunk",
            constraint=models.UniqueConstraint(fields=("document", "position"), name="uniq_dataset_chunk_position"),
        ),
        migrations.CreateModel(
            name="ChunkedDocument",
            fields=[],
            options={
                "verbose_name": "Phân đoạn",
                "verbose_name_plural": "Phân đoạn",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("dataset.sourcedocument",),
        ),
        migrations.RunPython(backfill_source_chunks, noop_reverse),
    ]
