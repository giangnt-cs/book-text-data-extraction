from django.db import migrations, models
from django.db.models import Q


def backfill_chunk_codes(apps, schema_editor):
    from apps.dataset.catalog import format_chunk_code
    from apps.dataset.markdown import annotate_chunk_pages
    from apps.dataset.models import SourceChunk, SourceDocument

    for document in SourceDocument.objects.iterator():
        rows = list(SourceChunk.objects.filter(document=document).order_by("position", "created_at"))
        if not rows:
            continue
        annotated = annotate_chunk_pages([chunk.text_md or "" for chunk in rows])
        catalog = (document.catalog_code or "").strip()
        for chunk, (_text, page, seq) in zip(rows, annotated):
            chunk.page_number = page
            chunk.page_seq = seq
            chunk.chunk_code = format_chunk_code(catalog, page, seq)
        SourceChunk.objects.bulk_update(rows, ["page_number", "page_seq", "chunk_code"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("dataset", "0004_source_chunks"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcechunk",
            name="page_number",
            field=models.PositiveIntegerField(default=1, verbose_name="Trang"),
        ),
        migrations.AddField(
            model_name="sourcechunk",
            name="page_seq",
            field=models.PositiveIntegerField(default=1, verbose_name="Thứ tự trong trang"),
        ),
        migrations.AddField(
            model_name="sourcechunk",
            name="chunk_code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Dạng DSG_fff_ccc_ppp: mã văn bản, trang, thứ tự trong trang.",
                max_length=64,
                verbose_name="Mã phân đoạn",
            ),
        ),
        migrations.RunPython(backfill_chunk_codes, noop_reverse),
        migrations.AddConstraint(
            model_name="sourcechunk",
            constraint=models.UniqueConstraint(
                condition=~Q(chunk_code=""),
                fields=("chunk_code",),
                name="uniq_dataset_chunk_code",
            ),
        ),
    ]
