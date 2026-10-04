from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dataset", "0014_ragflow_text_image_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcedocument",
            name="ragflow_match_status",
            field=models.CharField(
                blank=True,
                choices=[("matched", "Khớp với RAGFlow"), ("mismatched", "Không khớp với RAGFlow")],
                default="",
                max_length=16,
                verbose_name="Đối chiếu RAGFlow",
            ),
        ),
        migrations.AddField(
            model_name="sourcedocument",
            name="ragflow_match_detail",
            field=models.TextField(blank=True, default="", verbose_name="Chi tiết đối chiếu RAGFlow"),
        ),
        migrations.AddField(
            model_name="imagesourcedocument",
            name="ragflow_match_status",
            field=models.CharField(
                blank=True,
                choices=[("matched", "Khớp với RAGFlow"), ("mismatched", "Không khớp với RAGFlow")],
                default="",
                max_length=16,
                verbose_name="Đối chiếu RAGFlow",
            ),
        ),
        migrations.AddField(
            model_name="imagesourcedocument",
            name="ragflow_match_detail",
            field=models.TextField(blank=True, default="", verbose_name="Chi tiết đối chiếu RAGFlow"),
        ),
    ]
