import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Thêm cột trước. Unique index + backfill ở 0003 — tránh pending trigger trên PostgreSQL."""

    # Mỗi AddField commit riêng: FK/default="" không để trigger dở dang khi CREATE INDEX.
    atomic = False

    dependencies = [
        ("dataset", "0001_initial"),
        ("documents", "0006_book_classification"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcedocument",
            name="class_source",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"group__slug": "source"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="dataset_sources_source",
                to="documents.classificationterm",
                verbose_name="Nguồn y học",
            ),
        ),
        migrations.AddField(
            model_name="sourcedocument",
            name="class_field",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"group__slug": "field"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="dataset_sources_field",
                to="documents.classificationterm",
                verbose_name="Lĩnh vực",
            ),
        ),
        migrations.AddField(
            model_name="sourcedocument",
            name="class_language",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"group__slug": "language"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="dataset_sources_language",
                to="documents.classificationterm",
                verbose_name="Ngôn ngữ",
            ),
        ),
        migrations.AddField(
            model_name="sourcedocument",
            name="class_doc_type",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"group__slug": "doc_type"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="dataset_sources_doc_type",
                to="documents.classificationterm",
                verbose_name="Loại tài liệu",
            ),
        ),
        migrations.AddField(
            model_name="sourcedocument",
            name="class_era",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"group__slug": "era"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="dataset_sources_era",
                to="documents.classificationterm",
                verbose_name="Thời đại",
            ),
        ),
        migrations.AddField(
            model_name="sourcedocument",
            name="catalog_code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Tự gán khi đủ Nguồn y học, Lĩnh vực và Ngôn ngữ. Dạng DSG_001.",
                max_length=32,
                verbose_name="Mã văn bản",
            ),
        ),
    ]
