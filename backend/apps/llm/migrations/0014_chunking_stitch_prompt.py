from django.db import migrations, models


def _fill_stitch_prompt(apps, schema_editor):
    ChunkingConfig = apps.get_model("llm", "ChunkingConfig")
    default = (
        "Hai đoạn văn nằm ở ranh giới hai trang sách.\n"
        "Chỉ trả JSON: true hoặc false.\n"
        "true = hai đoạn là một câu/đoạn đang viết tiếp, cần nối.\n"
        "false = đoạn sau là nội dung mới, không nối.\n"
        "Không giải thích, không markdown, không object."
    )
    ChunkingConfig.objects.filter(stitch_prompt="").update(stitch_prompt=default)


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0013_chunking_window"),
    ]

    operations = [
        migrations.AddField(
            model_name="chunkingconfig",
            name="stitch_prompt",
            field=models.TextField(
                blank=True,
                help_text="LLM chỉ trả true hoặc false — có nối đoạn cuối trang trước với đoạn đầu trang sau.",
                verbose_name="Prompt nối trang",
            ),
        ),
        migrations.RunPython(_fill_stitch_prompt, migrations.RunPython.noop),
    ]
