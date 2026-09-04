"""
Detector Factory – chỉ hỗ trợ doclayout-yolo.
"""

from typing import Dict, Optional, Type

from .base_detector import BaseDetector
from .doclayout_yolo_detector import DocLayoutYOLODetector


class DetectorFactory:
    _detectors: Dict[str, Type[BaseDetector]] = {
        "doclayout_yolo": DocLayoutYOLODetector,
        "doclayout-yolo": DocLayoutYOLODetector,
    }

    @classmethod
    def create(cls, name: str, output_dir: Optional[str] = None, **kwargs) -> BaseDetector:
        name_lower = name.lower()
        if name_lower not in cls._detectors:
            available = ", ".join(sorted(cls._detectors.keys()))
            raise ValueError(f"Không tìm thấy detector '{name}'. Khả dụng: {available}")
        return cls._detectors[name_lower](output_dir=output_dir, **kwargs)

    @classmethod
    def list_available(cls) -> list:
        return sorted(set(cls._detectors.keys()))
