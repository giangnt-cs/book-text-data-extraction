from django.db import migrations

_OLD_PROMPT = """
Bạn đang xem một hình minh họa trong sách. Dưới đây là chữ đọc được trên hình (nếu có).

Nhiệm vụ: viết mô tả ngắn, rõ, bằng tiếng Việt về nội dung hình — dựa vào những gì nhìn thấy trên ảnh và chữ trên hình.

Quy tắc:
- Chỉ mô tả hình; không chép lại nguyên văn chữ trên hình (chữ đã có mục riêng).
- Không bịa chi tiết không nhìn thấy.
- Nếu không đủ thông tin để mô tả (hình mờ, chỉ có chữ rời, không rõ ngữ cảnh), trả đúng một dòng: [KHÔNG_ĐỦ_THÔNG_TIN]
- Không markdown heading, không lời dẫn kiểu "Đây là ảnh…".
""".strip()

_OLD_PAGE_PROMPT = """
Đây là hình minh họa cho nội dung trang sách được gửi kèm. Hãy mô tả hình sao cho phù hợp với ngữ cảnh trang (nhân vật, sự kiện, đồ vật, cảnh… mà hình đang minh họa).

Quy tắc:
- Mô tả bằng tiếng Việt, ngắn và rõ.
- Kết hợp những gì nhìn thấy trên hình, chữ trên hình (nếu có) và nội dung trang sách.
- Không bịa chi tiết không có trên hình và không suy ra được từ trang.
- Không markdown heading, không lời dẫn kiểu "Đây là ảnh…".
- Chỉ trả về đoạn mô tả hình.
""".strip()


def update_default_prompts(apps, schema_editor):
    ImageDescribeConfig = apps.get_model("llm", "ImageDescribeConfig")
    from ocr_llm.prompts import default_image_describe_page_prompt, default_image_describe_prompt

    obj = ImageDescribeConfig.objects.filter(pk=1).first()
    if obj is None:
        return
    fields: list[str] = []
    if (obj.prompt or "").strip() == _OLD_PROMPT:
        obj.prompt = default_image_describe_prompt()
        fields.append("prompt")
    if (obj.page_prompt or "").strip() == _OLD_PAGE_PROMPT:
        obj.page_prompt = default_image_describe_page_prompt()
        fields.append("page_prompt")
    if fields:
        obj.save(update_fields=fields)


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0016_image_describe_config"),
    ]

    operations = [
        migrations.RunPython(update_default_prompts, migrations.RunPython.noop),
    ]
