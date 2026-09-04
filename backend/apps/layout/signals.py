from __future__ import annotations

from django.db.models.signals import post_migrate
from django.dispatch import receiver

from .models import DetectorConfig


@receiver(post_migrate)
def ensure_default_detector(sender, **kwargs):
    if sender.name != "apps.layout":
        return
    if DetectorConfig.objects.exists():
        return
    DetectorConfig.objects.create(
        name="doclayout-yolo mặc định",
        model_path="models/doclayout_yolo.pt",
        confidence=0.25,
        imgsz=1024,
        dpi=150,
        use_cropbox=True,
        trim_scan=False,
        device="cuda:0",
        is_active=True,
        is_default=True,
    )
