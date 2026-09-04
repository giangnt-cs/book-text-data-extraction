from __future__ import annotations

from django.apps import AppConfig


class DatasetConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dataset"
    verbose_name = "Dataset"

    def ready(self):
        from . import signals  # noqa: F401
