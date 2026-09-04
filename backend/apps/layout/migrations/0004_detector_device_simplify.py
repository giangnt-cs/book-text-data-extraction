# Generated manually

from django.db import migrations, models


def normalize_device(apps, schema_editor):
    DetectorConfig = apps.get_model("layout", "DetectorConfig")
    DetectorConfig.objects.filter(device__in=["cuda", "cuda:1"]).update(device="cuda:0")


class Migration(migrations.Migration):

    dependencies = [
        ("layout", "0003_detector_config_controls"),
    ]

    operations = [
        migrations.RunPython(normalize_device, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="detectorconfig",
            name="device",
            field=models.CharField(
                choices=[("cuda:0", "GPU"), ("cpu", "CPU")],
                default="cuda:0",
                help_text=(
                    "Chọn GPU hoặc CPU. Trong Docker, GPU vật lý được chọn bằng GPU_DEVICE_IDS trong .env; "
                    "trong container luôn dùng cuda:0."
                ),
                max_length=16,
                verbose_name="Thiết bị",
            ),
        ),
    ]
