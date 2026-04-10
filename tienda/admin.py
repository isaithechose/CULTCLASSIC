from django.conf import settings
from django.contrib import admin
from django.db.models import Count, Sum
from django.utils.html import format_html

from .models import (
    Carrito,
    Categoria,
    Order,
    OrderItem,
    Producto,
    Reseña,
    ShippingAddress,
    ShippingUpdate,
    Subcategoria,
)

import os


@admin.action(description="Importar imagenes desde /media/diseños_nuevos/")
def importar_disenos(modeladmin, request, queryset):
    ruta = os.path.join(settings.MEDIA_ROOT, "diseños_nuevos")
    categoria, _ = Categoria.objects.get_or_create(nombre="Diseños")
    creados = 0

    if not os.path.exists(ruta):
        os.makedirs(ruta)

    for archivo in os.listdir(ruta):
        if archivo.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
            nombre = os.path.splitext(archivo)[0]
            if not Producto.objects.filter(nombre=nombre).exists():
                Producto.objects.create(
                    nombre=nombre,
                    descripcion="Diseño importado automaticamente.",
                    precio=199.00,
                    stock=10,
                    imagen=f"diseños_nuevos/{archivo}",
                    categoria=categoria,
                    tallas_disponibles="S,M,L",
                    colores_disponibles="Negro,Blanco",
                    disponible=True,
                )
                creados += 1

    modeladmin.message_user(request, f"{creados} productos fueron creados desde imagenes.")


@admin.action(description="Marcar productos como disponibles")
def marcar_disponibles(modeladmin, request, queryset):
    updated = queryset.update(disponible=True)
    modeladmin.message_user(request, f"{updated} productos marcados como disponibles.")


@admin.action(description="Marcar productos como no disponibles")
def marcar_no_disponibles(modeladmin, request, queryset):
    updated = queryset.update(disponible=False)
    modeladmin.message_user(request, f"{updated} productos marcados como no disponibles.")


@admin.action(description="Marcar pedidos como completados")
def marcar_pedidos_completados(modeladmin, request, queryset):
    updated = queryset.update(status="Completed")
    modeladmin.message_user(request, f"{updated} pedidos marcados como completados.")


@admin.action(description="Marcar pedidos como enviados")
def marcar_pedidos_enviados(modeladmin, request, queryset):
    updated = queryset.update(shipping_status="Shipped")
    modeladmin.message_user(request, f"{updated} pedidos marcados como enviados.")


class SubcategoriaInline(admin.TabularInline):
    model = Subcategoria
    extra = 0
    fields = ("nombre", "descripcion")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ("product", "quantity", "price")
    autocomplete_fields = ("product",)


class ShippingUpdateInline(admin.TabularInline):
    model = ShippingUpdate
    extra = 0
    fields = ("status_message", "updated_at")
    readonly_fields = ("updated_at",)


class ShippingAddressInline(admin.StackedInline):
    model = ShippingAddress
    extra = 0
    can_delete = False


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "descripcion_corta", "total_subcategorias", "total_productos")
    search_fields = ("nombre", "descripcion")
    inlines = [SubcategoriaInline]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.annotate(
            subcategorias_count=Count("subcategorias", distinct=True),
            productos_count=Count("producto", distinct=True),
        )

    @admin.display(description="Descripcion")
    def descripcion_corta(self, obj):
        if not obj.descripcion:
            return "-"
        return (obj.descripcion[:60] + "...") if len(obj.descripcion) > 60 else obj.descripcion

    @admin.display(ordering="subcategorias_count", description="Subcategorias")
    def total_subcategorias(self, obj):
        return obj.subcategorias_count

    @admin.display(ordering="productos_count", description="Productos")
    def total_productos(self, obj):
        return obj.productos_count


@admin.register(Subcategoria)
class SubcategoriaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "categoria", "descripcion_corta")
    list_filter = ("categoria",)
    search_fields = ("nombre", "descripcion", "categoria__nombre")
    autocomplete_fields = ("categoria",)

    @admin.display(description="Descripcion")
    def descripcion_corta(self, obj):
        if not obj.descripcion:
            return "-"
        return (obj.descripcion[:60] + "...") if len(obj.descripcion) > 60 else obj.descripcion


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = (
        "preview_imagen",
        "nombre",
        "categoria",
        "subcategoria",
        "precio",
        "stock",
        "disponible",
        "fecha_actualizacion",
    )
    list_filter = ("disponible", "categoria", "subcategoria", "fecha_creacion", "fecha_actualizacion")
    search_fields = ("nombre", "descripcion", "slug_imagen")
    autocomplete_fields = ("categoria", "subcategoria")
    actions = [importar_disenos, marcar_disponibles, marcar_no_disponibles]
    list_editable = ("precio", "stock", "disponible")
    readonly_fields = ("fecha_creacion", "fecha_actualizacion", "imagen_preview_large")
    fieldsets = (
        ("Identidad", {
            "fields": ("nombre", "slug_imagen", "descripcion")
        }),
        ("Catalogo", {
            "fields": ("categoria", "subcategoria", "precio", "stock", "disponible")
        }),
        ("Variantes", {
            "fields": ("tallas_disponibles", "colores_disponibles")
        }),
        ("Media", {
            "fields": ("imagen", "imagen_preview_large")
        }),
        ("Control", {
            "fields": ("fecha_creacion", "fecha_actualizacion")
        }),
    )

    @admin.display(description="Imagen")
    def preview_imagen(self, obj):
        if obj.imagen:
            return format_html(
                '<img src="{}" style="width:42px;height:42px;object-fit:cover;border-radius:8px;" />',
                obj.imagen.url,
            )
        return "-"

    @admin.display(description="Vista previa")
    def imagen_preview_large(self, obj):
        if obj.imagen:
            return format_html(
                '<img src="{}" style="max-width:220px;border-radius:14px;border:1px solid #ddd;" />',
                obj.imagen.url,
            )
        return "Sin imagen"


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "customer",
        "status_badge",
        "shipping_badge",
        "total_items",
        "total_amount",
        "created_at",
    )
    list_filter = ("status", "shipping_status", "created_at")
    search_fields = ("id", "customer__username", "customer__email", "tracking_number")
    readonly_fields = ("created_at", "total_amount")
    autocomplete_fields = ("customer",)
    inlines = [OrderItemInline, ShippingAddressInline, ShippingUpdateInline]
    actions = [marcar_pedidos_completados, marcar_pedidos_enviados]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.annotate(
            items_total=Count("items", distinct=True),
            amount_total=Sum("items__price"),
        )

    @admin.display(ordering="status", description="Estado")
    def status_badge(self, obj):
        colors = {
            "Pending": "#9a7a2f",
            "Completed": "#2d8a4b",
            "Canceled": "#a33d3d",
        }
        return format_html(
            '<span style="padding:0.25rem 0.55rem;border-radius:999px;background:{}22;color:{};font-weight:700;">{}</span>',
            colors.get(obj.status, "#666"),
            colors.get(obj.status, "#666"),
            obj.status,
        )

    @admin.display(ordering="shipping_status", description="Envio")
    def shipping_badge(self, obj):
        colors = {
            "Processing": "#9a7a2f",
            "Shipped": "#2f67b0",
            "Delivered": "#2d8a4b",
        }
        return format_html(
            '<span style="padding:0.25rem 0.55rem;border-radius:999px;background:{}22;color:{};font-weight:700;">{}</span>',
            colors.get(obj.shipping_status, "#666"),
            colors.get(obj.shipping_status, "#666"),
            obj.shipping_status,
        )

    @admin.display(ordering="items_total", description="Items")
    def total_items(self, obj):
        return obj.items_total

    @admin.display(description="Total")
    def total_amount(self, obj):
        return f"${obj.total_price:.2f}"


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "product", "quantity", "price")
    list_filter = ("order__status", "order__shipping_status")
    search_fields = ("order__id", "product__nombre")
    autocomplete_fields = ("order", "product")


@admin.register(Carrito)
class CarritoAdmin(admin.ModelAdmin):
    list_display = ("usuario", "producto", "cantidad", "subtotal_display")
    list_filter = ("usuario",)
    search_fields = ("usuario__username", "producto__nombre")
    autocomplete_fields = ("usuario", "producto")

    @admin.display(description="Subtotal")
    def subtotal_display(self, obj):
        return f"${obj.subtotal():.2f}"


@admin.register(Reseña)
class ResenaAdmin(admin.ModelAdmin):
    list_display = ("producto", "usuario", "calificacion", "fecha")
    list_filter = ("calificacion", "fecha", "producto")
    search_fields = ("producto__nombre", "usuario__username", "comentario")
    autocomplete_fields = ("producto", "usuario")


@admin.register(ShippingAddress)
class ShippingAddressAdmin(admin.ModelAdmin):
    list_display = ("order", "city", "state", "country", "postal_code")
    search_fields = ("order__id", "city", "state", "country", "postal_code")
    autocomplete_fields = ("order",)


@admin.register(ShippingUpdate)
class ShippingUpdateAdmin(admin.ModelAdmin):
    list_display = ("order", "status_message", "updated_at")
    list_filter = ("updated_at",)
    search_fields = ("order__id", "status_message")
    autocomplete_fields = ("order",)
