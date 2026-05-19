from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from . import api
from .models import (
    MercadoLibreCredential,
    MercadoLibreListing,
    MercadoLibreOrder,
    MercadoLibreOrderItem,
)


@admin.register(MercadoLibreCredential)
class CredentialAdmin(admin.ModelAdmin):
    list_display = ("nickname", "user_id", "site_id", "expires_at", "connected_at", "reconnect_btn")
    readonly_fields = (
        "user_id", "nickname", "site_id", "access_token", "refresh_token",
        "expires_at", "connected_at", "updated_at",
    )
    actions = ["sync_now"]

    def has_add_permission(self, request):
        # Las credenciales solo se crean por OAuth, no a mano
        return False

    def reconnect_btn(self, obj):
        return mark_safe('<a class="button" href="/mercadolibre/connect/" style="background:#3483fa;color:#fff;padding:5px 12px;border-radius:4px;text-decoration:none;">Reconectar</a>')
    reconnect_btn.short_description = "Conexión"

    @admin.action(description="Sincronizar pedidos y publicaciones")
    def sync_now(self, request, queryset):
        for cred in queryset:
            try:
                orders = api.sync_orders(cred)
                listings = api.sync_listings(cred)
                self.message_user(
                    request,
                    f"{cred.nickname or cred.user_id}: {orders} pedidos / {listings} publicaciones.",
                    level=messages.SUCCESS,
                )
            except Exception as exc:
                self.message_user(
                    request,
                    f"{cred.nickname or cred.user_id}: error — {exc}",
                    level=messages.ERROR,
                )

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        if not MercadoLibreCredential.objects.exists():
            messages.warning(
                request,
                mark_safe(
                    '<strong>No hay cuenta conectada.</strong> '
                    '<a href="/mercadolibre/connect/" '
                    'style="background:#3483fa;color:#fff;padding:6px 14px;'
                    'border-radius:4px;text-decoration:none;margin-left:8px;'
                    'display:inline-block;font-weight:600;">'
                    '🔗 Conectar con Mercado Libre</a>'
                ),
            )
        return super().changelist_view(request, extra_context)


class OrderItemInline(admin.TabularInline):
    model = MercadoLibreOrderItem
    extra = 0
    readonly_fields = ("item_id", "title", "quantity", "unit_price")
    can_delete = False


@admin.register(MercadoLibreOrder)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("ml_id", "date_created", "status", "buyer_nickname", "total_amount",
                    "currency_id", "shipping_status", "tracking_number")
    list_filter = ("status", "shipping_status", "currency_id")
    search_fields = ("ml_id", "buyer_nickname", "tracking_number")
    date_hierarchy = "date_created"
    fieldsets = (
        ("Datos del pedido", {
            "fields": ("ml_id", "status", "date_created", "date_closed", "total_amount",
                       "currency_id", "buyer_nickname", "buyer_id"),
        }),
        ("Envío", {
            "fields": ("shipping_status", "shipping_id", "tracking_number",
                       "tracking_carrier", "pushed_tracking_at"),
            "description": "Si llenas <strong>tracking_number</strong> y guardas, "
                           "se envía automáticamente a Mercado Libre (siempre que "
                           "el pedido tenga shipping_id).",
        }),
        ("Sistema", {"fields": ("synced_at", "raw"), "classes": ("collapse",)}),
    )
    readonly_fields = ("ml_id", "status", "date_created", "date_closed", "total_amount",
                       "currency_id", "buyer_nickname", "buyer_id", "shipping_status",
                       "shipping_id", "pushed_tracking_at", "synced_at", "raw")
    inlines = [OrderItemInline]


@admin.register(MercadoLibreListing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("ml_id", "title", "producto_link", "price", "available_quantity",
                    "sold_quantity", "status", "permalink_link")
    list_filter = ("status",)
    search_fields = ("ml_id", "title", "producto__nombre")
    autocomplete_fields = ("producto",)
    fields = ("ml_id", "producto", "title", "price", "currency_id", "available_quantity",
              "sold_quantity", "last_pushed_stock", "status", "permalink", "thumbnail",
              "listing_type_id", "synced_at", "raw")
    readonly_fields = ("ml_id", "title", "price", "currency_id", "available_quantity",
                       "sold_quantity", "last_pushed_stock", "status", "permalink",
                       "thumbnail", "listing_type_id", "synced_at", "raw")
    actions = ["auto_link_by_title"]

    def permalink_link(self, obj):
        if obj.permalink:
            return format_html('<a href="{}" target="_blank" rel="noopener">Ver en ML →</a>', obj.permalink)
        return "—"
    permalink_link.short_description = "Link público"

    def producto_link(self, obj):
        if not obj.producto:
            return format_html('<span style="color:#999;">— sin enlazar —</span>')
        url = f"/admin/tienda/producto/{obj.producto.pk}/change/"
        return format_html('<a href="{}">{}</a>', url, obj.producto.nombre)
    producto_link.short_description = "Producto local"

    @admin.action(description="Auto-enlazar al producto local con título parecido")
    def auto_link_by_title(self, request, queryset):
        from tienda.models import Producto
        linked = 0
        for listing in queryset.filter(producto__isnull=True):
            # Busca por coincidencia exacta primero, luego case-insensitive
            match = (
                Producto.objects.filter(nombre__iexact=listing.title).first()
                or Producto.objects.filter(nombre__icontains=listing.title[:30]).first()
            )
            if match:
                listing.producto = match
                listing.save(update_fields=["producto"])
                linked += 1
        self.message_user(request, f"Enlazadas {linked} publicaciones automáticamente.", messages.SUCCESS)
