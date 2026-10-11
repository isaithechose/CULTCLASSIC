"""Admin de pedidos: ordenes, punto de venta, envios, promociones y devoluciones."""

from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django import forms
from django.db import transaction
from django.db.models import Count
from django.forms import formset_factory
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from ..skydrop import create_shipment, map_skydrop_status, quote_order, sync_shipment
from ..models import (
    Carrito,
    Order,
    OrderItem,
    OrderReturn,
    Producto,
    ProductVariant,
    PromoCode,
    Reseña,
    ShippingAddress,
    ShippingUpdate,
    record_inventory_movement,
)
from decimal import Decimal
from django.utils import timezone
from ..utils.variant_image_assignment import existing_thumbnail_or_image_name
from ..models import NewsletterSubscriber

from .comun import (
    _money,
    _post_order_journal_entry,
    _product_unit_cost,
    _variant_sale_price,
    _variant_unit_cost,
)


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
                order.shipping_status = map_skydrop_status(result["status"])
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


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ("product", "talla", "color", "diseño_pecho", "diseño_espalda", "quantity", "price", "unit_cost")
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


class PointOfSaleHeaderForm(forms.Form):
    payment_method = forms.ChoiceField(
        choices=Order.PAYMENT_METHOD_CHOICES,
        initial="cash",
        label="Método de pago",
    )
    discount_amount = forms.DecimalField(
        min_value=0,
        decimal_places=2,
        max_digits=10,
        required=False,
        initial=0,
        label="Descuento",
    )
    internal_note = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
        label="Nota interna",
    )
    sale_date = forms.DateField(
        required=False,
        label="Fecha de la venta",
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Deja en blanco para usar hoy.",
    )


class OrderReturnInline(admin.TabularInline):
    model = OrderReturn
    extra = 0
    fields = ("reason", "status", "refund_amount", "restock", "notes")
    readonly_fields = ()
    show_change_link = True


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_select_related = ("customer", "cashier")
    list_per_page = 50
    show_full_result_count = False
    list_display = (
        "id",
        "customer",
        "sales_channel_badge",
        "payment_method",
        "status_badge",
        "shipping_badge",
        "total_items",
        "total_amount",
        "skydrop_badge",
        "created_at",
    )
    list_filter = ("status", "shipping_status", "sales_channel", "payment_method", "created_at")
    search_fields = ("id", "customer__username", "customer__email", "tracking_number")
    readonly_fields = (
        "created_at",
        "total_amount",
        "shipping_address_preview",
        "skydrop_readiness",
        "skydrop_actions_panel",
        "skydrop_summary",
    )
    autocomplete_fields = ("customer", "cashier")
    inlines = [OrderItemInline, ShippingAddressInline, ShippingUpdateInline, OrderReturnInline]
    actions = [
        marcar_pedidos_completados,
        marcar_pedidos_enviados,
        cotizar_con_skydrop,
        crear_guia_skydrop,
        sincronizar_skydrop,
    ]
    fieldsets = (
        ("Pedido", {
            "fields": (
                "customer",
                "cashier",
                "sales_channel",
                "payment_method",
                "discount_amount",
                "status",
                "shipping_status",
                "tracking_number",
                "internal_note",
                "created_at",
            )
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
                "point-of-sale/",
                self.admin_site.admin_view(self.point_of_sale_view),
                name="tienda_order_point_of_sale",
            ),
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
        return (
            super()
            .get_queryset(request)
            .annotate(items_total=Count("items", distinct=True))
            .prefetch_related("items")
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

    @admin.display(ordering="sales_channel", description="Canal")
    def sales_channel_badge(self, obj):
        colors = {
            "online": ("#1d4ed8", "#dbeafe"),
            "pos": ("#05603a", "#d1fadf"),
            "manual": ("#6b7280", "#ececf3"),
        }
        color, bg = colors.get(obj.sales_channel, ("#6b7280", "#ececf3"))
        return format_html(
            '<span style="display:inline-block;padding:0.25rem 0.55rem;border-radius:999px;background:{};color:{};font-weight:700;">{}</span>',
            bg,
            color,
            obj.get_sales_channel_display(),
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
            rows.append(format_html("{} / {}", obj.skydrop_carrier or "-", obj.skydrop_service or "-"))
        if obj.shipping_quote_amount:
            rows.append(format_html("Cotizacion: ${} {}", obj.shipping_quote_amount, obj.shipping_quote_currency or "MXN"))
        if obj.tracking_number:
            rows.append(format_html("Tracking: {}", obj.tracking_number))
        if obj.skydrop_label_url:
            rows.append(format_html('<a href="{}" target="_blank">Abrir guía</a>', obj.skydrop_label_url))
        if obj.skydrop_tracking_url:
            rows.append(format_html('<a href="{}" target="_blank">Abrir tracking</a>', obj.skydrop_tracking_url))
        if obj.skydrop_last_error:
            rows.append(format_html('<span style="color:#d46b6b;">{}</span>', obj.skydrop_last_error))
        if not rows:
            return "Sin datos de Skydrop."
        return format_html("<br>".join(str(row) for row in rows))

    def _redirect_to_change(self, order_id):
        return HttpResponseRedirect(reverse("admin:tienda_order_change", args=[order_id]))

    def point_of_sale_view(self, request):
        variants = list(
            ProductVariant.objects.filter(activo=True, product__disponible=True)
            .select_related("product")
            .order_by("product__nombre", "color", "talla")
        )
        general_products = list(
            Producto.objects.filter(disponible=True, variants__isnull=True)
            .order_by("nombre")
        )

        class POSLineForm(forms.Form):
            item_key = forms.CharField(widget=forms.HiddenInput)
            quantity = forms.IntegerField(min_value=0, required=False, initial=0, label="Cantidad")
            unit_price = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10, required=False, label="Precio")

        POSLineFormSet = formset_factory(POSLineForm, extra=0)
        catalog_rows = []
        initial = []

        for variant in variants:
            key = f"v:{variant.id}"
            image_name = existing_thumbnail_or_image_name(variant.display_image_name)
            image_url = f"{settings.MEDIA_URL.rstrip('/')}/{image_name.lstrip('/')}" if image_name else ""
            initial.append({"item_key": key, "quantity": 0, "unit_price": variant.product.precio})
            catalog_rows.append(
                {
                    "key": key,
                    "sku": variant.sku or "",
                    "image_url": image_url,
                    "label": variant.product.nombre,
                    "detail": f"{variant.color} / {variant.talla}",
                    "stock": variant.stock,
                    "unit_cost": _variant_unit_cost(variant),
                    "unit_price": _variant_sale_price(variant),
                }
            )

        for product in general_products:
            key = f"p:{product.id}"
            image_url = product.imagen.url if product.imagen else ""
            initial.append({"item_key": key, "quantity": 0, "unit_price": product.precio})
            catalog_rows.append(
                {
                    "key": key,
                    "sku": "",
                    "image_url": image_url,
                    "label": product.nombre,
                    "detail": "Stock general",
                    "stock": product.stock,
                    "unit_cost": _product_unit_cost(product),
                    "unit_price": _money(product.precio),
                }
            )

        # Facetas: categoria / subcategoria / color / talla, con contadores
        import re as _re, unicodedata as _ud
        from collections import Counter as _Counter
        def _facet_slug(s):
            if not s:
                return ""
            s = _ud.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
            return _re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

        for row, variant in zip(catalog_rows[:len(variants)], variants):
            prod = variant.product
            row["category_name"]    = prod.categoria.nombre if prod.categoria else ""
            row["category_slug"]    = _facet_slug(row["category_name"])
            row["subcategory_name"] = prod.subcategoria.nombre if prod.subcategoria else ""
            row["subcategory_slug"] = _facet_slug(row["subcategory_name"])
            row["color_name"]       = variant.color or ""
            row["color_slug"]       = _facet_slug(variant.color)
            row["size_name"]        = variant.talla or ""
            row["size_slug"]        = _facet_slug(variant.talla)
        for row, product in zip(catalog_rows[len(variants):], general_products):
            row["category_name"]    = product.categoria.nombre if product.categoria else ""
            row["category_slug"]    = _facet_slug(row["category_name"])
            row["subcategory_name"] = product.subcategoria.nombre if product.subcategoria else ""
            row["subcategory_slug"] = _facet_slug(row["subcategory_name"])
            row["color_name"]       = ""
            row["color_slug"]       = ""
            row["size_name"]        = ""
            row["size_slug"]        = ""

        # Agrupar por slug (ignorando mayusculas/acentos): BLACK y black son el mismo chip
        _cats = _Counter(); _cats_names = {}
        _subs = {}; _subs_names = {}
        _cols = _Counter(); _cols_names = {}
        _szs = _Counter(); _szs_names = {}
        for row in catalog_rows:
            if row["category_slug"]:
                _cats[row["category_slug"]] += 1
                _cats_names.setdefault(row["category_slug"], row["category_name"])
                if row["subcategory_slug"]:
                    _subs.setdefault(row["category_slug"], _Counter())
                    _subs[row["category_slug"]][row["subcategory_slug"]] += 1
                    _subs_names.setdefault((row["category_slug"], row["subcategory_slug"]), row["subcategory_name"])
            if row["color_slug"]:
                _cols[row["color_slug"]] += 1
                _cols_names.setdefault(row["color_slug"], row["color_name"].title() if row["color_name"] else row["color_slug"])
            if row["size_slug"]:
                _szs[row["size_slug"]] += 1
                _szs_names.setdefault(row["size_slug"], row["size_name"])

        facet_categories = [{"slug": s, "name": _cats_names[s], "count": c} for s, c in _cats.most_common()]
        facet_subcategories = {
            cat: [{"slug": s, "name": _subs_names[(cat, s)], "count": c} for s, c in cc.most_common()]
            for cat, cc in _subs.items()
        }
        facet_colors = sorted(
            [{"slug": s, "name": _cols_names[s], "count": c} for s, c in _cols.items()],
            key=lambda x: (-x["count"], x["name"])
        )
        _SIZE_ORDER = {"xs":1,"s":2,"m":3,"l":4,"xl":5,"2xl":6,"xxl":6,"3xl":7,"xxxl":7,"unica":10,"unitalla":10}
        def _size_rank(item):
            return (_SIZE_ORDER.get(item["slug"], 20), item["name"])
        facet_sizes = sorted(
            [{"slug": s, "name": _szs_names[s], "count": c} for s, c in _szs.items()],
            key=_size_rank
        )

        if request.method == "POST":
            header_form = PointOfSaleHeaderForm(request.POST)
            formset = POSLineFormSet(request.POST, prefix="pos")
            if header_form.is_valid() and formset.is_valid():
                variant_map = {f"v:{variant.id}": variant for variant in variants}
                product_map = {f"p:{product.id}": product for product in general_products}
                lines = []

                for form in formset:
                    item_key = form.cleaned_data.get("item_key")
                    quantity = form.cleaned_data.get("quantity") or 0
                    unit_price = form.cleaned_data.get("unit_price")
                    if quantity <= 0:
                        continue

                    if item_key in variant_map:
                        variant = variant_map[item_key]
                        if quantity > variant.stock:
                            form.add_error("quantity", f"Solo hay {variant.stock} piezas.")
                            continue
                        lines.append(
                            {
                                "product": variant.product,
                                "variant": variant,
                                "quantity": int(quantity),
                                "unit_price": unit_price if unit_price is not None else _variant_sale_price(variant),
                                "unit_cost": _variant_unit_cost(variant),
                                "talla": variant.talla,
                                "color": variant.color,
                            }
                        )
                    elif item_key in product_map:
                        product = product_map[item_key]
                        if quantity > product.stock:
                            form.add_error("quantity", f"Solo hay {product.stock} piezas.")
                            continue
                        lines.append(
                            {
                                "product": product,
                                "variant": None,
                                "quantity": int(quantity),
                                "unit_price": unit_price if unit_price is not None else _money(product.precio),
                                "unit_cost": _product_unit_cost(product),
                                "talla": "",
                                "color": "",
                            }
                        )

                line_errors = any(form.errors for form in formset)
                if line_errors:
                    self.message_user(request, "Revisa cantidades: alguna línea excede el stock.", level=messages.ERROR)
                elif not lines:
                    self.message_user(request, "Captura al menos una cantidad para vender.", level=messages.WARNING)
                else:
                    try:
                        with transaction.atomic():
                            order = Order.objects.create(
                                customer=None,
                                status="Completed",
                                shipping_status="Delivered",
                                sales_channel="pos",
                                payment_method=header_form.cleaned_data["payment_method"],
                                discount_amount=header_form.cleaned_data.get("discount_amount") or Decimal("0.00"),
                                internal_note=header_form.cleaned_data.get("internal_note", ""),
                                cashier=request.user if request.user.is_authenticated else None,
                            )
                            sale_date = header_form.cleaned_data.get("sale_date")
                            if sale_date and sale_date != timezone.localdate():
                                # El dia se elige en hora local; now() viene en UTC.
                                backdated = timezone.localtime().replace(
                                    year=sale_date.year,
                                    month=sale_date.month,
                                    day=sale_date.day,
                                )
                                Order.objects.filter(pk=order.pk).update(created_at=backdated)
                                order.refresh_from_db()
                            for line in lines:
                                OrderItem.objects.create(
                                    order=order,
                                    product=line["product"],
                                    quantity=line["quantity"],
                                    price=line["unit_price"],
                                    unit_cost=line["unit_cost"],
                                    talla=line["talla"],
                                    color=line["color"],
                                )
                                record_inventory_movement(
                                    product=line["product"],
                                    variant=line["variant"],
                                    order=order,
                                    movement_type="sale",
                                    quantity_change=-line["quantity"],
                                    note="Venta registrada desde punto de venta.",
                                    created_by=request.user if request.user.is_authenticated else None,
                                    metadata={
                                        "sales_channel": "pos",
                                        "payment_method": header_form.cleaned_data["payment_method"],
                                        "unit_price": str(line["unit_price"]),
                                    },
                                )
                            _post_order_journal_entry(
                                order,
                                lines,
                                created_by=request.user if request.user.is_authenticated else None,
                            )
                    except ValueError as exc:
                        self.message_user(request, str(exc), level=messages.ERROR)
                    else:
                        self.message_user(
                            request,
                            f"Venta POS registrada. Pedido #{order.id} por ${order.total_price:.2f}.",
                            level=messages.SUCCESS,
                        )
                        return HttpResponseRedirect(reverse("admin:tienda_order_change", args=[order.id]))
        else:
            header_form = PointOfSaleHeaderForm(initial={"sale_date": timezone.localdate()})
            formset = POSLineFormSet(initial=initial, prefix="pos")

        rows = []
        for row, form in zip(catalog_rows, formset.forms):
            row["form"] = form
            rows.append(row)

        import json as _json
        context = dict(
            self.admin_site.each_context(request),
            title="Punto de venta",
            subtitle="Venta rápida con descuento de inventario y pedido completado",
            header_form=header_form,
            formset=formset,
            rows=rows,
            facet_categories=facet_categories,
            facet_subcategories_json=_json.dumps(facet_subcategories),
            facet_colors=facet_colors,
            facet_sizes=facet_sizes,
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/point_of_sale.html", context)

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
                order.shipping_status = map_skydrop_status(result["status"])
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
    list_select_related = ("order", "product")
    list_per_page = 50
    show_full_result_count = False
    list_display = ("order", "product", "talla", "color", "quantity", "price", "unit_cost_display", "profit_display")
    list_filter = ("order__status", "order__shipping_status")
    search_fields = ("order__id", "product__nombre")
    autocomplete_fields = ("order", "product")

    @admin.display(description="Costo unitario", ordering="unit_cost")
    def unit_cost_display(self, obj):
        if obj.unit_cost is None:
            # venta anterior al congelado: se muestra el costo actual del producto
            return format_html(
                '<span title="Venta anterior al costo congelado: se usa el costo actual del producto." '
                'style="color:#888;">${}  ~</span>',
                f"{obj.effective_unit_cost:.2f}",
            )
        return f"${obj.unit_cost:.2f}"

    @admin.display(description="Utilidad")
    def profit_display(self, obj):
        profit = obj.profit_total
        color = "#2dbe6c" if profit >= 0 else "#e05050"
        return format_html('<span style="color:{};font-weight:700;">${}</span>', color, f"{profit:.2f}")


@admin.register(Carrito)
class CarritoAdmin(admin.ModelAdmin):
    list_select_related = ("usuario", "producto")
    list_display = ("usuario", "producto", "cantidad", "subtotal_display")
    list_filter = ("usuario",)
    search_fields = ("usuario__username", "producto__nombre")
    autocomplete_fields = ("usuario", "producto")

    @admin.display(description="Subtotal")
    def subtotal_display(self, obj):
        return f"${obj.subtotal():.2f}"


@admin.register(Reseña)
class ResenaAdmin(admin.ModelAdmin):
    list_select_related = ("producto", "usuario")
    list_display = ("producto", "usuario", "calificacion", "fecha")
    list_filter = ("calificacion", "fecha", "producto")
    search_fields = ("producto__nombre", "usuario__username", "comentario")
    autocomplete_fields = ("producto", "usuario")


@admin.register(ShippingAddress)
class ShippingAddressAdmin(admin.ModelAdmin):
    list_select_related = ("order",)
    list_display = ("order", "phone", "city", "state", "country", "postal_code")
    search_fields = ("order__id", "phone", "city", "state", "country", "postal_code")
    autocomplete_fields = ("order",)


@admin.register(ShippingUpdate)
class ShippingUpdateAdmin(admin.ModelAdmin):
    list_select_related = ("order",)
    list_display = ("order", "status_message", "updated_at")
    list_filter = ("updated_at",)
    search_fields = ("order__id", "status_message")
    autocomplete_fields = ("order",)

@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "source", "coupon_sent", "created_at")
    list_filter = ("source", "coupon_sent", "created_at")
    search_fields = ("email",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)


# ─────────────────────────────────────────────
#  PROMO CODES
# ─────────────────────────────────────────────

@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = (
        "code", "discount_type_display", "discount_value_display",
        "min_purchase", "uses_count", "max_uses_display",
        "expiration_date", "active",
    )
    list_filter = ("active", "discount_type", "created_at")
    search_fields = ("code", "description")
    list_editable = ("active",)
    readonly_fields = ("uses_count", "created_at", "promo_stats")
    ordering = ("-created_at",)
    fieldsets = (
        ("Código", {"fields": ("code", "description", "active")}),
        ("Descuento", {"fields": ("discount_type", "discount_value", "min_purchase")}),
        ("Límites", {"fields": ("max_uses", "uses_count", "expiration_date")}),
        ("Estadísticas", {"fields": ("promo_stats", "created_at")}),
    )

    @admin.display(description="Tipo")
    def discount_type_display(self, obj):
        icons = {"percentage": "％", "fixed": "$", "free_shipping": "🚚"}
        return format_html("{} {}", icons.get(obj.discount_type, ""), obj.get_discount_type_display())

    @admin.display(description="Descuento")
    def discount_value_display(self, obj):
        if obj.discount_type == "percentage":
            return f"{obj.discount_value:.0f}%"
        if obj.discount_type == "fixed":
            return f"${obj.discount_value:.2f}"
        return "Envío gratis"

    @admin.display(description="Usos máx.")
    def max_uses_display(self, obj):
        return obj.max_uses if obj.max_uses is not None else "∞"

    @admin.display(description="Resumen de uso")
    def promo_stats(self, obj):
        if not obj.pk:
            return "Guarda el código primero."
        total_orders = obj.orders.count()
        total_discount = sum(o.discount_amount for o in obj.orders.all())
        return format_html(
            "<strong>{}</strong> órdenes · <strong>${:.2f}</strong> en descuentos aplicados",
            total_orders, total_discount,
        )

    @admin.action(description="Activar códigos seleccionados")
    def activar(self, request, queryset):
        queryset.update(active=True)
        self.message_user(request, f"{queryset.count()} código(s) activados.")

    @admin.action(description="Desactivar códigos seleccionados")
    def desactivar(self, request, queryset):
        queryset.update(active=False)
        self.message_user(request, f"{queryset.count()} código(s) desactivados.")

    @admin.action(description="Resetear conteo de usos")
    def resetear_usos(self, request, queryset):
        queryset.update(uses_count=0)
        self.message_user(request, f"Conteo reseteado en {queryset.count()} código(s).")

    actions = [activar, desactivar, resetear_usos]


# ─────────────────────────────────────────────
#  DEVOLUCIONES / RMA
# ─────────────────────────────────────────────

@admin.register(OrderReturn)
class OrderReturnAdmin(admin.ModelAdmin):
    list_display = (
        "id", "order_link", "reason_display", "status_badge",
        "refund_amount", "restock", "created_at",
    )
    list_filter = ("status", "reason", "restock", "created_at")
    search_fields = ("order__id", "notes")
    readonly_fields = ("created_at", "updated_at", "order_summary")
    ordering = ("-created_at",)
    fieldsets = (
        ("Devolución", {"fields": ("order", "order_summary", "reason", "status", "restock")}),
        ("Reembolso", {"fields": ("refund_amount", "notes")}),
        ("Fechas", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Orden", ordering="order__id")
    def order_link(self, obj):
        url = reverse("admin:tienda_order_change", args=[obj.order_id])
        return format_html('<a href="{}">#{}</a>', url, obj.order_id)

    @admin.display(description="Razón")
    def reason_display(self, obj):
        return obj.get_reason_display()

    @admin.display(description="Estado")
    def status_badge(self, obj):
        colors = {
            "requested": ("#92400e", "#fef3c7"),
            "approved":  ("#1e40af", "#dbeafe"),
            "received":  ("#065f46", "#d1fae5"),
            "restocked": ("#065f46", "#d1fae5"),
            "refunded":  ("#374151", "#f3f4f6"),
            "rejected":  ("#991b1b", "#fee2e2"),
        }
        fg, bg = colors.get(obj.status, ("#374151", "#f3f4f6"))
        return format_html(
            '<span style="padding:3px 10px;border-radius:999px;font-size:12px;'
            'font-weight:700;background:{};color:{}">{}</span>',
            bg, fg, obj.get_status_display(),
        )

    @admin.display(description="Resumen de la orden")
    def order_summary(self, obj):
        if not obj.order_id:
            return "—"
        order = obj.order
        items_html = "".join(
            f"<li>{i.product.nombre} × {i.quantity} (${i.price})</li>"
            for i in order.items.all()
        )
        return format_html(
            "<ul style='margin:0;padding-left:1.2rem'>{}</ul>"
            "<p style='margin:.5rem 0 0'>Total orden: <strong>${}</strong></p>",
            items_html, order.total_price,
        )

    @admin.action(description="Aprobar devoluciones seleccionadas")
    def aprobar(self, request, queryset):
        queryset.filter(status="requested").update(status="approved")
        self.message_user(request, "Devoluciones aprobadas.")

    @admin.action(description="Marcar como recibidas en bodega")
    def recibir(self, request, queryset):
        queryset.filter(status="approved").update(status="received")
        self.message_user(request, "Marcadas como recibidas.")

    @admin.action(description="Rechazar devoluciones seleccionadas")
    def rechazar(self, request, queryset):
        queryset.filter(status="requested").update(status="rejected")
        self.message_user(request, "Devoluciones rechazadas.")

    actions = [aprobar, recibir, rechazar]
