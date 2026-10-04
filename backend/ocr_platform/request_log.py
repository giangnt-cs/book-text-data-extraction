"""Ghi log POST upload ngay khi header tới Django — trước khi đọc body file."""

from __future__ import annotations

import logging

logger = logging.getLogger("ocr.upload")


class IncomingPostLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST":
            path = request.path or ""
            length = request.META.get("CONTENT_LENGTH") or "?"
            try:
                size = int(length)
            except (TypeError, ValueError):
                size = 0
            if "upload" in path or "stagedbook" in path or "/book/" in path or size > 1024 * 1024:
                logger.info("Nhận POST %s content-length=%s", path, length)
        return self.get_response(request)
