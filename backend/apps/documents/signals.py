from __future__ import annotations

from django.db.models.signals import post_migrate
from django.dispatch import receiver

from .classification import CLASSIFICATION_CATALOG
from .models import ClassificationGroup, ClassificationTerm


@receiver(post_migrate)
def ensure_classification_catalog(sender, **kwargs):
    if sender.name != "apps.documents":
        return

    for order, (slug, name, kind, terms) in enumerate(CLASSIFICATION_CATALOG, start=1):
        group, _ = ClassificationGroup.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "kind": kind, "sort_order": order},
        )
        for term_order, (code, term_name, description) in enumerate(terms, start=1):
            ClassificationTerm.objects.get_or_create(
                group=group,
                code=code,
                defaults={
                    "name": term_name,
                    "description": description,
                    "sort_order": term_order,
                },
            )
