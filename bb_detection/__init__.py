"""
Bounding Box Detection Module
Chứa các class để thực hiện khoanh vùng dữ liệu từ PDF sử dụng các thư viện khác nhau.
"""

from .base_detector import BaseDetector, BoundingBox, ContentType
from .doclayout_yolo_detector import DocLayoutYOLODetector
from .detector_factory import DetectorFactory

__all__ = [
    "BaseDetector",
    "BoundingBox",
    "ContentType",
    "DocLayoutYOLODetector",
    "DetectorFactory",
]
