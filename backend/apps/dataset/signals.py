from __future__ import annotations

import logging

from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver

from apps.documents.models import Book, BookStatus

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Book)
def sync_dataset_when_book_approved(sender, instance: Book, **kwargs) -> None:
    if instance.status != BookStatus.APPROVED:
        return
    from apps.dataset.ingest import sync_source_from_book

    try:
        sync_source_from_book(instance)
    except Exception:
        logger.exception("Không đồng bộ Dataset từ sách «%s».", instance.title)


@receiver(post_migrate)
def import_approved_books_into_dataset(sender, **kwargs) -> None:
    if getattr(sender, "name", "") != "apps.dataset":
        return
    from apps.dataset.ingest import sync_approved_books

    try:
        created = sync_approved_books()
        if created:
            logger.info("Dataset: đã nhập %s sách đã duyệt.", created)
    except Exception:
        logger.exception("Dataset: không nhập được sách đã duyệt.")
