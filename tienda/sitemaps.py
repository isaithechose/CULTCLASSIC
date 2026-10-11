from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils.text import slugify

from .models import Producto, Categoria


class ProductoSitemap(Sitemap):
    """Productos disponibles del catálogo."""
    changefreq = "weekly"
    priority = 0.9
    protocol = "https"

    def items(self):
        return Producto.objects.filter(disponible=True).order_by("-fecha_actualizacion")

    def location(self, obj):
        return obj.get_absolute_url()

    def lastmod(self, obj):
        return obj.fecha_actualizacion


class CategoriaSitemap(Sitemap):
    """Categorias con productos disponibles."""
    changefreq = "weekly"
    priority = 0.8
    protocol = "https"

    def items(self):
        return list(Categoria.objects.filter(producto__disponible=True).distinct())

    def location(self, obj):
        return reverse("tienda:categoria", args=[slugify(obj.nombre)])


class StaticViewSitemap(Sitemap):
    """Páginas estáticas relevantes para indexar."""
    protocol = "https"

    def items(self):
        return [
            "tienda:tienda",
            "tienda:lista_productos",
            "tienda:shaka_wear_mexico",
            "tienda:culto_calle",
            "tienda:nosotros",
            "tienda:catalogo_diseños",
            "tienda:catalogo_diseños_propios",
            "tienda:design_creator",
            "tienda:mayoreo",
            "tienda:faq",
            "tienda:devoluciones",
            "tienda:privacidad",
        ]

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        if item == "tienda:tienda":
            return 1.0
        if item in ("tienda:shaka_wear_mexico", "tienda:culto_calle", "tienda:lista_productos"):
            return 0.9
        if item == "tienda:nosotros":
            return 0.7
        return 0.6

    def changefreq(self, item):
        if item in ("tienda:tienda", "tienda:lista_productos", "tienda:shaka_wear_mexico"):
            return "weekly"
        return "monthly"
