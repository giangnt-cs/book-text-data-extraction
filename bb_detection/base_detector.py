"""
Base Detector Module
Định nghĩa abstract class và các kiểu dữ liệu chung cho tất cả các detector.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
from pathlib import Path
import json
import os
import tempfile

logger = logging.getLogger(__name__)


class ContentType(Enum):
    """Phân loại nội dung trong vùng dữ liệu"""
    TEXT = "text"                 # Văn bản thông thường
    IMAGE = "image"               # Hình ảnh
    FORMULA = "formula"           # Công thức toán học
    TABLE = "table"               # Bảng biểu
    COLUMNAR = "columnar"         # Văn bản có chia cột
    UNKNOWN = "unknown"           # Chưa xác định


@dataclass
class BoundingBox:
    """
    Đại diện cho một vùng dữ liệu được khoanh trong trang.
    
    Attributes:
        x: Tọa độ x của góc trên trái
        y: Tọa độ y của góc trên trái
        width: Chiều rộng của vùng
        height: Chiều cao của vùng
        content_type: Loại nội dung trong vùng
        confidence: Độ tin cậy của việc phát hiện (0-1)
        page_number: Số trang chứa vùng này
        raw_text: Text thô được nhận dạng (nếu có)
        image_path: Đường dẫn đến hình ảnh được crop (nếu có)
        metadata: Các thông tin bổ sung
    """
    x: float
    y: float
    width: float
    height: float
    content_type: ContentType = ContentType.UNKNOWN
    confidence: float = 0.0
    page_number: int = 0
    raw_text: Optional[str] = None
    image_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def x2(self) -> float:
        """Tọa độ x của góc dưới phải"""
        return self.x + self.width
    
    @property
    def y2(self) -> float:
        """Tọa độ y của góc dưới phải"""
        return self.y + self.height
    
    @property
    def area(self) -> float:
        """Diện tích của vùng"""
        return self.width * self.height
    
    @property
    def center(self) -> tuple:
        """Tọa độ tâm của vùng"""
        return (self.x + self.width / 2, self.y + self.height / 2)
    
    def to_dict(self) -> Dict[str, Any]:
        """Chuyển đổi sang dictionary để lưu JSON"""
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "x2": self.x2,
            "y2": self.y2,
            "content_type": self.content_type.value,
            "confidence": self.confidence,
            "page_number": self.page_number,
            "raw_text": self.raw_text,
            "image_path": self.image_path,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BoundingBox':
        """Tạo BoundingBox từ dictionary"""
        return cls(
            x=data["x"],
            y=data["y"],
            width=data["width"],
            height=data["height"],
            content_type=ContentType(data.get("content_type", "unknown")),
            confidence=data.get("confidence", 0.0),
            page_number=data.get("page_number", 0),
            raw_text=data.get("raw_text"),
            image_path=data.get("image_path"),
            metadata=data.get("metadata", {})
        )


@dataclass
class PageDetectionResult:
    """
    Kết quả phát hiện của một trang.
    
    Attributes:
        page_number: Số trang
        page_width: Chiều rộng trang
        page_height: Chiều cao trang
        bounding_boxes: Danh sách các vùng được phát hiện
        page_image_path: Đường dẫn đến hình ảnh của trang
    """
    page_number: int
    page_width: float
    page_height: float
    bounding_boxes: List[BoundingBox] = field(default_factory=list)
    page_image_path: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Chuyển đổi sang dictionary"""
        return {
            "page_number": self.page_number,
            "page_width": self.page_width,
            "page_height": self.page_height,
            "page_image_path": self.page_image_path,
            "bounding_boxes": [bb.to_dict() for bb in self.bounding_boxes]
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PageDetectionResult':
        """Tạo PageDetectionResult từ dictionary"""
        return cls(
            page_number=data["page_number"],
            page_width=data["page_width"],
            page_height=data["page_height"],
            page_image_path=data.get("page_image_path"),
            bounding_boxes=[BoundingBox.from_dict(bb) for bb in data.get("bounding_boxes", [])]
        )


@dataclass
class DocumentDetectionResult:
    """
    Kết quả phát hiện của toàn bộ tài liệu.
    
    Attributes:
        source_file: Đường dẫn file PDF gốc
        detector_name: Tên thư viện detector được sử dụng
        pages: Danh sách kết quả của từng trang
        output_dir: Thư mục chứa kết quả
    """
    source_file: str
    detector_name: str
    pages: List[PageDetectionResult] = field(default_factory=list)
    output_dir: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Chuyển đổi sang dictionary"""
        return {
            "source_file": self.source_file,
            "detector_name": self.detector_name,
            "output_dir": self.output_dir,
            "total_pages": len(self.pages),
            "pages": [page.to_dict() for page in self.pages]
        }
    
    def save_to_json(self, output_path: str) -> None:
        """Lưu kết quả ra file JSON"""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
    
    @classmethod
    def load_from_json(cls, json_path: str) -> 'DocumentDetectionResult':
        """Tải kết quả từ file JSON"""
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return cls(
            source_file=data["source_file"],
            detector_name=data["detector_name"],
            output_dir=data.get("output_dir"),
            pages=[PageDetectionResult.from_dict(page) for page in data.get("pages", [])]
        )


class BaseDetector(ABC):
    """
    Abstract base class cho tất cả các detector.
    Định nghĩa interface chung cho việc phát hiện bounding box.
    """
    
    def __init__(self, output_dir: Optional[str] = None):
        """
        Khởi tạo detector.
        
        Args:
            output_dir: Thư mục để lưu kết quả
        """
        self.output_dir = output_dir or tempfile.mkdtemp(prefix="bbox_detect_")
        self._ensure_output_dir()
    
    def _ensure_output_dir(self) -> None:
        """Tạo thư mục output nếu chưa tồn tại"""
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tên của detector"""
        pass
    
    @abstractmethod
    def detect_page(self, page_image, page_number: int) -> PageDetectionResult:
        """
        Phát hiện các vùng dữ liệu trong một trang.
        
        Args:
            page_image: Hình ảnh của trang (PIL Image hoặc numpy array)
            page_number: Số trang
            
        Returns:
            PageDetectionResult: Kết quả phát hiện của trang
        """
        pass
    
    @abstractmethod
    def classify_content(self, bbox: BoundingBox, image) -> ContentType:
        """
        Phân loại nội dung trong một vùng.
        
        Args:
            bbox: Vùng cần phân loại
            image: Hình ảnh được crop của vùng đó
            
        Returns:
            ContentType: Loại nội dung
        """
        pass
    
    def detect_document(
        self,
        pdf_path: str,
        dpi: int = 200,
        *,
        use_cropbox: bool = True,
        trim_scan: bool = False,
    ) -> DocumentDetectionResult:
        """
        Phát hiện các vùng dữ liệu trong toàn bộ tài liệu PDF.

        Args:
            pdf_path: Đường dẫn đến file PDF
            dpi: Độ phân giải khi chuyển PDF sang hình ảnh
            use_cropbox: dùng CropBox PDF
            trim_scan: cắt nền quét (mặc định tắt)
        """
        from PIL import Image
        import numpy as np

        from .pdf_render import render_all_pages
        
        # Tạo thư mục con cho file này
        pdf_name = Path(pdf_path).stem
        doc_output_dir = os.path.join(self.output_dir, self.name, pdf_name)
        Path(doc_output_dir).mkdir(parents=True, exist_ok=True)
        
        # Chuyển PDF sang hình ảnh (trim_scan tắt mặc định — tránh cắt mất lề sách)
        logger.info("[%s] Đang chuyển đổi PDF sang hình ảnh...", self.name)
        pages_images = render_all_pages(
            pdf_path, dpi=dpi, use_cropbox=use_cropbox, trim_scan=trim_scan
        )
        
        result = DocumentDetectionResult(
            source_file=pdf_path,
            detector_name=self.name,
            output_dir=doc_output_dir
        )
        
        # Xử lý từng trang
        for page_num, page_image in enumerate(pages_images, start=1):
            logger.info("[%s] Đang xử lý trang %s/%s...", self.name, page_num, len(pages_images))
            
            # Lưu hình ảnh trang
            page_image_path = os.path.join(doc_output_dir, f"page_{page_num:04d}.png")
            page_image.save(page_image_path)
            
            # Phát hiện các vùng trong trang
            page_result = self.detect_page(page_image, page_num)
            page_result.page_image_path = page_image_path
            
            # Crop và lưu từng vùng
            for idx, bbox in enumerate(page_result.bounding_boxes):
                cropped = page_image.crop((int(bbox.x), int(bbox.y), int(bbox.x2), int(bbox.y2)))
                crop_path = os.path.join(doc_output_dir, f"page_{page_num:04d}_box_{idx:03d}.png")
                cropped.save(crop_path)
                bbox.image_path = crop_path
            
            result.pages.append(page_result)
        
        # Lưu kết quả JSON
        json_path = os.path.join(doc_output_dir, "detection_result.json")
        result.save_to_json(json_path)
        logger.info("[%s] Đã lưu kết quả vào: %s", self.name, json_path)
        
        return result

