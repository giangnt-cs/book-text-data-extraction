from django.db import migrations, models


def migrate_params_to_fields(apps, schema_editor):
    DetectorConfig = apps.get_model("layout", "DetectorConfig")
    for cfg in DetectorConfig.objects.all():
        params = cfg.params or {}
        changed = False
        if "imgsz" in params:
            cfg.imgsz = int(params["imgsz"])
            changed = True
        if "trim_scan" in params:
            cfg.trim_scan = bool(params["trim_scan"])
            changed = True
        if "use_cropbox" in params:
            cfg.use_cropbox = bool(params["use_cropbox"])
            changed = True
        if changed:
            cfg.save(update_fields=["imgsz", "trim_scan", "use_cropbox"])


class Migration(migrations.Migration):

    dependencies = [
        ("layout", "0002_detector_device_gpu_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="detectorconfig",
            name="imgsz",
            field=models.PositiveIntegerField(
                default=1024,
                verbose_name="Kích thước infer (imgsz)",
                help_text="Ảnh đầu vào YOLO (px). Khuyến nghị 1024.",
            ),
        ),
        migrations.AddField(
            model_name="detectorconfig",
            name="use_cropbox",
            field=models.BooleanField(
                default=True,
                verbose_name="Dùng CropBox PDF",
                help_text="Render theo CropBox của PDF (thường bật).",
            ),
        ),
        migrations.AddField(
            model_name="detectorconfig",
            name="trim_scan",
            field=models.BooleanField(
                default=False,
                verbose_name="Cắt nền quét",
                help_text="Tự cắt nền tối quanh trang. Tắt với sách ố vàng — dễ mất lề chữ.",
            ),
        ),
        migrations.AlterField(
            model_name="detectorconfig",
            name="params",
            field=models.JSONField(
                blank=True,
                default=dict,
                editable=False,
                verbose_name="Tham số legacy (JSON)",
                help_text="Không dùng — giữ cho tương thích cũ.",
            ),
        ),
        migrations.RunPython(migrate_params_to_fields, migrations.RunPython.noop),
    ]
