from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0012_llm_temperature_thinking"),
    ]

    operations = [
        migrations.AddField(
            model_name="chunkingconfig",
            name="window_pages",
            field=models.PositiveSmallIntegerField(
                default=2,
                help_text="Mỗi lần gọi LLM gửi bấy nhiêu trang liên tiếp (1–20). Mặc định 2.",
                verbose_name="Số trang mỗi lần gửi",
            ),
        ),
        migrations.AddField(
            model_name="chunkingconfig",
            name="overlap_pages",
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text=(
                    "Số trang cuối của lần trước được gửi lại làm ngữ cảnh. "
                    "Phải nhỏ hơn số trang mỗi lần. 0 = không trùng. Mặc định 1."
                ),
                verbose_name="Số trang trùng giữa hai lần",
            ),
        ),
    ]
