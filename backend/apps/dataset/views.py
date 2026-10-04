"""Endpoint công khai: ảnh trang sách theo image_id RAGFlow."""

from __future__ import annotations

from django.http import Http404, HttpResponse
from django.views.decorators.http import require_GET

from apps.dataset.models import ImageChunk, SourceChunk
from apps.dataset.ragflow import PageImageBase64Cache, image_chunk_png


@require_GET
def ragflow_page_image(request, image_id: str):
    image_id = (image_id or "").strip().removesuffix(".png")
    if not image_id:
        raise Http404

    chunk = (
        SourceChunk.objects.filter(ragflow_image_id=image_id)
        .select_related("document", "document__book")
        .first()
    )
    if chunk is not None:
        png = PageImageBase64Cache().png_for_chunk(chunk)
    else:
        image_chunk = ImageChunk.objects.filter(ragflow_image_id=image_id).first()
        if image_chunk is None:
            raise Http404
        png = image_chunk_png(image_chunk)
    if not png:
        raise Http404

    response = HttpResponse(png, content_type="image/png")
    response["Cache-Control"] = "public, max-age=86400"
    response["Access-Control-Allow-Origin"] = "*"
    return response
