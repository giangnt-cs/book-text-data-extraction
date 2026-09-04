from django.db import migrations, models


def forwards_cuda_to_cuda0(apps, schema_editor):
    DetectorConfig = apps.get_model("layout", "DetectorConfig")
    DetectorConfig.objects.filter(device="cuda").update(device="cuda:0")


class Migration(migrations.Migration):

    dependencies = [
        ("layout", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards_cuda_to_cuda0, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="detectorconfig",
            name="device",
            field=models.CharField(
                choices=[
                    ("cuda:0", "GPU 0 (cuda:0)"),
                    ("cuda:1", "GPU 1 (cuda:1)"),
                    ("cpu", "CPU"),
                ],
                default="cuda:0",
                help_text=(
                    "Chỉ số CUDA khi chạy trực tiếp trên host. "
                    "Trong Docker, dùng biến GPU_DEVICE_IDS trong .env để chọn GPU vật lý."
                ),
                max_length=16,
                verbose_name="Thiết bị",
            ),
        ),
    ]
