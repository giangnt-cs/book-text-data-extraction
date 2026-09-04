import django.db.models.deletion
from django.db import migrations, models


def seed_chunking_config(apps, schema_editor):
    LLM = apps.get_model("llm", "LLMProvider")
    Config = apps.get_model("llm", "ChunkingConfig")
    default = LLM.objects.filter(is_active=True, is_default=True).first()
    if default is None:
        default = LLM.objects.filter(is_active=True).order_by("id").first()
    Config.objects.get_or_create(
        pk=1,
        defaults={
            "provider": default,
            "model": "",
            "prompt": (
                "Dưới đây là nội dung của một trang sách y học cổ truyền / dược liệu Việt Nam.\n\n"
                "Nếu trong nội dung này có nhiều chỉ mục tách biệt nhau hoàn toàn "
                "(ví dụ nhiều bài thuốc, nhiều vị thuốc, nhiều mục từ điển độc lập) "
                "thì hãy chèn đúng chuỗi ký tự </break> vào giữa hai nội dung tách biệt nhau. "
                "Nếu không có thì không cần làm gì.\n\n"
                "Cân nhắc kỹ xem các phần có liên quan / tiếp nối nhau hay không. "
                "Chỉ tách khi hai phần độc lập, không phải đoạn đang viết tiếp của cùng một mục.\n\n"
                "Quy tắc:\n"
                "- Không sửa, không tóm tắt, không dịch, không thêm chữ nào khác ngoài </break>.\n"
                "- Giữ nguyên toàn bộ nội dung, thứ tự, xuống dòng và Markdown.\n"
                "- Không bọc ```markdown, không giải thích, không lời dẫn.\n"
                "- Trả về toàn bộ trang (đã chèn </break> nếu cần)."
            ),
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0009_unify_llm_provider"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChunkingConfig",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                (
                    "model",
                    models.CharField(
                        blank=True,
                        help_text="Để trống = model mặc định của LLM.",
                        max_length=200,
                        verbose_name="Model",
                    ),
                ),
                ("prompt", models.TextField(verbose_name="Prompt phân đoạn")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "provider",
                    models.ForeignKey(
                        blank=True,
                        help_text="Chọn LLM đã khai báo. Để trống = LLM mặc định.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="chunking_configs",
                        to="llm.llmprovider",
                        verbose_name="LLM",
                    ),
                ),
            ],
            options={
                "verbose_name": "Phân đoạn",
                "verbose_name_plural": "6. Phân đoạn",
            },
        ),
        migrations.RunPython(seed_chunking_config, migrations.RunPython.noop),
    ]
