from __future__ import annotations

import os

from celery import Celery
from ocr_platform.gpu_env import normalize_cuda_visible_devices

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ocr_platform.settings")

app = Celery("ocr_platform")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.on_after_configure.connect
def _log_gpu_env(**kwargs) -> None:
    import logging

    normalize_cuda_visible_devices()
    logging.getLogger(__name__).info(
        "Worker GPU: GPU_DEVICE_IDS=%s NVIDIA_VISIBLE_DEVICES=%s CUDA_VISIBLE_DEVICES=%s",
        os.getenv("GPU_DEVICE_IDS"),
        os.getenv("NVIDIA_VISIBLE_DEVICES"),
        os.getenv("CUDA_VISIBLE_DEVICES"),
    )

