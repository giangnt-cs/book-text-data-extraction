from django.db import migrations, models


def backfill_token_count(apps, schema_editor):
    SourceChunk = apps.get_model("dataset", "SourceChunk")
    from apps.dataset.tokens import count_tokens

    batch = []
    for chunk in SourceChunk.objects.iterator(chunk_size=200):
        chunk.token_count = count_tokens(chunk.text_md)
        batch.append(chunk)
        if len(batch) >= 200:
            SourceChunk.objects.bulk_update(batch, ["token_count"])
            batch = []
    if batch:
        SourceChunk.objects.bulk_update(batch, ["token_count"])


class Migration(migrations.Migration):

    dependencies = [
        ("dataset", "0009_chunk_ragflow_image_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcechunk",
            name="token_count",
            field=models.PositiveIntegerField(
                db_index=True,
                default=0,
                verbose_name="Số token",
            ),
        ),
        migrations.RunPython(backfill_token_count, migrations.RunPython.noop),
    ]
