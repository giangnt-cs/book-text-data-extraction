from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView
from unfold.sites import UnfoldAdminSite

from .health import healthz

# Unfold cần UnfoldAdminSite để có toggle_sidebar, search, …
if not isinstance(admin.site, UnfoldAdminSite):
    admin.site.__class__ = UnfoldAdminSite

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
]

if not getattr(settings, "USE_S3", False):
    from django.conf.urls.static import static

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
