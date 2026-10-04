from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from unfold.sites import UnfoldAdminSite

from .health import healthz
from apps.dataset.views import ragflow_page_image

# Unfold cần UnfoldAdminSite để có toggle_sidebar, search, …
if not isinstance(admin.site, UnfoldAdminSite):
    admin.site.__class__ = UnfoldAdminSite

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("s/", include("apps.sharing.urls")),
    re_path(r"^rag/images/(?P<image_id>[^/]+)/?$", ragflow_page_image, name="ragflow_page_image"),
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
]

if not getattr(settings, "USE_S3", False):
    from django.views.static import serve as serve_media

    # django.conf.urls.static chỉ gắn URL khi DEBUG=True — Docker mặc định DEBUG=0.
    urlpatterns += [
        re_path(
            r"^media/(?P<path>.*)$",
            serve_media,
            {"document_root": str(settings.MEDIA_ROOT)},
        ),
    ]
