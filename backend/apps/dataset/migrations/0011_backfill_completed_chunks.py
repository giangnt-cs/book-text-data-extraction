from django.db import migrations


def backfill_completed_chunks(apps, schema_editor):
    """Tạo SourceChunk cho văn bản đã phân đoạn xong nhưng chưa có khối."""
    from apps.dataset.markdown import rebuild_document_chunks
    from apps.dataset.models import ChunkingBatchStatus, SourceDocument

    done_ids = (
        SourceDocument.objects.filter(chunking_batches__status=ChunkingBatchStatus.DONE)
        .values_list("pk", flat=True)
        .distinct()
    )
    for document in SourceDocument.objects.filter(pk__in=done_ids).iterator():
        if document.chunks.exists():
            continue
        rebuild_document_chunks(document)


class Migration(migrations.Migration):

    dependencies = [
        ("dataset", "0010_chunk_token_count"),
    ]

    operations = [
        migrations.RunPython(backfill_completed_chunks, migrations.RunPython.noop),
    ]
