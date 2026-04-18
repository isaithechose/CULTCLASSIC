from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django import forms
from django.db.models import Count, Sum
from django.forms import formset_factory
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from .skydrop import SkydropError, create_shipment, quote_order, sync_shipment
from .models import (
    Carrito,
    Categoria,
    Order,
    OrderItem,
    Producto,
    ProductVariant,
    InventoryMovement,
    Reseña,
    ShippingAddress,
    ShippingUpdate,
    Subcategoria,
    record_inventory_movement,
)

import os


def _split_variant_values(raw_value):
    return [value.strip() for value in (raw_value or "").split(",") if value.strip()]


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


@admin.action(description="Generar variantes faltantes desde tallas y colores")
def generar_variantes_faltantes(modeladmin, request, queryset):
    total_created = 0
    total_products = 0
    for product in queryset:
        created_count = modeladmin._create_missing_variants_for_product(product)
        total_created += created_count
        total_products += 1
    if total_created:
        modeladmin.message_user(
            request,
            f"Se generaron {total_created} variantes nuevas en {total_products} productos.",
            level=messages.SUCCESS,
        )
    else:
        modeladmin.message_user(
            request,
            "No había variantes faltantes por crear. Revisa que tallas y colores estén separados por comas.",
            level=messages.INFO,
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


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = ("sku", "talla", "color", "stock", "costo", "precio_override", "activo")
    autocomplete_fields = ()


class InventoryMovementInline(admin.TabularInline):
    model = InventoryMovement
    extra = 0
    fields = ("created_at", "movement_type", "variant", "quantity_change", "stock_before", "stock_after", "note")
    readonly_fields = ("created_at", "movement_type", "variant", "quantity_change", "stock_before", "stock_after", "note")
    can_delete = False
    show_change_link = True


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
        "inventory_mode",
        "stock",
        "variant_stock_summary",
        "disponible",
        "fecha_actualizacion",
    )
    list_filter = ("disponible", "categoria", "subcategoria", "fecha_creacion", "fecha_actualizacion")
    search_fields = ("nombre", "descripcion", "slug_imagen")
    autocomplete_fields = ("categoria", "subcategoria")
    actions = [importar_disenos, marcar_disponibles, marcar_no_disponibles, generar_variantes_faltantes]
    list_editable = ("precio", "stock", "disponible")
    readonly_fields = (
        "fecha_creacion",
        "fecha_actualizacion",
        "imagen_preview_large",
        "inventory_snapshot",
        "variant_generation_panel",
        "stock_count_panel",
    )
    inlines = [ProductVariantInline, InventoryMovementInline]
    fieldsets = (
        ("Identidad", {
            "fields": ("nombre", "slug_imagen", "descripcion")
        }),
        ("Catalogo", {
            "fields": ("categoria", "subcategoria", "precio", "stock", "disponible")
        }),
        ("Variantes", {
            "fields": (
                "tallas_disponibles",
                "colores_disponibles",
                "variant_generation_panel",
                "stock_count_panel",
                "inventory_snapshot",
            )
        }),
        ("Media", {
            "fields": ("imagen", "imagen_preview_large")
        }),
        ("Control", {
            "fields": ("fecha_creacion", "fecha_actualizacion")
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "inventory-dashboard/",
                self.admin_site.admin_view(self.inventory_dashboard_view),
                name="tienda_producto_inventory_dashboard",
            ),
            path(
                "<int:product_id>/generate-variants/",
                self.admin_site.admin_view(self.generate_variants_view),
                name="tienda_producto_generate_variants",
            ),
            path(
                "<int:product_id>/stock-count/",
                self.admin_site.admin_view(self.stock_count_view),
                name="tienda_producto_stock_count",
            ),
        ]
        return custom_urls + urls

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

    @admin.display(description="Inventario")
    def inventory_mode(self, obj):
        if obj.uses_variant_inventory():
            return format_html('<strong style="color:#2f67b0;">Por variantes</strong>')
        return format_html('<span style="color:#888;">General</span>')

    @admin.display(description="Stock variantes")
    def variant_stock_summary(self, obj):
        variants = obj.variants.filter(activo=True)
        if not variants.exists():
            return "-"
        return ", ".join(f"{variant.color}/{variant.talla}: {variant.stock}" for variant in variants[:6])

    @admin.display(description="Resumen de inventario")
    def inventory_snapshot(self, obj):
        variants = list(obj.variants.filter(activo=True))
        if not variants:
            return "Este producto todavía usa stock general. Si quieres controlar talla y color por separado, agrega variantes abajo."
        rows = [
            "<div style='display:flex;flex-wrap:wrap;gap:0.45rem;'>"
        ]
        for variant in variants:
            rows.append(
                f"<span style='padding:0.3rem 0.6rem;border-radius:999px;background:#2f67b022;color:#2f67b0;font-weight:700;'>{variant.color} / {variant.talla}: {variant.stock}</span>"
            )
        rows.append("</div>")
        rows.append(f"<p style='margin-top:0.8rem;'><strong>Total sincronizado:</strong> {obj.stock}</p>")
        return format_html("".join(rows))

    @admin.display(description="Crear variantes")
    def variant_generation_panel(self, obj):
        if not obj.pk:
            return "Guarda el producto primero para poder generar variantes."
        if not obj.tallas_disponibles or not obj.colores_disponibles:
            return "Agrega tallas y colores separados por comas para generar combinaciones."
        generate_url = reverse("admin:tienda_producto_generate_variants", args=[obj.pk])
        sizes = ", ".join(_split_variant_values(obj.tallas_disponibles))
        colors = ", ".join(_split_variant_values(obj.colores_disponibles))
        return format_html(
            """
            <p style="margin-bottom:0.6rem;"><strong>Tallas:</strong> {}</p>
            <p style="margin-bottom:0.9rem;"><strong>Colores:</strong> {}</p>
            <a class="button" href="{}">Generar variantes faltantes</a>
            """,
            sizes or "-",
            colors or "-",
            generate_url,
        )

    @admin.display(description="Inventario físico")
    def stock_count_panel(self, obj):
        if not obj.pk:
            return "Guarda el producto primero para capturar inventario físico."
        count_url = reverse("admin:tienda_producto_stock_count", args=[obj.pk])
        return format_html(
            """
            <p style="margin-bottom:0.9rem;">Captura tu conteo real y el sistema ajusta las diferencias como movimientos de inventario.</p>
            <a class="button" href="{}">Capturar inventario físico</a>
            """,
            count_url,
        )

    def _create_missing_variants_for_product(self, product):
        sizes = _split_variant_values(product.tallas_disponibles)
        colors = _split_variant_values(product.colores_disponibles)
        created_count = 0
        for color in colors:
            for size in sizes:
                _, created = ProductVariant.objects.get_or_create(
                    product=product,
                    talla=size,
                    color=color,
                    defaults={
                        "stock": 0,
                        "costo": product.precio,
                        "precio_override": None,
                        "activo": True,
                    },
                )
                if created:
                    created_count += 1
        product.sync_stock_from_variants()
        return created_count

    def generate_variants_view(self, request, product_id):
        product = self.get_object(request, product_id)
        if not product:
            self.message_user(request, "No encontramos ese producto.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:tienda_producto_changelist"))

        created_count = self._create_missing_variants_for_product(product)
        if created_count:
            self.message_user(
                request,
                f"Se generaron {created_count} variantes faltantes para {product.nombre}.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "No había variantes nuevas por crear para este producto.",
                level=messages.INFO,
            )
        return HttpResponseRedirect(reverse("admin:tienda_producto_change", args=[product.pk]))

    def stock_count_view(self, request, product_id):
        product = self.get_object(request, product_id)
        if not product:
            self.message_user(request, "No encontramos ese producto.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:tienda_producto_changelist"))

        variants = list(product.variants.filter(activo=True).order_by("color", "talla"))

        class StockCountLineForm(forms.Form):
            variant_id = forms.IntegerField(widget=forms.HiddenInput, required=False)
            label = forms.CharField(required=False, widget=forms.HiddenInput)
            current_stock = forms.IntegerField(required=False, widget=forms.HiddenInput)
            counted_stock = forms.IntegerField(min_value=0, label="Conteo real")

        StockCountFormSet = formset_factory(StockCountLineForm, extra=0)

        if request.method == "POST":
            formset = StockCountFormSet(request.POST, prefix="count")
            note = request.POST.get("note", "").strip() or "Conteo físico desde admin."
            if formset.is_valid():
                adjustments = 0
                if variants:
                    variant_map = {variant.id: variant for variant in variants}
                    for form in formset:
                        variant_id = form.cleaned_data.get("variant_id")
                        counted_stock = form.cleaned_data.get("counted_stock")
                        variant = variant_map.get(variant_id)
                        if variant is None or counted_stock is None:
                            continue
                        difference = int(counted_stock) - int(variant.stock)
                        if difference != 0:
                            record_inventory_movement(
                                product=product,
                                variant=variant,
                                movement_type="adjustment",
                                quantity_change=difference,
                                note=note,
                                created_by=request.user,
                                metadata={"counted_stock": counted_stock},
                            )
                            adjustments += 1
                else:
                    counted_stock = request.POST.get("general_counted_stock")
                    if counted_stock not in (None, ""):
                        counted_stock = int(counted_stock)
                        difference = counted_stock - int(product.stock)
                        if difference != 0:
                            record_inventory_movement(
                                product=product,
                                movement_type="adjustment",
                                quantity_change=difference,
                                note=note,
                                created_by=request.user,
                                metadata={"counted_stock": counted_stock},
                            )
                            adjustments += 1

                if adjustments:
                    self.message_user(
                        request,
                        f"Conteo guardado. Se registraron {adjustments} ajustes para {product.nombre}.",
                        level=messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        "Conteo guardado sin diferencias. No hizo falta ajustar inventario.",
                        level=messages.INFO,
                    )
                return HttpResponseRedirect(reverse("admin:tienda_producto_change", args=[product.pk]))
        else:
            initial = []
            for variant in variants:
                initial.append(
                    {
                        "variant_id": variant.id,
                        "label": f"{variant.color} / {variant.talla}",
                        "current_stock": variant.stock,
                        "counted_stock": variant.stock,
                    }
                )
            formset = StockCountFormSet(initial=initial, prefix="count")

        rows = []
        for form in formset:
            rows.append(
                {
                    "form": form,
                    "label": form.initial.get("label", ""),
                    "current_stock": form.initial.get("current_stock", 0),
                }
            )

        context = dict(
            self.admin_site.each_context(request),
            title=f"Conteo físico: {product.nombre}",
            product=product,
            rows=rows,
            formset=formset,
            opts=self.model._meta,
            has_variants=bool(variants),
        )
        return TemplateResponse(request, "admin/tienda/product_stock_count.html", context)

    def inventory_dashboard_view(self, request):
        active_products = Producto.objects.filter(disponible=True)
        active_variants = ProductVariant.objects.filter(activo=True, product__disponible=True).select_related("product")
        low_stock_variants = list(active_variants.filter(stock__lte=3).order_by("stock", "product__nombre", "color", "talla")[:12])
        out_of_stock_variants = list(active_variants.filter(stock=0).order_by("product__nombre", "color", "talla")[:12])
        general_products = list(
            active_products.filter(variants__isnull=True).order_by("stock", "nombre")[:12]
        )
        recent_movements = list(
            InventoryMovement.objects.select_related("product", "variant", "created_by")
            .order_by("-created_at")[:14]
        )

        total_units = 0
        total_inventory_value = 0
        products_using_variants = 0
        products_using_general_stock = 0

        for product in active_products.prefetch_related("variants"):
            variants = [variant for variant in product.variants.all() if variant.activo]
            if variants:
                products_using_variants += 1
                total_units += sum(variant.stock for variant in variants)
                total_inventory_value += sum(
                    (variant.costo or product.precio) * variant.stock
                    for variant in variants
                )
            else:
                products_using_general_stock += 1
                total_units += product.stock
                total_inventory_value += product.precio * product.stock

        context = dict(
            self.admin_site.each_context(request),
            title="Dashboard de inventario",
            subtitle="Resumen operativo del stock actual",
            total_products=active_products.count(),
            total_variants=active_variants.count(),
            total_units=total_units,
            total_inventory_value=total_inventory_value,
            products_using_variants=products_using_variants,
            products_using_general_stock=products_using_general_stock,
            low_stock_variants=low_stock_variants,
            out_of_stock_variants=out_of_stock_variants,
            general_products=general_products,
            recent_movements=recent_movements,
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/inventory_dashboard.html", context)


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


class InventoryMovementAdminForm(forms.ModelForm):
    class Meta:
        model = InventoryMovement
        fields = ("product", "variant", "movement_type", "quantity_change", "order", "note", "created_by")

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        variant = cleaned.get("variant")
        quantity_change = cleaned.get("quantity_change")

        if variant and product and variant.product_id != product.id:
            raise forms.ValidationError("La variante elegida no pertenece al producto seleccionado.")

        if quantity_change == 0:
            raise forms.ValidationError("El movimiento debe cambiar el inventario con una cantidad distinta de cero.")

        return cleaned


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("product", "sku", "color", "talla", "stock", "costo", "activo", "updated_at")
    list_filter = ("activo", "color", "talla", "product__categoria")
    search_fields = ("product__nombre", "sku", "color", "talla")
    autocomplete_fields = ("product",)
    list_editable = ("activo",)
    readonly_fields = ("updated_at", "created_at")
    fieldsets = (
        ("Variante", {
            "fields": ("product", "sku", "color", "talla", "activo")
        }),
        ("Inventario", {
            "fields": ("stock", "costo", "precio_override")
        }),
        ("Control", {
            "fields": ("created_at", "updated_at")
        }),
    )


@admin.register(InventoryMovement)
class InventoryMovementAdmin(admin.ModelAdmin):
    form = InventoryMovementAdminForm
    list_display = (
        "created_at",
        "product",
        "variant",
        "movement_type",
        "quantity_change",
        "stock_before",
        "stock_after",
        "created_by",
    )
    list_filter = ("movement_type", "created_at", "product__categoria")
    search_fields = ("product__nombre", "variant__sku", "variant__color", "variant__talla", "note")
    autocomplete_fields = ("product", "variant", "order", "created_by")
    readonly_fields = ("stock_before", "stock_after", "created_at")

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return (
                "product",
                "variant",
                "order",
                "movement_type",
                "quantity_change",
                "note",
                "created_by",
                "metadata",
                "stock_before",
                "stock_after",
                "created_at",
            )
        return self.readonly_fields

    def has_change_permission(self, request, obj=None):
        if obj:
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if change:
            return

        created = record_inventory_movement(
            product=form.cleaned_data["product"],
            variant=form.cleaned_data.get("variant"),
            order=form.cleaned_data.get("order"),
            movement_type=form.cleaned_data["movement_type"],
            quantity_change=form.cleaned_data["quantity_change"],
            note=form.cleaned_data.get("note", ""),
            created_by=form.cleaned_data.get("created_by") or request.user,
            metadata=obj.metadata,
        )
        obj.pk = created.pk
        obj.stock_before = created.stock_before
        obj.stock_after = created.stock_after
        obj.created_at = created.created_at


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
