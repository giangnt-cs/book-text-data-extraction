"""
DocLayout-YOLO Detector (layout-only, scan/ảnh)

Chạy inference trên ảnh trang (render từ PDF) bằng DocLayout-YOLO / Ultralytics YOLOv10.
Repo upstream: https://github.com/opendatalab/DocLayout-YOLO (AGPL-3.0)

Lưu ý:
- Có 2 cách load model:
  1) `weights_path`: dùng file `.pt` local (khuyến nghị để ổn định).
  2) `pretrained_repo_id`: dùng `YOLOv10.from_pretrained("<repo_id>")` (cần internet).
"""

from __future__ import annotations

from typing import List, Optional

from .base_detector import BaseDetector, BoundingBox, ContentType, PageDetectionResult


def _map_label_to_content_type(label: str) -> ContentType:
    s = (label or "").strip().lower()
    if any(k in s for k in ["table"]):
        return ContentType.TABLE
    if any(k in s for k in ["figure", "image", "img", "picture", "photo"]):
        return ContentType.IMAGE
    if any(k in s for k in ["equation", "formula", "math"]):
        return ContentType.FORMULA
    if any(k in s for k in ["title", "heading", "header", "text", "paragraph", "caption", "footer", "list"]):
        return ContentType.TEXT
    return ContentType.UNKNOWN


class DocLayoutYOLODetector(BaseDetector):
    def __init__(
        self,
        output_dir: Optional[str] = None,
        weights_path: str = "/app/models/doclayout_yolo.pt",
        pretrained_repo_id: Optional[str] = None,
        imgsz: int = 1024,
        conf: float = 0.25,
        device: str = "cuda:0",
    ):
        super().__init__(output_dir)
        self.weights_path = weights_path
        self.pretrained_repo_id = pretrained_repo_id
        self.imgsz = int(imgsz)
        self.conf = float(conf)
        self.device = device
        self._model = None

    @property
    def name(self) -> str:
        return "doclayout_yolo"

    def _init_model(self):
        if self._model is not None:
            return

        # DocLayout-YOLO cung cấp package `doclayout_yolo` (PyPI: doclayout-yolo).
        # Weights DocLayout-YOLO thường yêu cầu đúng runtime này. Vì vậy ưu tiên load trực tiếp từ doclayout_yolo,
        # không phụ thuộc `ultralytics` (tránh mismatch phiên bản).
        import_err: Exception | None = None
        try:
            # Một số bản export YOLOv10 trực tiếp
            from doclayout_yolo import YOLOv10 as DLYOLOv10  # type: ignore
            YOLOv10_cls = DLYOLOv10  # type: ignore
        except Exception as e:
            import_err = e
            YOLOv10_cls = None  # type: ignore

        try:
            # Fallback: đôi khi class tên YOLO
            from doclayout_yolo import YOLO as DLYOLO  # type: ignore
        except Exception as e:
            if import_err is None:
                import_err = e
            DLYOLO = None  # type: ignore

        if YOLOv10_cls is None and DLYOLO is None:
            detail = f" ({import_err})" if import_err else ""
            raise RuntimeError(
                "Không load được doclayout-yolo (module `doclayout_yolo`)."
                f"{detail}\n"
                "Rebuild worker: docker compose build --no-cache worker-gpu"
            ) from import_err

        if self.pretrained_repo_id:
            if YOLOv10_cls is None or not hasattr(YOLOv10_cls, "from_pretrained"):
                raise RuntimeError(
                    "Bản `doclayout-yolo` hiện tại không hỗ trợ `YOLOv10.from_pretrained`.\n"
                    "Hãy dùng weights local (.pt) hoặc cập nhật `doclayout-yolo`."
                )
            self._model = YOLOv10_cls.from_pretrained(self.pretrained_repo_id)  # type: ignore
        else:
            if YOLOv10_cls is not None:
                self._model = YOLOv10_cls(self.weights_path)  # type: ignore
            else:
                self._model = DLYOLO(self.weights_path)  # type: ignore

    def detect_page(self, page_image, page_number: int) -> PageDetectionResult:
        """
        Detect trên một ảnh trang (PIL Image). Trả bbox theo pixels top-left.
        """
        self._init_model()

        import numpy as np
        from PIL import Image

        if isinstance(page_image, Image.Image):
            img = page_image
            img_np = np.array(img.convert("RGB"))
            w, h = img.size
        else:
            img_np = page_image
            h, w = img_np.shape[:2]

        # predict
        results = self._model.predict(img_np, imgsz=self.imgsz, conf=self.conf, device=self.device, verbose=False)
        if not results:
            return PageDetectionResult(page_number=page_number, page_width=w, page_height=h, bounding_boxes=[])

        r0 = results[0]
        names = getattr(r0, "names", {}) or {}
        boxes = getattr(r0, "boxes", None)
        if boxes is None:
            return PageDetectionResult(page_number=page_number, page_width=w, page_height=h, bounding_boxes=[])

        out: List[BoundingBox] = []
        xyxy = boxes.xyxy.cpu().numpy() if hasattr(boxes.xyxy, "cpu") else boxes.xyxy.numpy()
        cls = boxes.cls.cpu().numpy() if hasattr(boxes.cls, "cpu") else boxes.cls.numpy()
        confs = boxes.conf.cpu().numpy() if hasattr(boxes.conf, "cpu") else boxes.conf.numpy()

        for i in range(len(xyxy)):
            x1, y1, x2, y2 = map(float, xyxy[i])
            if x2 <= x1 or y2 <= y1:
                continue
            label = str(names.get(int(cls[i]), int(cls[i])))
            ctype = _map_label_to_content_type(label)
            out.append(
                BoundingBox(
                    x=x1,
                    y=y1,
                    width=x2 - x1,
                    height=y2 - y1,
                    content_type=ctype,
                    confidence=float(confs[i]) if confs is not None else 1.0,
                    page_number=page_number,
                    raw_text=None,
                    metadata={
                        "coord_space": "image_pixels_top_left",
                        "source": "doclayout_yolo",
                        "label": label,
                        "imgsz": self.imgsz,
                        "conf": self.conf,
                        "device": self.device,
                        "weights_path": self.weights_path,
                    },
                )
            )

        return PageDetectionResult(page_number=page_number, page_width=w, page_height=h, bounding_boxes=out)

    def classify_content(self, bbox: BoundingBox, image) -> ContentType:
        return bbox.content_type


