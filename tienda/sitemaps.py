from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Producto


class ProductoSitemap(Sitemap):
    """Productos disponibles del catálogo."""
    changefreq = "weekly"
    priority = 0.9
    protocol = "https"

    def items(self):
        return Producto.objects.filter(disponible=True).order_by("-fecha_actualizacion")

    def location(self, obj):
        return reverse("tienda:detalle_producto", args=[obj.id])

    def lastmod(self, obj):
        return obj.fecha_actualizacion


class StaticViewSitemap(Sitemap):
    """Páginas estáticas relevantes para indexar."""
    changefreq = "monthly"
    priority = 0.6
    protocol = "https"

    def items(self):
        return [
            "tienda:tienda",
            "tienda:archivo",
            "tienda:catalogo_diseños_propios",
            "tienda:design_creator",
            "tienda:faq",
            "tienda:devoluciones",
            "tienda:privacidad",
        ]

    def location(self, item):
        return reverse(item)
