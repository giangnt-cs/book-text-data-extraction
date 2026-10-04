import django.db.models.deletion
from django.db import migrations, models


def seed_image_describe_config(apps, schema_editor):
    OCRProvider = apps.get_model("llm", "OCRProvider")
    ImageDescribeConfig = apps.get_model("llm", "ImageDescribeConfig")
    from ocr_llm.prompts import default_image_describe_page_prompt, default_image_describe_prompt

    vlm_backends = {
        "vlm_gemini",
        "vlm_openai",
        "vlm_ollama",
        "vlm_vllm",
        "vlm_deepseek",
        "ollama",
    }
    provider = (
        OCRProvider.objects.filter(is_active=True, backend__in=vlm_backends).order_by("name").first()
        or OCRProvider.objects.filter(backend="vlm_gemini").first()
        or OCRProvider.objects.filter(backend__in=vlm_backends).order_by("name").first()
    )
    ImageDescribeConfig.objects.get_or_create(
        pk=1,
        defaults={
            "provider_id": getattr(provider, "pk", None),
            "model": "",
            "prompt": default_image_describe_prompt(),
            "page_prompt": default_image_describe_page_prompt(),
            "min_description_chars": 40,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0015_chunk_resize_config"),
    ]

    operations = [
        migrations.CreateModel(
            name="ImageDescribeConfig",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                (
                    "model",
                    models.CharField(
                        blank=True,
                        help_text="Để trống = model mặc định của VLM đã chọn.",
                        max_length=200,
                        verbose_name="Model",
                    ),
                ),
                (
                    "prompt",
                    models.TextField(
                        help_text="Lần 1: chỉ gửi chữ đọc được trên hình (không gửi ảnh).",
                        verbose_name="Prompt mô tả từ chữ trên hình",
                    ),
                ),
                (
                    "page_prompt",
                    models.TextField(
                        help_text=(
                            "Lần 2 khi lần 1 không đủ thông tin: gửi ảnh crop, chữ trên hình và nội dung trang sách. "
                            "Prompt cần nói rõ đây là hình minh họa rút trích từ sách y học cổ truyền Việt Nam."
                        ),
                        verbose_name="Prompt mô tả kèm nội dung trang",
                    ),
                ),
                (
                    "min_description_chars",
                    models.PositiveIntegerField(
                        default=40,
                        help_text=(
                            "Mô tả ngắn hơn mức này (hoặc VLM trả [KHÔNG_ĐỦ_THÔNG_TIN]) "
                            "sẽ gọi lần 2 với nội dung trang."
                        ),
                        verbose_name="Độ dài tối thiểu của mô tả",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "provider",
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            "Chọn công cụ VLM đã khai báo (Gemini / Ollama / vLLM / ChatGPT). "
                            "Để trống = VLM đang kích hoạt."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="image_describe_configs",
                        to="llm.ocrprovider",
                        verbose_name="VLM",
                    ),
                ),
            ],
            options={
                "verbose_name": "Mô tả hình minh họa",
                "verbose_name_plural": "6c. Mô tả hình minh họa",
            },
        ),
        migrations.RunPython(seed_image_describe_config, migrations.RunPython.noop),
    ]
