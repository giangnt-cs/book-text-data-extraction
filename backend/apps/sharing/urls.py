from __future__ import annotations

from django.urls import path

from apps.sharing import views

urlpatterns = [
    path("<str:token>/", views.share_gate, name="share_gate"),
    path("<str:token>/files/", views.share_catalog, name="share_catalog"),
    path("<str:token>/dl/<uuid:item_id>/<str:kind>/", views.share_download, name="share_download"),
]
