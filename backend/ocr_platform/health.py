from __future__ import annotations

from django.http import JsonResponse


def healthz(_request):
    return JsonResponse({"ok": True})

