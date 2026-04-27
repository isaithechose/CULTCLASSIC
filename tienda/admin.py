from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django import forms
from django.db.models import Count, Sum
from django.forms import formset_factory
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
import calendar
from datetime import date, timedelta

from .skydrop import SkydropError, create_shipment, quote_order, sync_shipment
from .models import (
    BusinessPayment,
    Carrito,
    Categoria,
    Order,
    OrderItem,
    Producto,
    ProductVariant,
    InventoryMovement,
    ExpenseCategory,
    Expense,
    Reseña,
    ShippingAddress,
    ShippingUpdate,
    Subcategoria,
    record_inventory_movement,
)

import os
import types
from decimal import Decimal

from django.utils import timezone

from .utils.variant_image_assignment import (
    existing_thumbnail_or_image_name,
    find_best_image_for_variant,
)


def _split_variant_values(raw_value):
    return [value.strip() for value in (raw_value or "").split(",") if value.strip()]


def _money(value):
    return Decimal(str(value or 0))


def _product_unit_cost(product):
    return _money(getattr(product, "costo", 0))


def _variant_unit_cost(variant):
    return _product_unit_cost(variant.product)


def _variant_sale_price(variant):
    return _money(variant.product.precio)


def _variant_profit(variant):
    return _variant_sale_price(variant) - _variant_unit_cost(variant)


def _variant_margin(variant):
    sale_price = _variant_sale_price(variant)
    if sale_price <= 0:
        return Decimal("0.00")
    return (_variant_profit(variant) / sale_price) * Decimal("100")


def _admin_overview_context():
    today = timezone.localdate()
    month_start = today.replace(day=1)
    pending_orders = Order.objects.filter(status="Pending").count()
    orders_to_ship = Order.objects.filter(status="Completed", shipping_status="Processing").count()
    low_stock_products = Producto.objects.filter(disponible=True, stock__lte=3).count()
    active_variants = ProductVariant.objects.filter(activo=True)
    low_stock_variants = active_variants.filter(stock__lte=3).count()
    out_of_stock_variants = active_variants.filter(stock=0).count()
    recent_movements = InventoryMovement.objects.filter(created_at__date=today).count()
    monthly_expenses = Expense.objects.filter(fecha__gte=month_start, fecha__lte=today).aggregate(total=Sum("monto"))["total"] or 0
    completed_orders = Order.objects.filter(status="Completed")
    monthly_sales = sum(order.total_price for order in completed_orders.filter(created_at__date__gte=month_start, created_at__date__lte=today))

    return {
        "admin_overview_cards": [
            {
                "label": "Pedidos pendientes",
                "value": pending_orders,
                "url": "/admin/tienda/order/?status__exact=Pending",
                "tone": "warn",
            },
            {
                "label": "Pedidos por enviar",
                "value": orders_to_ship,
                "url": "/admin/tienda/order/?status__exact=Completed&shipping_status__exact=Processing",
                "tone": "info",
            },
            {
                "label": "Productos en alerta",
                "value": low_stock_products,
                "url": "/admin/tienda/producto/?stock__lte=3",
                "tone": "danger",
            },
            {
                "label": "Variantes activas",
                "value": active_variants.count(),
                "url": "/admin/tienda/productvariant/",
                "tone": "ok",
            },
            {
                "label": "Variantes con stock bajo",
                "value": low_stock_variants,
                "url": "/admin/tienda/productvariant/?stock__lte=3",
                "tone": "warn",
            },
            {
                "label": "Movimientos hoy",
                "value": recent_movements,
                "url": "/admin/tienda/inventorymovement/",
                "tone": "neutral",
            },
            {
                "label": "Gastos del mes",
                "value": f"${monthly_expenses:.2f}",
                "url": "/admin/tienda/expense/",
                "tone": "danger",
            },
            {
                "label": "Ventas del mes",
                "value": f"${monthly_sales:.2f}",
                "url": "/admin/tienda/order/?status__exact=Completed",
                "tone": "ok",
            },
        ],
        "admin_quick_links": [
            {"label": "Inventario", "url": "/admin/tienda/producto/inventory-dashboard/"},
            {"label": "Recepción de compra", "url": "/admin/tienda/inventorymovement/receive-purchase/"},
            {"label": "Calendario negocio", "url": "/admin/tienda/businesspayment/business-calendar/"},
            {"label": "Dashboard contable", "url": "/admin/tienda/expense/accounting-dashboard/"},
        ],
        "admin_watchlist": [
            {
                "label": "Variantes agotadas",
                "value": out_of_stock_variants,
                "url": "/admin/tienda/productvariant/?stock__exact=0",
            },
            {
                "label": "Stock bajo",
                "value": low_stock_variants,
                "url": "/admin/tienda/productvariant/?stock__lte=3",
            },
            {
                "label": "Pedidos pendientes",
                "value": pending_orders,
                "url": "/admin/tienda/order/",
            },
        ],
    }


def _inventory_snapshot_metrics():
    active_variants = ProductVariant.objects.filter(activo=True)
    low_stock_variants = active_variants.filter(stock__lte=3).count()
    out_of_stock_variants = active_variants.filter(stock=0).count()
    total_units = (active_variants.aggregate(total=Sum("stock"))["total"] or 0) + (
        Producto.objects.filter(disponible=True).exclude(variants__activo=True).aggregate(total=Sum("stock"))["total"] or 0
    )

    total_inventory_cost_value = Decimal("0.00")
    total_inventory_sale_value = Decimal("0.00")
    for variant in active_variants.select_related("product"):
        total_inventory_cost_value += _variant_unit_cost(variant) * Decimal(str(variant.stock))
        total_inventory_sale_value += _variant_sale_price(variant) * Decimal(str(variant.stock))

    return {
        "low_stock_variants": low_stock_variants,
        "out_of_stock_variants": out_of_stock_variants,
        "total_units": total_units,
        "total_inventory_value": total_inventory_cost_value,
        "total_inventory_cost_value": total_inventory_cost_value,
        "total_inventory_sale_value": total_inventory_sale_value,
        "total_inventory_profit_value": total_inventory_sale_value - total_inventory_cost_value,
    }


def _sales_projection_metrics(today):
    window_start = today - timedelta(days=29)
    completed_orders = [order for order in Order.objects.filter(status="Completed") if window_start <= order.created_at.date() <= today]
    trailing_sales = sum(order.total_price for order in completed_orders)
    daily_average = (Decimal(str(trailing_sales)) / Decimal("30")) if completed_orders or trailing_sales else Decimal("0.00")

    month_start = today.replace(day=1)
    _, month_days = calendar.monthrange(today.year, today.month)
    elapsed_days = max((today - month_start).days + 1, 1)
    month_sales = sum(
        order.total_price for order in Order.objects.filter(status="Completed")
        if month_start <= order.created_at.date() <= today
    )
    current_daily_average = Decimal(str(month_sales)) / Decimal(str(elapsed_days))
    projected_month_sales = current_daily_average * Decimal(str(month_days))
    projected_next_30_days = daily_average * Decimal("30")

    return {
        "trailing_30_sales": trailing_sales,
        "daily_average_sales": daily_average.quantize(Decimal("0.01")),
        "projected_month_sales": projected_month_sales.quantize(Decimal("0.01")),
        "projected_next_30_days": projected_next_30_days.quantize(Decimal("0.01")),
    }


def _coerce_month(month_value, today):
    try:
        year_str, month_str = (month_value or "").split("-")
        year = int(year_str)
        month = int(month_str)
        if 1 <= month <= 12:
            return year, month
    except (TypeError, ValueError):
        pass
    return today.year, today.month


def _add_months(current_date, months):
    month_index = current_date.month - 1 + months
    year = current_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(current_date.day, calendar.monthrange(year, month)[1])
    return current_date.replace(year=year, month=month, day=day)


def _next_expense_recurrence_date(expense):
    if expense.recurrencia == "weekly":
        return expense.fecha + timedelta(days=7)
    if expense.recurrencia == "monthly":
        return _add_months(expense.fecha, 1)
    if expense.recurrencia == "yearly":
        return _add_months(expense.fecha, 12)
    return None


def _build_business_calendar_context(year, month):
    first_day = date(year, month, 1)
    _, month_days = calendar.monthrange(year, month)
    last_day = first_day.replace(day=month_days)
    month_weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    today = timezone.localdate()

    orders = [order for order in Order.objects.filter(status="Completed").prefetch_related("items__product") if first_day <= order.created_at.date() <= last_day]
    expenses = list(Expense.objects.filter(fecha__gte=first_day, fecha__lte=last_day).select_related("categoria"))
    payments = list(BusinessPayment.objects.filter(fecha_programada__gte=first_day, fecha_programada__lte=last_day))

    sales_by_day = {}
    for order in orders:
        day = order.created_at.date()
        bucket = sales_by_day.setdefault(day, {"count": 0, "total": Decimal("0.00")})
        bucket["count"] += 1
        bucket["total"] += order.total_price

    expenses_by_day = {}
    for expense in expenses:
        bucket = expenses_by_day.setdefault(expense.fecha, {"count": 0, "total": Decimal("0.00")})
        bucket["count"] += 1
        bucket["total"] += expense.monto

    payments_by_day = {}
    for payment in payments:
        bucket = payments_by_day.setdefault(payment.fecha_programada, {"count": 0, "total": Decimal("0.00"), "pending": 0})
        bucket["count"] += 1
        bucket["total"] += payment.monto
        if payment.estado == "pending":
            bucket["pending"] += 1

    calendar_weeks = []
    for week in month_weeks:
        days = []
        for day in week:
            events = []
            sales = sales_by_day.get(day)
            if sales:
                events.append({"label": "Ventas", "count": sales["count"], "total": sales["total"], "tone": "ok"})
            expense = expenses_by_day.get(day)
            if expense:
                events.append({"label": "Gastos", "count": expense["count"], "total": expense["total"], "tone": "danger"})
            payment = payments_by_day.get(day)
            if payment:
                events.append({
                    "label": "Pagos",
                    "count": payment["count"],
                    "total": payment["total"],
                    "tone": "warn" if payment["pending"] else "neutral",
                })

            days.append(
                {
                    "date": day,
                    "in_month": day.month == month,
                    "is_today": day == today,
                    "events": events,
                }
            )
        calendar_weeks.append(days)

    previous_month = (first_day - timedelta(days=1)).strftime("%Y-%m")
    next_month = (last_day + timedelta(days=1)).strftime("%Y-%m")
    inventory_metrics = _inventory_snapshot_metrics()
    projection_metrics = _sales_projection_metrics(today)

    total_sales = sum(item["total"] for item in sales_by_day.values()) if sales_by_day else Decimal("0.00")
    total_expenses = sum(item["total"] for item in expenses_by_day.values()) if expenses_by_day else Decimal("0.00")
    pending_payments = [payment for payment in payments if payment.estado == "pending"]
    total_pending_payments = sum(payment.monto for payment in pending_payments) if pending_payments else Decimal("0.00")

    return {
        "calendar_weeks": calendar_weeks,
        "calendar_label": first_day.strftime("%B %Y").capitalize(),
        "current_month_value": first_day.strftime("%Y-%m"),
        "previous_month": previous_month,
        "next_month": next_month,
        "month_sales_total": total_sales,
        "month_expenses_total": total_expenses,
        "pending_payments_total": total_pending_payments,
        "pending_payments_count": len(pending_payments),
        "upcoming_payments": sorted(pending_payments, key=lambda payment: (payment.fecha_programada, payment.id))[:8],
        "recent_sales": sorted(orders, key=lambda order: order.created_at, reverse=True)[:8],
        "recent_expenses": sorted(expenses, key=lambda expense: (expense.fecha, expense.id), reverse=True)[:8],
        **inventory_metrics,
        **projection_metrics,
    }


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


@admin.action(description="Asignar imágenes de variante desde media/productos")
def asignar_imagenes_variantes(modeladmin, request, queryset):
    assigned = 0
    missing = 0

    for variant in queryset.select_related("product"):
        image_name = find_best_image_for_variant(variant)
        if not image_name:
            missing += 1
            continue

        if variant.imagen.name == image_name:
            continue

        variant.imagen.name = image_name
        variant.save(update_fields=["imagen", "updated_at"])
        assigned += 1

    if assigned:
        modeladmin.message_user(
            request,
            f"Se asignaron {assigned} imágenes de variante.",
            level=messages.SUCCESS,
        )
    if missing:
        modeladmin.message_user(
            request,
            f"{missing} variantes no encontraron imagen compatible en media/productos.",
            level=messages.WARNING,
        )


@admin.action(description="Marcar variantes sin imagen compatible")
def validar_imagenes_variantes(modeladmin, request, queryset):
    missing = []
    checked = 0

    for variant in queryset.select_related("product"):
        checked += 1
        if not (variant.imagen.name or find_best_image_for_variant(variant)):
            missing.append(str(variant))

    if missing:
        preview = ", ".join(missing[:8])
        extra = "" if len(missing) <= 8 else f" y {len(missing) - 8} más"
        modeladmin.message_user(
            request,
            f"{len(missing)} de {checked} variantes no tienen imagen compatible: {preview}{extra}.",
            level=messages.WARNING,
        )
    else:
        modeladmin.message_user(
            request,
            f"Todas las {checked} variantes revisadas tienen imagen compatible.",
            level=messages.SUCCESS,
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
    fields = (
        "sku",
        "talla",
        "color",
        "imagen",
        "stock",
        "precio_venta_display",
        "costo_producto_display",
        "utilidad_display",
        "margen_display",
        "activo",
    )
    readonly_fields = ("precio_venta_display", "costo_producto_display", "utilidad_display", "margen_display")
    autocomplete_fields = ()
    verbose_name = "Variante real"
    verbose_name_plural = "Variantes reales de inventario (color + talla)"

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        return formfield

    @admin.display(description="Precio venta")
    def precio_venta_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        return f"${_variant_sale_price(obj):.2f}"

    @admin.display(description="Costo producto")
    def costo_producto_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        return f"${_variant_unit_cost(obj):.2f}"

    @admin.display(description="Utilidad")
    def utilidad_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        return f"${_variant_profit(obj):.2f}"

    @admin.display(description="Margen")
    def margen_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        return f"{_variant_margin(obj):.1f}%"


class InventoryMovementInline(admin.TabularInline):
    model = InventoryMovement
    extra = 0
    fields = ("created_at", "movement_type", "variant", "quantity_change", "stock_before", "stock_after", "note")
    readonly_fields = ("created_at", "movement_type", "variant", "quantity_change", "stock_before", "stock_after", "note")
    can_delete = False
    show_change_link = True
    verbose_name = "Movimiento de inventario"
    verbose_name_plural = "Historial de movimientos de inventario"


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
        "costo_producto",
        "precio_venta_base",
        "inventory_mode",
        "stock",
        "variant_stock_summary",
        "inventory_value_summary",
        "disponible",
        "fecha_actualizacion",
    )
    list_filter = ("disponible", "categoria", "subcategoria", "fecha_creacion", "fecha_actualizacion")
    search_fields = ("nombre", "descripcion", "slug_imagen")
    autocomplete_fields = ("categoria", "subcategoria")
    actions = [importar_disenos, marcar_disponibles, marcar_no_disponibles, generar_variantes_faltantes]
    list_editable = ("stock", "disponible")
    readonly_fields = (
        "fecha_creacion",
        "fecha_actualizacion",
        "imagen_preview_large",
        "inventory_guide",
        "inventory_snapshot",
        "variant_generation_panel",
        "stock_count_panel",
    )
    inlines = [ProductVariantInline, InventoryMovementInline]
    fieldsets = (
        ("Base del producto", {
            "fields": ("nombre", "slug_imagen", "descripcion")
        }),
        ("Venta general", {
            "fields": ("categoria", "subcategoria", "costo", "precio", "stock", "disponible")
        }),
        ("Inventario y variantes", {
            "fields": (
                "inventory_guide",
                "tallas_disponibles",
                "colores_disponibles",
                "variant_generation_panel",
                "stock_count_panel",
                "inventory_snapshot",
            )
        }),
        ("Imagen general del producto", {
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
                "inventory-matrix/",
                self.admin_site.admin_view(self.inventory_matrix_view),
                name="tienda_producto_inventory_matrix",
            ),
            path(
                "inventory-dashboard/",
                self.admin_site.admin_view(self.inventory_dashboard_view),
                name="tienda_producto_inventory_dashboard",
            ),
            path(
                "stock-count-bulk/",
                self.admin_site.admin_view(self.stock_count_bulk_view),
                name="tienda_producto_stock_count_bulk",
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

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == "precio" and formfield:
            formfield.label = "Precio venta base"
            formfield.help_text = "Precio de venta del producto. Todas sus variantes usan este mismo precio."
        if db_field.name == "costo" and formfield:
            formfield.label = "Costo unitario del producto"
            formfield.help_text = "Costo por pieza. Todas las variantes usan este mismo costo."
        return formfield

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

    @admin.display(description="Precio venta base", ordering="precio")
    def precio_venta_base(self, obj):
        return f"${obj.precio:.2f}"

    @admin.display(description="Costo producto", ordering="costo")
    def costo_producto(self, obj):
        return f"${_product_unit_cost(obj):.2f}"

    @admin.display(description="Inventario")
    def inventory_mode(self, obj):
        if obj.uses_variant_inventory():
            return format_html('<strong style="color:#2f67b0;">Variantes mandan</strong>')
        return format_html('<span style="color:#888;">Stock general manda</span>')

    @admin.display(description="Cómo funciona este inventario")
    def inventory_guide(self, obj):
        if obj.uses_variant_inventory():
            return format_html(
                """
                <div style="padding:0.9rem 1rem;border-radius:14px;background:#eff6ff;border:1px solid #bfdbfe;">
                  <strong style="display:block;margin-bottom:0.35rem;color:#1d4ed8;">Este producto ya trabaja por variantes.</strong>
                  <div style="color:#334155;">
                    El stock real vive en cada combinación de <strong>color + talla</strong>. El campo <strong>stock</strong> del producto solo refleja la suma y no deberías capturarlo manualmente.
                  </div>
                </div>
                """
            )
        return format_html(
            """
            <div style="padding:0.9rem 1rem;border-radius:14px;background:#fff7ed;border:1px solid #fed7aa;">
              <strong style="display:block;margin-bottom:0.35rem;color:#c2410c;">Este producto todavía usa stock general.</strong>
              <div style="color:#7c2d12;">
                Si vendes por talla y color, genera variantes. Mientras no existan variantes activas, el campo <strong>stock</strong> del producto es el que manda en ventas.
              </div>
            </div>
            """
        )

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
            sale_value = _money(obj.precio) * Decimal(str(obj.stock))
            return format_html(
                """
                <div style="padding:0.85rem 1rem;border-radius:12px;background:#f8fafc;border:1px solid #e2e8f0;">
                  <strong>Modo actual:</strong> stock general.<br>
                  Si quieres controlar talla y color por separado, primero genera variantes abajo y luego captura el inventario en la mesa o en el conteo físico.
                  <div style="margin-top:0.7rem;"><strong>Valor potencial venta:</strong> ${}</div>
                </div>
                """,
                f"{sale_value:.2f}",
            )
        total_units = sum(variant.stock for variant in variants)
        cost_value = sum(_variant_unit_cost(variant) * Decimal(str(variant.stock)) for variant in variants)
        sale_value = sum(_variant_sale_price(variant) * Decimal(str(variant.stock)) for variant in variants)
        profit_value = sale_value - cost_value
        rows = [
            "<div style='display:flex;flex-wrap:wrap;gap:0.45rem;'>"
        ]
        for variant in variants:
            rows.append(
                f"<span style='padding:0.3rem 0.6rem;border-radius:999px;background:#2f67b022;color:#2f67b0;font-weight:700;'>{variant.color} / {variant.talla}: {variant.stock}</span>"
            )
        rows.append("</div>")
        rows.append(
            f"<p style='margin-top:0.8rem;'><strong>Total sincronizado:</strong> {obj.stock}</p>"
            f"<p style='margin-top:0.35rem;'><strong>Valor al costo:</strong> ${cost_value:.2f} &nbsp; <strong>Valor venta:</strong> ${sale_value:.2f} &nbsp; <strong>Utilidad potencial:</strong> ${profit_value:.2f}</p>"
            "<p style='margin-top:0.35rem;color:#475569;'>Este total se recalcula desde las variantes activas.</p>"
        )
        return format_html("".join(rows))

    @admin.display(description="Valor inventario")
    def inventory_value_summary(self, obj):
        variants = list(obj.variants.filter(activo=True))
        if variants:
            cost_value = sum(_variant_unit_cost(variant) * Decimal(str(variant.stock)) for variant in variants)
            sale_value = sum(_variant_sale_price(variant) * Decimal(str(variant.stock)) for variant in variants)
        else:
            cost_value = _money(obj.precio) * Decimal(str(obj.stock))
            sale_value = cost_value
        profit_value = sale_value - cost_value
        return format_html(
            "<span title='Costo: ${} | Venta: ${} | Utilidad potencial: ${}'>${}</span>",
            f"{cost_value:.2f}",
            f"{sale_value:.2f}",
            f"{profit_value:.2f}",
            f"{sale_value:.2f}",
        )

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
            <p style="margin-bottom:0.6rem;color:#475569;">Aquí defines las combinaciones que después se convierten en variantes editables.</p>
            <p style="margin-bottom:0.6rem;"><strong>Tallas base:</strong> {}</p>
            <p style="margin-bottom:0.9rem;"><strong>Colores base:</strong> {}</p>
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
            <p style="margin-bottom:0.9rem;">Si ya hay variantes, aquí cuentas cada color/talla. Si no hay variantes, ajustas el stock general del producto.</p>
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
        total_inventory_cost_value = Decimal("0.00")
        total_inventory_sale_value = Decimal("0.00")
        products_using_variants = 0
        products_using_general_stock = 0

        for product in active_products.prefetch_related("variants"):
            variants = [variant for variant in product.variants.all() if variant.activo]
            if variants:
                products_using_variants += 1
                total_units += sum(variant.stock for variant in variants)
                total_inventory_cost_value += sum(_variant_unit_cost(variant) * Decimal(str(variant.stock)) for variant in variants)
                total_inventory_sale_value += sum(_variant_sale_price(variant) * Decimal(str(variant.stock)) for variant in variants)
            else:
                products_using_general_stock += 1
                total_units += product.stock
                total_inventory_cost_value += _product_unit_cost(product) * Decimal(str(product.stock))
                total_inventory_sale_value += _money(product.precio) * Decimal(str(product.stock))

        context = dict(
            self.admin_site.each_context(request),
            title="Dashboard de inventario",
            subtitle="Resumen operativo del stock actual",
            total_products=active_products.count(),
            total_variants=active_variants.count(),
            total_units=total_units,
            total_inventory_value=total_inventory_cost_value,
            total_inventory_cost_value=total_inventory_cost_value,
            total_inventory_sale_value=total_inventory_sale_value,
            total_inventory_profit_value=total_inventory_sale_value - total_inventory_cost_value,
            products_using_variants=products_using_variants,
            products_using_general_stock=products_using_general_stock,
            low_stock_variants=low_stock_variants,
            out_of_stock_variants=out_of_stock_variants,
            general_products=general_products,
            recent_movements=recent_movements,
            stock_count_bulk_url=reverse("admin:tienda_producto_stock_count_bulk"),
            inventory_matrix_url=reverse("admin:tienda_producto_inventory_matrix"),
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/inventory_dashboard.html", context)

    def inventory_matrix_view(self, request):
        variants = list(
            ProductVariant.objects.filter(activo=True, product__disponible=True)
            .select_related("product", "product__categoria")
            .order_by("product__nombre", "color", "talla")
        )

        class InventoryMatrixLineForm(forms.Form):
            variant_id = forms.IntegerField(widget=forms.HiddenInput)
            stock = forms.IntegerField(min_value=0, label="Stock")

        InventoryMatrixFormSet = formset_factory(InventoryMatrixLineForm, extra=0)

        if request.method == "POST":
            formset = InventoryMatrixFormSet(request.POST, prefix="matrix")
            note = request.POST.get("note", "").strip() or "Ajuste desde mesa de inventario."
            if formset.is_valid():
                variant_map = {variant.id: variant for variant in variants}
                adjustments = 0
                for form in formset:
                    variant_id = form.cleaned_data.get("variant_id")
                    new_stock = form.cleaned_data.get("stock")
                    variant = variant_map.get(variant_id)
                    if variant is None or new_stock is None:
                        continue
                    difference = int(new_stock) - int(variant.stock)
                    if difference != 0:
                        record_inventory_movement(
                            product=variant.product,
                            variant=variant,
                            movement_type="adjustment",
                            quantity_change=difference,
                            note=note,
                            created_by=request.user,
                            metadata={"target_stock": new_stock, "mode": "inventory_matrix"},
                        )
                        adjustments += 1

                if adjustments:
                    self.message_user(
                        request,
                        f"Mesa de inventario guardada. Se registraron {adjustments} ajustes.",
                        level=messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        "No hubo cambios de stock que guardar.",
                        level=messages.INFO,
                    )
                return HttpResponseRedirect(reverse("admin:tienda_producto_inventory_matrix"))
        else:
            initial = [
                {
                    "variant_id": variant.id,
                    "stock": variant.stock,
                }
                for variant in variants
            ]
            formset = InventoryMatrixFormSet(initial=initial, prefix="matrix")

        rows = []
        for variant, form in zip(variants, formset.forms):
            rows.append(
                {
                    "variant": variant,
                    "form": form,
                }
            )

        context = dict(
            self.admin_site.each_context(request),
            title="Inventario por variantes",
            rows=rows,
            formset=formset,
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/inventory_matrix.html", context)

    def stock_count_bulk_view(self, request):
        variants = list(
            ProductVariant.objects.filter(activo=True, product__disponible=True)
            .select_related("product")
            .order_by("product__nombre", "color", "talla")
        )

        class BulkStockCountLineForm(forms.Form):
            variant_id = forms.IntegerField(widget=forms.HiddenInput)
            counted_stock = forms.IntegerField(min_value=0, label="Conteo real")

        BulkStockCountFormSet = formset_factory(BulkStockCountLineForm, extra=0)

        if request.method == "POST":
            formset = BulkStockCountFormSet(request.POST, prefix="bulk")
            note = request.POST.get("note", "").strip() or "Conteo físico masivo desde admin."
            if formset.is_valid():
                variant_map = {variant.id: variant for variant in variants}
                adjustments = 0
                for form in formset:
                    variant_id = form.cleaned_data.get("variant_id")
                    counted_stock = form.cleaned_data.get("counted_stock")
                    variant = variant_map.get(variant_id)
                    if variant is None or counted_stock is None:
                        continue
                    difference = int(counted_stock) - int(variant.stock)
                    if difference != 0:
                        record_inventory_movement(
                            product=variant.product,
                            variant=variant,
                            movement_type="adjustment",
                            quantity_change=difference,
                            note=note,
                            created_by=request.user,
                            metadata={"counted_stock": counted_stock, "mode": "bulk"},
                        )
                        adjustments += 1

                if adjustments:
                    self.message_user(
                        request,
                        f"Conteo masivo guardado. Se registraron {adjustments} ajustes.",
                        level=messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        "Conteo masivo guardado sin diferencias.",
                        level=messages.INFO,
                    )
                return HttpResponseRedirect(reverse("admin:tienda_producto_inventory_dashboard"))
        else:
            initial = [
                {
                    "variant_id": variant.id,
                    "counted_stock": variant.stock,
                }
                for variant in variants
            ]
            formset = BulkStockCountFormSet(initial=initial, prefix="bulk")

        rows = []
        for variant, form in zip(variants, formset.forms):
            rows.append(
                {
                    "variant": variant,
                    "form": form,
                }
            )

        context = dict(
            self.admin_site.each_context(request),
            title="Conteo físico masivo",
            rows=rows,
            formset=formset,
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/product_stock_count_bulk.html", context)


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
    list_display = (
        "preview_imagen",
        "product",
        "sku",
        "color",
        "talla",
        "imagen_status",
        "stock",
        "costo_producto_display",
        "precio_venta_display",
        "utilidad_display",
        "margen_display",
        "activo",
        "updated_at",
    )
    list_filter = ("activo", "color", "talla", "product__categoria")
    search_fields = ("product__nombre", "sku", "color", "talla")
    autocomplete_fields = ("product",)
    list_editable = ("activo",)
    actions = [asignar_imagenes_variantes, validar_imagenes_variantes]
    readonly_fields = (
        "updated_at",
        "created_at",
        "imagen_preview_large",
        "costo_producto_display",
        "precio_venta_display",
        "utilidad_display",
        "margen_display",
        "variant_role_help",
    )
    fieldsets = (
        ("Variante", {
            "fields": ("product", "sku", "color", "talla", "imagen", "activo", "imagen_preview_large", "variant_role_help")
        }),
        ("Inventario", {
            "fields": ("stock", "costo_producto_display", "precio_venta_display", "utilidad_display", "margen_display")
        }),
        ("Control", {
            "fields": ("created_at", "updated_at")
        }),
    )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        return formfield

    @admin.display(description="Imagen")
    def preview_imagen(self, obj):
        image_name = existing_thumbnail_or_image_name(obj.display_image_name)
        if image_name:
            return format_html(
                '<img src="{}" loading="lazy" decoding="async" style="width:42px;height:42px;object-fit:cover;border-radius:8px;" />',
                f"{settings.MEDIA_URL.rstrip('/')}/{image_name.lstrip('/')}",
            )
        return "-"

    @admin.display(description="Imagen color")
    def imagen_status(self, obj):
        image_name = obj.display_image_name
        if not image_name:
            return format_html(
                '<span style="display:inline-block;padding:0.24rem 0.55rem;border-radius:999px;background:#fee2e2;color:#991b1b;font-weight:700;">Falta</span>'
            )

        source = "Manual" if obj.imagen else "Auto"
        return format_html(
            '<span title="{}" style="display:inline-block;padding:0.24rem 0.55rem;border-radius:999px;background:#dcfce7;color:#166534;font-weight:700;">{}</span>',
            image_name,
            source,
        )

    @admin.display(description="Vista previa")
    def imagen_preview_large(self, obj):
        if not obj:
            return "-"
        image_url = obj.display_image_url
        if not image_url:
            return "-"
        return format_html(
            '<img src="{}" loading="lazy" decoding="async" style="width:220px;height:280px;object-fit:cover;border-radius:8px;border:1px solid #e5e7eb;" />',
            image_url,
        )

    @admin.display(description="Costo producto")
    def costo_producto_display(self, obj):
        return f"${_variant_unit_cost(obj):.2f}"

    @admin.display(description="Precio venta")
    def precio_venta_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        return f"${_variant_sale_price(obj):.2f}"

    @admin.display(description="Utilidad")
    def utilidad_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        profit = _variant_profit(obj)
        color = "#166534" if profit >= 0 else "#991b1b"
        bg = "#dcfce7" if profit >= 0 else "#fee2e2"
        return format_html(
            '<span style="display:inline-block;padding:0.24rem 0.55rem;border-radius:999px;background:{};color:{};font-weight:700;">${}</span>',
            bg,
            color,
            f"{profit:.2f}",
        )

    @admin.display(description="Margen")
    def margen_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        return f"{_variant_margin(obj):.1f}%"

    @admin.display(description="Qué controla esta variante")
    def variant_role_help(self, obj):
        return format_html(
            """
            <div style="padding:0.85rem 1rem;border-radius:12px;background:#eff6ff;border:1px solid #bfdbfe;">
              <strong style="display:block;margin-bottom:0.35rem;color:#1d4ed8;">Esta fila es la existencia real vendible.</strong>
              <div style="color:#334155;">
                Cuando el cliente elige <strong>{color}</strong> y <strong>{talla}</strong>, este es el stock que se descuenta. La imagen aquí debe corresponder al color de esta variante.
              </div>
            </div>
            """,
            color=obj.color or "-",
            talla=obj.talla or "-",
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
    readonly_fields = ("stock_before", "stock_after", "created_at", "movement_guide")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "receive-purchase/",
                self.admin_site.admin_view(self.receive_purchase_view),
                name="tienda_inventorymovement_receive_purchase",
            ),
            path(
                "receive-purchase/<int:variant_id>/",
                self.admin_site.admin_view(self.receive_purchase_view),
                name="tienda_inventorymovement_receive_purchase_variant",
            ),
        ]
        return custom_urls + urls

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
                "movement_guide",
            )
        return self.readonly_fields

    fieldsets = (
        ("Movimiento", {
            "fields": ("movement_guide", "product", "variant", "movement_type", "quantity_change", "order", "note", "created_by")
        }),
        ("Resultado", {
            "fields": ("stock_before", "stock_after", "created_at")
        }),
    )

    @admin.display(description="Cómo usar movimientos")
    def movement_guide(self, obj=None):
        return format_html(
            """
            <div style="padding:0.85rem 1rem;border-radius:12px;background:#f8fafc;border:1px solid #e2e8f0;">
              Usa <strong>Movimientos</strong> para dejar historial. Si solo editas el stock de una variante, cambias la existencia pero no el motivo. Aquí registras compras, ventas, ajustes y entradas/salidas manuales.
            </div>
            """
        )

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

    def _purchase_expense_category(self):
        category, _ = ExpenseCategory.objects.get_or_create(
            nombre="Compras inventario",
            defaults={"descripcion": "Compra de mercancía para inventario."},
        )
        return category

    def receive_purchase_view(self, request, variant_id=None):
        variants = list(
            ProductVariant.objects.filter(activo=True, product__disponible=True)
            .select_related("product")
            .order_by("product__nombre", "color", "talla")
        )
        selected_variant = None
        if variant_id:
            selected_variant = next((variant for variant in variants if variant.id == variant_id), None)

        class PurchaseReceiptLineForm(forms.Form):
            variant_id = forms.IntegerField(widget=forms.HiddenInput)
            quantity = forms.IntegerField(min_value=0, required=False, initial=0, label="Cantidad")
            unit_cost = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10, required=False, label="Costo producto")

        PurchaseReceiptFormSet = formset_factory(PurchaseReceiptLineForm, extra=0)

        if request.method == "POST":
            formset = PurchaseReceiptFormSet(request.POST, prefix="receipt")
            supplier = request.POST.get("supplier", "").strip()
            note = request.POST.get("note", "").strip() or "Recepción de compra desde admin."
            create_expense = request.POST.get("create_expense") == "on"
            receipt_date = request.POST.get("receipt_date", "").strip() or str(timezone.localdate())

            if formset.is_valid():
                variant_map = {variant.id: variant for variant in variants}
                movements = 0
                total_purchase_amount = Decimal("0.00")

                for form in formset:
                    current_variant_id = form.cleaned_data.get("variant_id")
                    quantity = form.cleaned_data.get("quantity") or 0
                    unit_cost = form.cleaned_data.get("unit_cost")
                    variant = variant_map.get(current_variant_id)
                    if variant is None or quantity <= 0:
                        continue

                    if unit_cost is not None and variant.product.costo != unit_cost:
                        variant.product.costo = unit_cost
                        variant.product.save(update_fields=["costo", "fecha_actualizacion"])
                    unit_cost = unit_cost if unit_cost is not None else _product_unit_cost(variant.product)

                    record_inventory_movement(
                        product=variant.product,
                        variant=variant,
                        movement_type="purchase",
                        quantity_change=int(quantity),
                        note=note,
                        created_by=request.user,
                        metadata={
                            "supplier": supplier,
                            "unit_cost": str(unit_cost),
                            "receipt_date": receipt_date,
                        },
                    )
                    total_purchase_amount += Decimal(str(unit_cost)) * Decimal(str(quantity))
                    movements += 1

                if create_expense and total_purchase_amount > 0:
                    Expense.objects.create(
                        fecha=receipt_date,
                        categoria=self._purchase_expense_category(),
                        concepto=f"Recepción de compra ({movements} variantes)",
                        monto=total_purchase_amount,
                        metodo_pago="transfer",
                        proveedor=supplier or "",
                        nota=note,
                        created_by=request.user,
                    )

                if movements:
                    self.message_user(
                        request,
                        f"Compra registrada. Se cargaron {movements} variantes y ${total_purchase_amount:.2f} de costo total.",
                        level=messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        "No capturaste cantidades mayores a cero.",
                        level=messages.INFO,
                    )
                return HttpResponseRedirect(reverse("admin:tienda_inventorymovement_changelist"))
        else:
            initial = [
                {
                    "variant_id": variant.id,
                    "quantity": 0,
                    "unit_cost": variant.product.costo or "",
                }
                for variant in variants
            ]
            formset = PurchaseReceiptFormSet(initial=initial, prefix="receipt")

        rows = []
        for variant, form in zip(variants, formset.forms):
            if selected_variant and variant.id != selected_variant.id:
                continue
            rows.append({"variant": variant, "form": form})

        context = dict(
            self.admin_site.each_context(request),
            title="Recepción de compra",
            rows=rows,
            formset=formset,
            selected_variant=selected_variant,
            today=str(timezone.localdate()),
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/receive_purchase.html", context)


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activo", "descripcion_corta")
    list_filter = ("activo",)
    search_fields = ("nombre", "descripcion")
    list_editable = ("activo",)

    @admin.display(description="Descripción")
    def descripcion_corta(self, obj):
        if not obj.descripcion:
            return "-"
        return (obj.descripcion[:60] + "...") if len(obj.descripcion) > 60 else obj.descripcion


@admin.action(description="Generar siguiente gasto recurrente")
def generar_siguiente_gasto_recurrente(modeladmin, request, queryset):
    created = 0
    skipped = 0

    for expense in queryset.select_related("gasto_origen", "categoria", "created_by"):
        next_date = _next_expense_recurrence_date(expense)
        if not expense.recurrencia_activa or not next_date:
            skipped += 1
            continue
        if expense.recurrencia_fin and next_date > expense.recurrencia_fin:
            skipped += 1
            continue

        origin = expense.gasto_origen or expense
        duplicate_exists = Expense.objects.filter(
            gasto_origen=origin,
            fecha=next_date,
            concepto=expense.concepto,
            monto=expense.monto,
        ).exists()
        if duplicate_exists:
            skipped += 1
            continue

        Expense.objects.create(
            fecha=next_date,
            categoria=expense.categoria,
            concepto=expense.concepto,
            monto=expense.monto,
            metodo_pago=expense.metodo_pago,
            proveedor=expense.proveedor,
            nota=expense.nota,
            recurrencia=expense.recurrencia,
            recurrencia_activa=expense.recurrencia_activa,
            recurrencia_fin=expense.recurrencia_fin,
            gasto_origen=origin,
            created_by=expense.created_by or request.user,
        )
        created += 1

    if created:
        modeladmin.message_user(
            request,
            f"Se generaron {created} gastos recurrentes.",
            level=messages.SUCCESS,
        )
    if skipped:
        modeladmin.message_user(
            request,
            f"Se omitieron {skipped} gastos porque no estaban activos, ya existían o terminaron.",
            level=messages.WARNING,
        )


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = (
        "fecha",
        "concepto",
        "categoria",
        "monto",
        "metodo_pago",
        "proveedor",
        "recurrencia_badge",
        "created_by",
    )
    list_filter = ("fecha", "categoria", "metodo_pago", "recurrencia", "recurrencia_activa")
    search_fields = ("concepto", "proveedor", "nota")
    autocomplete_fields = ("categoria", "created_by", "gasto_origen")
    readonly_fields = ("created_at", "generated_count")
    date_hierarchy = "fecha"
    actions = [generar_siguiente_gasto_recurrente]
    fieldsets = (
        ("Gasto", {
            "fields": ("fecha", "categoria", "concepto", "monto", "metodo_pago", "proveedor")
        }),
        ("Recurrencia", {
            "fields": ("recurrencia", "recurrencia_activa", "recurrencia_fin", "gasto_origen", "generated_count")
        }),
        ("Detalle", {
            "fields": ("nota", "created_by", "created_at")
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "accounting-dashboard/",
                self.admin_site.admin_view(self.accounting_dashboard_view),
                name="tienda_expense_accounting_dashboard",
            ),
        ]
        return custom_urls + urls

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Recurrencia")
    def recurrencia_badge(self, obj):
        if obj.recurrencia == "none":
            return "-"
        color = "#166534" if obj.recurrencia_activa else "#6b7280"
        bg = "#dcfce7" if obj.recurrencia_activa else "#ececf3"
        suffix = "activa" if obj.recurrencia_activa else "inactiva"
        return format_html(
            '<span style="display:inline-block;padding:0.28rem 0.65rem;border-radius:999px;background:{};color:{};font-weight:700;">{} ({})</span>',
            bg,
            color,
            obj.get_recurrencia_display(),
            suffix,
        )

    @admin.display(description="Gastos generados")
    def generated_count(self, obj):
        if not obj or not obj.pk:
            return 0
        return obj.gastos_generados.count()

    def accounting_dashboard_view(self, request):
        today = timezone.localdate()
        year, month = _coerce_month(request.GET.get("month"), today)
        month_start = date(year, month, 1)
        _, month_days = calendar.monthrange(year, month)
        month_end = month_start.replace(day=month_days)
        previous_month = (month_start - timedelta(days=1)).strftime("%Y-%m")
        next_month = (month_end + timedelta(days=1)).strftime("%Y-%m")
        completed_orders = list(Order.objects.filter(status="Completed").prefetch_related("items__product"))
        month_orders = [order for order in completed_orders if month_start <= order.created_at.date() <= month_end]

        product_sales = sum(order.subtotal_price for order in month_orders)
        shipping_income = sum(order.shipping_total for order in month_orders)
        total_revenue = product_sales + shipping_income
        month_expenses = Expense.objects.filter(fecha__gte=month_start, fecha__lte=month_end)
        recurring_expenses = month_expenses.exclude(recurrencia="none")
        one_time_expenses = month_expenses.filter(recurrencia="none")
        total_expenses = month_expenses.aggregate(total=Sum("monto"))["total"] or 0
        total_recurring_expenses = recurring_expenses.aggregate(total=Sum("monto"))["total"] or 0
        total_one_time_expenses = one_time_expenses.aggregate(total=Sum("monto"))["total"] or 0

        estimated_cogs = 0
        for order in month_orders:
            for item in order.items.all():
                estimated_cogs += _product_unit_cost(item.product) * item.quantity

        gross_profit = product_sales - estimated_cogs
        net_profit = gross_profit - total_expenses
        gross_margin = (gross_profit / product_sales * 100) if product_sales else Decimal("0.00")
        net_margin = (net_profit / product_sales * 100) if product_sales else Decimal("0.00")

        top_expenses = list(month_expenses.select_related("categoria").order_by("-monto")[:10])
        recent_orders = month_orders[-10:][::-1]
        active_recurring_expenses = []
        for expense in Expense.objects.filter(recurrencia_activa=True).exclude(recurrencia="none").select_related("categoria").order_by("fecha", "id"):
            next_date = _next_expense_recurrence_date(expense)
            if expense.recurrencia_fin and next_date and next_date > expense.recurrencia_fin:
                continue
            active_recurring_expenses.append(
                {
                    "expense": expense,
                    "next_date": next_date,
                }
            )
            if len(active_recurring_expenses) >= 8:
                break

        expense_breakdown = []
        category_totals = (
            month_expenses.values("categoria__nombre")
            .annotate(total=Sum("monto"))
            .order_by("-total")
        )
        for item in category_totals:
            expense_breakdown.append(
                {
                    "label": item["categoria__nombre"] or "Sin categoría",
                    "total": item["total"],
                }
            )

        income_statement = [
            {"label": "Ventas de producto", "amount": product_sales, "tone": "ok"},
            {"label": "Costo de producto vendido", "amount": -estimated_cogs, "tone": "warn"},
            {"label": "Utilidad bruta", "amount": gross_profit, "tone": "ok" if gross_profit >= 0 else "danger"},
            {"label": "Gastos únicos", "amount": -total_one_time_expenses, "tone": "danger"},
            {"label": "Gastos recurrentes", "amount": -total_recurring_expenses, "tone": "danger"},
            {"label": "Utilidad neta estimada", "amount": net_profit, "tone": "ok" if net_profit >= 0 else "danger"},
        ]

        context = dict(
            self.admin_site.each_context(request),
            title="Dashboard contable",
            subtitle="Ventas, costos, gastos y utilidad estimada del mes",
            month_label=month_start.strftime("%B %Y").capitalize(),
            current_month_value=month_start.strftime("%Y-%m"),
            previous_month=previous_month,
            next_month=next_month,
            product_sales=product_sales,
            shipping_income=shipping_income,
            total_revenue=total_revenue,
            total_expenses=total_expenses,
            total_recurring_expenses=total_recurring_expenses,
            total_one_time_expenses=total_one_time_expenses,
            estimated_cogs=estimated_cogs,
            gross_profit=gross_profit,
            net_profit=net_profit,
            gross_margin=gross_margin,
            net_margin=net_margin,
            month_orders_count=len(month_orders),
            month_expenses_count=month_expenses.count(),
            recurring_expenses_count=recurring_expenses.count(),
            one_time_expenses_count=one_time_expenses.count(),
            top_expenses=top_expenses,
            recent_orders=recent_orders,
            active_recurring_expenses=active_recurring_expenses,
            expense_breakdown=expense_breakdown,
            income_statement=income_statement,
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/accounting_dashboard.html", context)


@admin.action(description="Marcar pagos como pagados")
def marcar_pagos_pagados(modeladmin, request, queryset):
    today = timezone.localdate()
    updated = queryset.exclude(estado="paid").update(estado="paid", fecha_pagado=today)
    modeladmin.message_user(request, f"{updated} pagos marcados como pagados.", level=messages.SUCCESS)


@admin.action(description="Marcar pagos como pendientes")
def marcar_pagos_pendientes(modeladmin, request, queryset):
    updated = queryset.exclude(estado="pending").update(estado="pending", fecha_pagado=None)
    modeladmin.message_user(request, f"{updated} pagos regresaron a pendiente.", level=messages.SUCCESS)


@admin.register(BusinessPayment)
class BusinessPaymentAdmin(admin.ModelAdmin):
    list_display = ("fecha_programada", "concepto", "categoria", "estado_badge", "monto", "proveedor", "fecha_pagado")
    list_filter = ("estado", "categoria", "fecha_programada", "metodo_pago")
    search_fields = ("concepto", "proveedor", "nota")
    autocomplete_fields = ("created_by",)
    readonly_fields = ("created_at",)
    date_hierarchy = "fecha_programada"
    actions = [marcar_pagos_pagados, marcar_pagos_pendientes]
    fieldsets = (
        ("Pago programado", {
            "fields": ("fecha_programada", "concepto", "monto", "categoria", "estado")
        }),
        ("Seguimiento", {
            "fields": ("fecha_pagado", "metodo_pago", "proveedor", "nota")
        }),
        ("Control", {
            "fields": ("created_by", "created_at")
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "business-calendar/",
                self.admin_site.admin_view(self.business_calendar_view),
                name="tienda_businesspayment_business_calendar",
            ),
        ]
        return custom_urls + urls

    @admin.display(description="Estado")
    def estado_badge(self, obj):
        tones = {
            "pending": ("#9a6700", "#fff4d6"),
            "paid": ("#05603a", "#d1fadf"),
            "canceled": ("#6b7280", "#ececf3"),
        }
        color, bg = tones.get(obj.estado, ("#6b7280", "#ececf3"))
        return format_html(
            '<span style="display:inline-block;padding:0.28rem 0.65rem;border-radius:999px;background:{};color:{};font-weight:700;">{}</span>',
            bg,
            color,
            obj.get_estado_display(),
        )

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        if obj.estado == "paid" and not obj.fecha_pagado:
            obj.fecha_pagado = timezone.localdate()
        if obj.estado != "paid":
            obj.fecha_pagado = None
        super().save_model(request, obj, form, change)

    def business_calendar_view(self, request):
        today = timezone.localdate()
        year, month = _coerce_month(request.GET.get("month"), today)
        context = dict(
            self.admin_site.each_context(request),
            title="Calendario del negocio",
            subtitle="Ventas, gastos, pagos e inventario desde una sola vista",
            opts=self.model._meta,
            **_build_business_calendar_context(year, month),
        )
        return TemplateResponse(request, "admin/tienda/business_calendar_dashboard.html", context)


def _enhanced_admin_index(self, request, extra_context=None):
    context = extra_context or {}
    context.update(_admin_overview_context())
    return AdminSite.index(self, request, extra_context=context)


admin.site.index_template = "admin/index.html"
admin.site.index = types.MethodType(_enhanced_admin_index, admin.site)


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
