# Generated manually

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0005_page_ocr_tool"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClassificationGroup",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("slug", models.SlugField(max_length=32, unique=True, verbose_name="Mã nhóm")),
                ("name", models.CharField(max_length=120, verbose_name="Tên nhóm")),
                (
                    "kind",
                    models.CharField(
                        choices=[("main", "Nhóm chính"), ("aux", "Nhóm phụ")],
                        default="main",
                        max_length=8,
                        verbose_name="Loại nhóm",
                    ),
                ),
                ("sort_order", models.PositiveSmallIntegerField(default=0, verbose_name="Thứ tự")),
            ],
            options={
                "verbose_name": "Nhóm phân loại",
                "verbose_name_plural": "Nhóm phân loại",
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.CreateModel(
            name="ClassificationTerm",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("code", models.CharField(max_length=8, verbose_name="Ký tự / mã")),
                ("name", models.CharField(max_length=200, verbose_name="Thuật ngữ")),
                ("description", models.CharField(blank=True, max_length=400, verbose_name="Mô tả")),
                ("sort_order", models.PositiveSmallIntegerField(default=0, verbose_name="Thứ tự")),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="terms",
                        to="documents.classificationgroup",
                        verbose_name="Nhóm",
                    ),
                ),
            ],
            options={
                "verbose_name": "Thuật ngữ phân loại",
                "verbose_name_plural": "Thuật ngữ phân loại",
                "ordering": ["group__sort_order", "sort_order", "code"],
            },
        ),
        migrations.AddConstraint(
            model_name="classificationterm",
            constraint=models.UniqueConstraint(fields=("group", "code"), name="uniq_class_term_group_code"),
        ),
        migrations.AddField(
            model_name="book",
            name="class_source",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"group__slug": "source"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="books_source",
                to="documents.classificationterm",
                verbose_name="Nguồn y học",
            ),
        ),
        migrations.AddField(
            model_name="book",
            name="class_field",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"group__slug": "field"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="books_field",
                to="documents.classificationterm",
                verbose_name="Lĩnh vực",
            ),
        ),
        migrations.AddField(
            model_name="book",
            name="class_language",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"group__slug": "language"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="books_language",
                to="documents.classificationterm",
                verbose_name="Ngôn ngữ",
            ),
        ),
        migrations.AddField(
            model_name="book",
            name="class_doc_type",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"group__slug": "doc_type"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="books_doc_type",
                to="documents.classificationterm",
                verbose_name="Loại tài liệu",
            ),
        ),
        migrations.AddField(
            model_name="book",
            name="class_era",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"group__slug": "era"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="books_era",
                to="documents.classificationterm",
                verbose_name="Thời đại",
            ),
        ),
    ]
