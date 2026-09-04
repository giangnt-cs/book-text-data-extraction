from __future__ import annotations

from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import DetectorConfig


@admin.register(DetectorConfig)
class DetectorConfigAdmin(ModelAdmin):
    list_display = (
        "name",
        "dpi",
        "imgsz",
        "confidence",
        "trim_scan",
        "device",
        "is_active",
        "is_default",
    )
    list_filter = ("is_active", "device", "trim_scan")
    search_fields = ("name",)
    fieldsets = (
        (None, {"fields": ("name", "is_active", "is_default")}),
        (
            "Model & thiết bị",
            {
                "fields": ("model_path", "device", "confidence", "imgsz"),
                "description": (
                    "Chọn GPU vật lý trên host bằng GPU_DEVICE_IDS trong .env (mặc định 0). "
                    "Trong container worker luôn dùng cuda:0."
                ),
            },
        ),
        (
            "Render PDF → ảnh",
            {
                "fields": ("dpi", "use_cropbox", "trim_scan"),
                "description": (
                    "DPI và tuỳ chọn cắt ảnh trước khi detect. "
                    "Giữ «Cắt nền quét» tắt với sách ố vàng để không mất lề chữ."
                ),
            },
        ),
    )
