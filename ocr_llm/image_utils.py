from __future__ import annotations

import io


def crop_region(image_bytes: bytes, x: float, y: float, width: float, height: float) -> bytes:
    """Cắt vùng bbox từ ảnh trang (tọa độ pixel)."""
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    left = max(0, int(x))
    top = max(0, int(y))
    right = min(img.width, int(x + width))
    bottom = min(img.height, int(y + height))
    if right <= left or bottom <= top:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    crop = img.crop((left, top, right, bottom))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()
