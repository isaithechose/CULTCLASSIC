# cultcalle/urls.py
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.decorators.cache import cache_page

from tienda.sitemaps import ProductoSitemap, StaticViewSitemap

sitemaps = {
    "productos": ProductoSitemap,
    "static": StaticViewSitemap,
}

urlpatterns = [
    path('admin/', admin.site.urls),
    path('sitemap.xml', cache_page(60 * 60)(sitemap), {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('', include('tienda.urls', namespace='tienda')),
    path('accounts/', include('allauth.urls')),  # <-- para django-allauth
    path('mercadolibre/', include('mercadolibre.urls')),
]

# Para servir archivos estáticos y/o multimedia en modo DEBUG
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    if getattr(settings, "MEDIA_PLACEHOLDER", False):
        # En local faltan casi todas las imagenes de produccion: si el archivo
        # no existe, se sirve un placeholder en vez de un 404.
        from django.contrib.staticfiles import finders
        from django.http import FileResponse, Http404
        from django.urls import re_path
        from django.views.static import serve

        def media_or_placeholder(request, path):
            try:
                return serve(request, path, document_root=settings.MEDIA_ROOT)
            except Http404:
                return FileResponse(open(finders.find("images/placeholder.svg"), "rb"), content_type="image/svg+xml")

        urlpatterns += [re_path(rf"^{settings.MEDIA_URL.lstrip('/')}(?P<path>.*)$", media_or_placeholder)]
    else:
        urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
