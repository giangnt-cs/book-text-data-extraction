import re

from django.db import migrations, models, transaction
from django.db.models import Q

_CODE_RE = re.compile(r"^(.+)_(\d+)$")
_MAIN = ("class_source", "class_field", "class_language")
_ALL = _MAIN + ("class_doc_type", "class_era")


def _dsg_prefix(doc) -> str:
    parts: list[str] = []
    for attr in _MAIN:
        term = getattr(doc, attr, None)
        code = (getattr(term, "code", None) or "").strip()
        if not code:
            return ""
        parts.append(code)
    return "".join(parts)


def _assign_catalog_code(SourceDocument, doc) -> None:
    prefix = _dsg_prefix(doc)
    if not prefix:
        return
    current = (doc.catalog_code or "").strip()
    match = _CODE_RE.match(current)
    if match and match.group(1) == prefix:
        return
    with transaction.atomic():
        qs = SourceDocument.objects.select_for_update().filter(catalog_code__startswith=f"{prefix}_")
        if doc.pk:
            qs = qs.exclude(pk=doc.pk)
        max_n = 0
        for code in qs.values_list("catalog_code", flat=True):
            parsed = _CODE_RE.match(code or "")
            if parsed and parsed.group(1) == prefix:
                max_n = max(max_n, int(parsed.group(2)))
        doc.catalog_code = f"{prefix}_{max_n + 1:03d}"
        SourceDocument.objects.filter(pk=doc.pk).update(catalog_code=doc.catalog_code)


def backfill_source_classification(apps, schema_editor):
    SourceDocument = apps.get_model("dataset", "SourceDocument")
    related = ("book", *_ALL)
    for doc in SourceDocument.objects.select_related(*related).order_by("created_at"):
        if doc.book_id:
            book = doc.book
            for attr in _ALL:
                setattr(doc, f"{attr}_id", getattr(book, f"{attr}_id"))
            doc.save(update_fields=[f"{attr}_id" for attr in _ALL] + ["updated_at"])
            doc = SourceDocument.objects.select_related(*related).get(pk=doc.pk)
        _assign_catalog_code(SourceDocument, doc)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("dataset", "0002_source_classification"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="sourcedocument",
            constraint=models.UniqueConstraint(
                condition=~Q(catalog_code=""),
                fields=("catalog_code",),
                name="uniq_dataset_catalog_code",
            ),
        ),
        migrations.RunPython(backfill_source_classification, noop_reverse),
    ]
