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

    def reconnect_btn(self, obj):
        return mark_safe('<a class="button" href="/mercadolibre/connect/">Reconectar</a>')
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
            extra_context["title"] = "Conectar Mercado Libre"
            messages.info(
                request,
                mark_safe(
                    'Aún no hay cuenta conectada. '
                    '<a href="/mercadolibre/connect/" class="button">Conectar con Mercado Libre</a>'
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
                    "currency_id", "shipping_status")
    list_filter = ("status", "shipping_status", "currency_id")
    search_fields = ("ml_id", "buyer_nickname")
    date_hierarchy = "date_created"
    readonly_fields = ("ml_id", "status", "date_created", "date_closed", "total_amount",
                       "currency_id", "buyer_nickname", "buyer_id", "shipping_status",
                       "synced_at", "raw")
    inlines = [OrderItemInline]


@admin.register(MercadoLibreListing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("ml_id", "title", "price", "available_quantity", "sold_quantity",
                    "status", "permalink_link")
    list_filter = ("status",)
    search_fields = ("ml_id", "title")
    readonly_fields = ("ml_id", "title", "price", "currency_id", "available_quantity",
                       "sold_quantity", "status", "permalink", "thumbnail",
                       "listing_type_id", "synced_at", "raw")

    def permalink_link(self, obj):
        if obj.permalink:
            return format_html('<a href="{}" target="_blank" rel="noopener">Ver en ML →</a>', obj.permalink)
        return "—"
    permalink_link.short_description = "Link público"
