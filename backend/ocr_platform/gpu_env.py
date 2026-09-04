"""Chuẩn hóa biến môi trường GPU trong Docker worker."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def normalize_cuda_visible_devices() -> None:
    """
    Container chỉ mount 1 GPU vật lý (NVIDIA_VISIBLE_DEVICES=<id>) → bên trong luôn cuda:0.

    Lỗi thường gặp: đặt CUDA_VISIBLE_DEVICES=1 trong .env nghĩ là chọn GPU host #1,
    trong khi card đó đã map thành cuda:0 — PyTorch báo is_available()=False.
    """
    nv = os.getenv("NVIDIA_VISIBLE_DEVICES", "").strip()
    if not nv or nv.lower() in {"all", "void", "none"} or "," in nv:
        return

    cuda = os.getenv("CUDA_VISIBLE_DEVICES", "").strip()
    if cuda != "0":
        logger.warning(
            "CUDA_VISIBLE_DEVICES=%s → 0 (container 1 GPU, card vật lý=%s qua NVIDIA_VISIBLE_DEVICES)",
            cuda or "(trống)",
            nv,
        )
    os.environ["CUDA_VISIBLE_DEVICES"] = "0"


def cuda_is_usable() -> bool:
    normalize_cuda_visible_devices()
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception as exc:
        logger.warning("Không kiểm tra được CUDA: %s", exc)
        return False
