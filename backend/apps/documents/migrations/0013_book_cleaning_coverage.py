from django.db import migrations, models


def backfill_cleaning_coverage(apps, schema_editor):
    Book = apps.get_model("documents", "Book")
    BookCleaningBatch = apps.get_model("documents", "BookCleaningBatch")
    Page = apps.get_model("documents", "Page")
    PageCleanedText = apps.get_model("documents", "PageCleanedText")

    for book in Book.objects.all().iterator():
        batch = (
            BookCleaningBatch.objects.filter(book=book, status="done")
            .order_by("-created_at")
            .first()
        )
        ocr_ids = [
            page.id
            for page in Page.objects.filter(book=book).only("id", "text_md")
            if (page.text_md or "").strip()
        ]
        if not batch or not ocr_ids:
            coverage = "none"
        else:
            cleaned_n = (
                PageCleanedText.objects.filter(batch=batch, page_id__in=ocr_ids)
                .exclude(text_md="")
                .values("page_id")
                .distinct()
                .count()
            )
            if cleaned_n <= 0:
                coverage = "none"
            elif cleaned_n >= len(ocr_ids):
                coverage = "full"
            else:
                coverage = "partial"
        Book.objects.filter(pk=book.pk).update(cleaning_coverage=coverage)


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0012_book_chunking"),
    ]

    operations = [
        migrations.AddField(
            model_name="book",
            name="cleaning_coverage",
            field=models.CharField(
                choices=[
                    ("none", "Chưa làm sạch"),
                    ("partial", "Làm sạch một phần"),
                    ("full", "Làm sạch toàn bộ"),
                ],
                default="none",
                help_text="Tự cập nhật: chưa làm sạch, một phần (một số trang), hoặc toàn bộ sách.",
                max_length=16,
                verbose_name="Mức làm sạch",
            ),
        ),
        migrations.RunPython(backfill_cleaning_coverage, migrations.RunPython.noop),
    ]
