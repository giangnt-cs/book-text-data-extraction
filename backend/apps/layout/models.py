from __future__ import annotations

from django.db import models


class DetectorConfig(models.Model):
    """Cấu hình doclayout-yolo để rút trích bounding box."""

    name = models.CharField(max_length=100, unique=True, verbose_name="Tên cấu hình")
    model_path = models.CharField(
        max_length=500,
        default="models/doclayout_yolo.pt",
        verbose_name="Đường dẫn model",
        help_text="Đường dẫn file .pt của doclayout-yolo (tương đối repo root).",
    )
    confidence = models.FloatField(
        default=0.25,
        verbose_name="Ngưỡng confidence",
        help_text="0.0–1.0. Cao hơn → ít bbox hơn, chính xác hơn.",
    )
    imgsz = models.PositiveIntegerField(
        default=1024,
        verbose_name="Kích thước infer (imgsz)",
        help_text="Cạnh ảnh đầu vào YOLO (px). Khuyến nghị 1024.",
    )
    dpi = models.PositiveIntegerField(
        default=150,
        verbose_name="DPI render PDF",
        help_text="Độ phân giải khi chuyển PDF → ảnh. Scan sách: 120–150.",
    )
    use_cropbox = models.BooleanField(
        default=True,
        verbose_name="Dùng CropBox PDF",
        help_text="Render theo CropBox của PDF.",
    )
    trim_scan = models.BooleanField(
        default=False,
        verbose_name="Cắt nền quét",
        help_text="Tự cắt nền tối quanh trang. Chỉ bật khi PDF chụp có nền bàn rõ.",
    )
    device = models.CharField(
        max_length=16,
        default="cuda:0",
        choices=[
            ("cuda:0", "GPU"),
            ("cpu", "CPU"),
        ],
        verbose_name="Thiết bị",
        help_text=(
            "Chọn GPU hoặc CPU. Trong Docker, GPU vật lý được chọn bằng GPU_DEVICE_IDS trong .env; "
            "trong container luôn dùng cuda:0."
        ),
    )
    params = models.JSONField(
        default=dict,
        blank=True,
        editable=False,
        verbose_name="Tham số legacy",
    )
    is_active = models.BooleanField(default=True, verbose_name="Kích hoạt")
    is_default = models.BooleanField(default=False, verbose_name="Mặc định")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cấu hình doclayout-yolo"
        verbose_name_plural = "Cấu hình doclayout-yolo"

    def save(self, *args, **kwargs):
        if self.is_default:
            DetectorConfig.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name
