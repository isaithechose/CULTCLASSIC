from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.db.models import Count, Sum
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html

from .skydrop import SkydropError, create_shipment, quote_order, sync_shipment
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


@admin.action(description="Cotizar envío con Skydrop")
def cotizar_con_skydrop(modeladmin, request, queryset):
    procesados = 0
    errores = 0
    for order in queryset:
        try:
            result = quote_order(order)
            best_rate = result["best_rate"]
            order.skydrop_quotation_id = result["quotation_id"]
            order.skydrop_rate_id = best_rate["id"]
            order.skydrop_carrier = best_rate["carrier"]
            order.skydrop_service = best_rate["service"]
            order.shipping_quote_amount = best_rate["amount"]
            order.shipping_quote_currency = "MXN"
            order.skydrop_last_payload = result["payload"]
            order.skydrop_last_error = ""
            order.save()
            order.shipping_updates.create(
                status_message=(
                    f"Cotización Skydrop lista. "
                    f"{best_rate['carrier']} / {best_rate['service']} - ${best_rate['amount']} MXN."
                )
            )
            procesados += 1
        except Exception as exc:
            order.skydrop_last_error = str(exc)
            order.save(update_fields=["skydrop_last_error"])
            errores += 1
    if procesados:
        modeladmin.message_user(request, f"{procesados} pedidos cotizados en Skydrop.")
    if errores:
        modeladmin.message_user(
            request,
            f"{errores} pedidos fallaron al cotizar. Revisa el campo 'skydrop_last_error' en cada orden.",
            level="error",
        )


@admin.action(description="Crear guía en Skydrop")
def crear_guia_skydrop(modeladmin, request, queryset):
    procesados = 0
    errores = 0
    for order in queryset:
        try:
            result = create_shipment(order, order.skydrop_rate_id)
            order.skydrop_shipment_id = result["shipment_id"]
            order.skydrop_label_url = result["label_url"]
            order.skydrop_tracking_url = result["tracking_url"]
            order.tracking_number = result["tracking_number"]
            order.skydrop_carrier = result["carrier"] or order.skydrop_carrier
            order.skydrop_service = result["service"] or order.skydrop_service
            order.skydrop_last_payload = result["payload"]
            order.skydrop_last_error = ""
            order.shipping_status = "Shipped" if result["tracking_number"] else order.shipping_status
            order.save()
            order.shipping_updates.create(
                status_message=(
                    f"Guía creada en Skydrop. "
                    f"Tracking: {result['tracking_number'] or 'pendiente'}."
                )
            )
            procesados += 1
        except Exception as exc:
            order.skydrop_last_error = str(exc)
            order.save(update_fields=["skydrop_last_error"])
            errores += 1
    if procesados:
        modeladmin.message_user(
            request,
            f"{procesados} guías creadas. Revisa el detalle del pedido para tracking o errores.",
        )
    if errores:
        modeladmin.message_user(
            request,
            f"{errores} pedidos fallaron al crear guía. Revisa 'skydrop_last_error'.",
            level="error",
        )


@admin.action(description="Sincronizar tracking desde Skydrop")
def sincronizar_skydrop(modeladmin, request, queryset):
    procesados = 0
    errores = 0
    for order in queryset:
        try:
            result = sync_shipment(order)
            order.tracking_number = result.get("tracking_number") or order.tracking_number
            order.skydrop_tracking_url = result.get("tracking_url") or order.skydrop_tracking_url
            order.skydrop_carrier = result.get("carrier") or order.skydrop_carrier
            order.skydrop_service = result.get("service") or order.skydrop_service
            if result.get("status"):
                normalized = result["status"].lower()
                if "deliver" in normalized:
                    order.shipping_status = "Delivered"
                elif any(token in normalized for token in ["transit", "ship", "pickup", "label"]):
                    order.shipping_status = "Shipped"
                else:
                    order.shipping_status = "Processing"
            order.skydrop_last_payload = result.get("payload")
            order.skydrop_last_error = ""
            order.save()
            procesados += 1
        except Exception as exc:
            order.skydrop_last_error = str(exc)
            order.save(update_fields=["skydrop_last_error"])
            errores += 1
    if procesados:
        modeladmin.message_user(request, f"{procesados} pedidos sincronizados con Skydrop.")
    if errores:
        modeladmin.message_user(
            request,
            f"{errores} pedidos fallaron al sincronizar. Revisa 'skydrop_last_error'.",
            level="error",
        )


class SubcategoriaInline(admin.TabularInline):
    model = Subcategoria
    extra = 0
    fields = ("nombre", "descripcion")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ("product", "talla", "color", "diseño_pecho", "diseño_espalda", "quantity", "price")
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
        "skydrop_badge",
        "created_at",
    )
    list_filter = ("status", "shipping_status", "created_at")
    search_fields = ("id", "customer__username", "customer__email", "tracking_number")
    readonly_fields = (
        "created_at",
        "total_amount",
        "shipping_address_preview",
        "skydrop_readiness",
        "skydrop_actions_panel",
        "skydrop_summary",
    )
    autocomplete_fields = ("customer",)
    inlines = [OrderItemInline, ShippingAddressInline, ShippingUpdateInline]
    actions = [
        marcar_pedidos_completados,
        marcar_pedidos_enviados,
        cotizar_con_skydrop,
        crear_guia_skydrop,
        sincronizar_skydrop,
    ]
    fieldsets = (
        ("Pedido", {
            "fields": ("customer", "status", "shipping_status", "tracking_number", "created_at")
        }),
        ("Direccion", {
            "fields": ("shipping_address_preview",)
        }),
        ("Skydrop", {
            "fields": (
                "skydrop_readiness",
                "skydrop_actions_panel",
                "skydrop_summary",
                "skydrop_quotation_id",
                "skydrop_rate_id",
                "skydrop_shipment_id",
                "skydrop_label_url",
                "skydrop_tracking_url",
                "skydrop_carrier",
                "skydrop_service",
                "shipping_quote_amount",
                "shipping_quote_currency",
                "skydrop_last_error",
            )
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:order_id>/skydrop/quote/",
                self.admin_site.admin_view(self.quote_order_view),
                name="tienda_order_skydrop_quote",
            ),
            path(
                "<int:order_id>/skydrop/shipment/",
                self.admin_site.admin_view(self.create_shipment_view),
                name="tienda_order_skydrop_shipment",
            ),
            path(
                "<int:order_id>/skydrop/sync/",
                self.admin_site.admin_view(self.sync_shipment_view),
                name="tienda_order_skydrop_sync",
            ),
        ]
        return custom_urls + urls

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

    @admin.display(description="Skydrop")
    def skydrop_badge(self, obj):
        if obj.skydrop_shipment_id:
            return format_html(
                '<span style="padding:0.25rem 0.55rem;border-radius:999px;background:#2d8a4b22;color:#2d8a4b;font-weight:700;">Guía creada</span>'
            )
        if obj.skydrop_quotation_id:
            return format_html(
                '<span style="padding:0.25rem 0.55rem;border-radius:999px;background:#2f67b022;color:#2f67b0;font-weight:700;">Cotizado</span>'
            )
        return format_html(
            '<span style="padding:0.25rem 0.55rem;border-radius:999px;background:#66666622;color:#888;font-weight:700;">Sin conectar</span>'
        )

    @admin.display(description="Direccion guardada")
    def shipping_address_preview(self, obj):
        address = getattr(obj, "shipping_address", None)
        if not address:
            return "El pedido todavia no tiene direccion de envio."
        lines = [
            f"<strong>{address.address_line1}</strong>",
        ]
        if address.address_line2:
            lines.append(address.address_line2)
        lines.append(f"{address.city}, {address.state}, {address.postal_code}")
        lines.append(address.country)
        if address.phone:
            lines.append(f"Tel: {address.phone}")
        return format_html("<br>".join(lines))

    @admin.display(description="Checklist Skydrop")
    def skydrop_readiness(self, obj):
        address = getattr(obj, "shipping_address", None)
        checks = [
            ("Direccion", bool(address)),
            ("Telefono", bool(address and address.phone)),
            ("Items", obj.items.exists()),
            ("Cotizacion", bool(obj.shipping_quote_amount)),
            ("Guia", bool(obj.skydrop_shipment_id)),
        ]
        chips = []
        for label, ok in checks:
            color = "#2d8a4b" if ok else "#a38b5d"
            bg = "#2d8a4b22" if ok else "#a38b5d22"
            text = "Listo" if ok else "Pendiente"
            chips.append(
                f'<span style="display:inline-flex;margin:0 0.45rem 0.45rem 0;padding:0.3rem 0.6rem;border-radius:999px;background:{bg};color:{color};font-weight:700;">{label}: {text}</span>'
            )
        return format_html("".join(chips))

    @admin.display(description="Acciones Skydrop")
    def skydrop_actions_panel(self, obj):
        if not obj.pk:
            return "Guarda el pedido para usar acciones de Skydrop."
        quote_url = reverse("admin:tienda_order_skydrop_quote", args=[obj.pk])
        shipment_url = reverse("admin:tienda_order_skydrop_shipment", args=[obj.pk])
        sync_url = reverse("admin:tienda_order_skydrop_sync", args=[obj.pk])
        return format_html(
            '''
            <div style="display:flex;flex-wrap:wrap;gap:0.6rem;">
                <a class="button" href="{}">Cotizar envio</a>
                <a class="button" href="{}">Crear guia</a>
                <a class="button" href="{}">Sincronizar tracking</a>
            </div>
            ''',
            quote_url,
            shipment_url,
            sync_url,
        )

    @admin.display(description="Resumen Skydrop")
    def skydrop_summary(self, obj):
        rows = []
        if obj.skydrop_carrier or obj.skydrop_service:
            rows.append(f"{obj.skydrop_carrier or '-'} / {obj.skydrop_service or '-'}")
        if obj.shipping_quote_amount:
            rows.append(f"Cotizacion: ${obj.shipping_quote_amount} {obj.shipping_quote_currency or 'MXN'}")
        if obj.tracking_number:
            rows.append(f"Tracking: {obj.tracking_number}")
        if obj.skydrop_label_url:
            rows.append(f'<a href="{obj.skydrop_label_url}" target="_blank">Abrir guía</a>')
        if obj.skydrop_tracking_url:
            rows.append(f'<a href="{obj.skydrop_tracking_url}" target="_blank">Abrir tracking</a>')
        if obj.skydrop_last_error:
            rows.append(f'<span style="color:#d46b6b;">{obj.skydrop_last_error}</span>')
        return format_html("<br>".join(rows) if rows else "Sin datos de Skydrop.")

    def _redirect_to_change(self, order_id):
        return HttpResponseRedirect(reverse("admin:tienda_order_change", args=[order_id]))

    def quote_order_view(self, request, order_id):
        order = self.get_object(request, order_id)
        if not order:
            self.message_user(request, "No encontramos ese pedido.", level=messages.ERROR)
            return self._redirect_to_change(order_id)
        try:
            result = quote_order(order)
            best_rate = result["best_rate"]
            order.skydrop_quotation_id = result["quotation_id"]
            order.skydrop_rate_id = best_rate["id"]
            order.skydrop_carrier = best_rate["carrier"]
            order.skydrop_service = best_rate["service"]
            order.shipping_quote_amount = best_rate["amount"]
            order.shipping_quote_currency = "MXN"
            order.skydrop_last_payload = result["payload"]
            order.skydrop_last_error = ""
            order.save()
            order.shipping_updates.create(
                status_message=(
                    f"Cotizacion Skydrop lista. "
                    f"{best_rate['carrier']} / {best_rate['service']} - ${best_rate['amount']} MXN."
                )
            )
            self.message_user(request, "Cotizacion lista y guardada en el pedido.")
        except Exception as exc:
            order.skydrop_last_error = str(exc)
            order.save(update_fields=["skydrop_last_error"])
            self.message_user(request, str(exc), level=messages.ERROR)
        return self._redirect_to_change(order_id)

    def create_shipment_view(self, request, order_id):
        order = self.get_object(request, order_id)
        if not order:
            self.message_user(request, "No encontramos ese pedido.", level=messages.ERROR)
            return self._redirect_to_change(order_id)
        try:
            result = create_shipment(order, order.skydrop_rate_id)
            order.skydrop_shipment_id = result["shipment_id"]
            order.skydrop_label_url = result["label_url"]
            order.skydrop_tracking_url = result["tracking_url"]
            order.tracking_number = result["tracking_number"]
            order.skydrop_carrier = result["carrier"] or order.skydrop_carrier
            order.skydrop_service = result["service"] or order.skydrop_service
            order.skydrop_last_payload = result["payload"]
            order.skydrop_last_error = ""
            order.shipping_status = "Shipped" if result["tracking_number"] else order.shipping_status
            order.save()
            order.shipping_updates.create(
                status_message=f"Guia creada en Skydrop. Tracking: {result['tracking_number'] or 'pendiente'}."
            )
            self.message_user(request, "Guia creada y tracking actualizado.")
        except Exception as exc:
            order.skydrop_last_error = str(exc)
            order.save(update_fields=["skydrop_last_error"])
            self.message_user(request, str(exc), level=messages.ERROR)
        return self._redirect_to_change(order_id)

    def sync_shipment_view(self, request, order_id):
        order = self.get_object(request, order_id)
        if not order:
            self.message_user(request, "No encontramos ese pedido.", level=messages.ERROR)
            return self._redirect_to_change(order_id)
        try:
            result = sync_shipment(order)
            order.tracking_number = result.get("tracking_number") or order.tracking_number
            order.skydrop_tracking_url = result.get("tracking_url") or order.skydrop_tracking_url
            order.skydrop_carrier = result.get("carrier") or order.skydrop_carrier
            order.skydrop_service = result.get("service") or order.skydrop_service
            if result.get("status"):
                normalized = result["status"].lower()
                if "deliver" in normalized:
                    order.shipping_status = "Delivered"
                elif any(token in normalized for token in ["transit", "ship", "pickup", "label"]):
                    order.shipping_status = "Shipped"
                else:
                    order.shipping_status = "Processing"
            order.skydrop_last_payload = result.get("payload")
            order.skydrop_last_error = ""
            order.save()
            self.message_user(request, "Tracking sincronizado.")
        except Exception as exc:
            order.skydrop_last_error = str(exc)
            order.save(update_fields=["skydrop_last_error"])
            self.message_user(request, str(exc), level=messages.ERROR)
        return self._redirect_to_change(order_id)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "product", "talla", "color", "quantity", "price")
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
    list_display = ("order", "phone", "city", "state", "country", "postal_code")
    search_fields = ("order__id", "phone", "city", "state", "country", "postal_code")
    autocomplete_fields = ("order",)


@admin.register(ShippingUpdate)
class ShippingUpdateAdmin(admin.ModelAdmin):
    list_display = ("order", "status_message", "updated_at")
    list_filter = ("updated_at",)
    search_fields = ("order__id", "status_message")
    autocomplete_fields = ("order",)
