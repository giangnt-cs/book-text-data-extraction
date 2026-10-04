from django.db import migrations, models


def copy_publication_from_books(apps, schema_editor):
    SourceDocument = apps.get_model("dataset", "SourceDocument")
    for document in SourceDocument.objects.exclude(book_id=None).select_related("book").iterator():
        book = document.book
        if book is None:
            continue
        SourceDocument.objects.filter(pk=document.pk).update(
            author=book.author or "",
            publisher=book.publisher or "",
            published_year=book.published_year or "",
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("dataset", "0006_ragflow"),
        ("documents", "0010_book_publication_meta"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcedocument",
            name="author",
            field=models.CharField(blank=True, default="", max_length=400, verbose_name="Tên tác giả"),
        ),
        migrations.AddField(
            model_name="sourcedocument",
            name="publisher",
            field=models.CharField(blank=True, default="", max_length=400, verbose_name="Nhà xuất bản"),
        ),
        migrations.AddField(
            model_name="sourcedocument",
            name="published_year",
            field=models.CharField(blank=True, default="", max_length=32, verbose_name="Năm xuất bản"),
        ),
        migrations.RunPython(copy_publication_from_books, noop_reverse),
    ]
