"""URL configuration for the procuremcp project."""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

from procurement import pages

urlpatterns = [
    path("", pages.admin_home, name="home"),
    path("chat/", pages.chat_page, name="chat"),
    path("admin/", admin.site.urls),
    path("api/", include("procurement.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]
