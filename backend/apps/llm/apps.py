from __future__ import annotations

from django.apps import AppConfig


class LLMConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.llm"
    verbose_name = "OCR / LLM"

    def ready(self):
        from . import signals  # noqa: F401

