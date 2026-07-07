from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path

from auctions.views import serve_uploaded_media

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("auctions.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.SERVE_MEDIA_FILES:
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", serve_uploaded_media),
    ]
