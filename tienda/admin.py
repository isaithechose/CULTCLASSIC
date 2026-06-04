from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django import forms
from django.db import transaction
from django.db.models import Case, Count, DecimalField, ExpressionWrapper, F, Prefetch, Sum, Value, When
from django.forms import formset_factory
from django.http import HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import escape, format_html
import calendar
import json
from datetime import date, timedelta

from .skydrop import SkydropError, create_shipment, map_skydrop_status, quote_order, sync_shipment
from .models import (
    BusinessPayment,
    CashRegisterClosure,
    Carrito,
    Categoria,
    AccountingAccount,
    AccountingPeriodClose,
    CreditCardAccount,
    CreditCardStatement,
    JournalEntry,
    JournalEntryLine,
    MoneyAccount,
    BankMovement,
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


DEFAULT_ACCOUNTING_ACCOUNTS = {
    "1000": ("Caja", "asset"),
    "1010": ("Bancos", "asset"),
    "1020": ("Tarjetas por cobrar", "asset"),
    "1100": ("Inventario", "asset"),
    "1200": ("Clientes", "asset"),
    "2000": ("Proveedores", "liability"),
    "2100": ("Tarjetas de credito por pagar", "liability"),
    "3000": ("Capital", "equity"),
    "4000": ("Ventas", "income"),
    "4010": ("Descuentos sobre ventas", "income"),
    "4020": ("Ingresos por envio", "income"),
    "5000": ("Costo de ventas", "cost"),
    "6000": ("Gastos generales", "expense"),
}


def _account(code):
    name, account_type = DEFAULT_ACCOUNTING_ACCOUNTS[code]
    account, _ = AccountingAccount.objects.get_or_create(
        code=code,
        defaults={"name": name, "account_type": account_type},
    )
    return account


def _cash_account_for_method(method):
    code = {"cash": "1000", "transfer": "1010", "card": "1020", "stripe": "1020"}.get(method, "1010")
    return _account(code)


def _create_balanced_journal_entry(*, date_value, entry_type, source, concept, lines, reference="", order=None, expense=None, credit_card_statement=None, created_by=None):
    debit_total = sum(_money(line.get("debit")) for line in lines)
    credit_total = sum(_money(line.get("credit")) for line in lines)
    if debit_total != credit_total:
        raise ValueError(f"La póliza no cuadra: debe ${debit_total:.2f}, haber ${credit_total:.2f}.")
    if debit_total <= 0:
        raise ValueError("La póliza debe tener importe mayor a cero.")

    entry = JournalEntry.objects.create(
        date=date_value,
        entry_type=entry_type,
        source=source,
        concept=concept,
        reference=reference,
        order=order,
        expense=expense,
        credit_card_statement=credit_card_statement,
        created_by=created_by,
    )
    JournalEntryLine.objects.bulk_create(
        [
            JournalEntryLine(
                journal_entry=entry,
                account=line["account"],
                description=line.get("description", ""),
                debit=_money(line.get("debit")),
                credit=_money(line.get("credit")),
            )
            for line in lines
            if _money(line.get("debit")) or _money(line.get("credit"))
        ]
    )
    return entry


def _post_order_journal_entry(order, lines, created_by=None):
    if JournalEntry.objects.filter(order=order, source="pos").exists():
        return None

    product_sales = _money(order.subtotal_price)
    shipping_income = _money(order.shipping_total)
    discount = _money(order.discount_amount)
    total = _money(order.total_price)
    cogs = Decimal("0.00")
    for line in lines:
        cogs += _product_unit_cost(line["product"]) * Decimal(str(line["quantity"]))

    journal_lines = [
        {"account": _cash_account_for_method(order.payment_method), "debit": total, "description": "Cobro venta"},
        {"account": _account("4000"), "credit": product_sales, "description": "Venta de producto"},
    ]
    if discount > 0:
        journal_lines.append({"account": _account("4010"), "debit": discount, "description": "Descuento aplicado"})
    if shipping_income > 0:
        journal_lines.append({"account": _account("4020"), "credit": shipping_income, "description": "Ingreso por envio"})
    if cogs > 0:
        journal_lines.extend(
            [
                {"account": _account("5000"), "debit": cogs, "description": "Costo de ventas"},
                {"account": _account("1100"), "credit": cogs, "description": "Salida de inventario"},
            ]
        )

    return _create_balanced_journal_entry(
        date_value=order.created_at.date() if order.created_at else timezone.localdate(),
        entry_type="income",
        source="pos",
        concept=f"Venta pedido #{order.id}",
        reference=f"ORDER-{order.id}",
        order=order,
        lines=journal_lines,
        created_by=created_by,
    )


def _post_expense_journal_entry(expense, created_by=None):
    if JournalEntry.objects.filter(expense=expense, source="expense").exists():
        return None
    if expense.categoria and expense.categoria.nombre == "Pagos tarjetas de credito":
        return None
    amount = _money(expense.monto)
    if amount <= 0:
        return None
    credit_account = _account("2100") if expense.metodo_pago == "card" else _cash_account_for_method(expense.metodo_pago)
    return _create_balanced_journal_entry(
        date_value=expense.fecha,
        entry_type="expense",
        source="expense",
        concept=f"Gasto: {expense.concepto}",
        reference=f"EXP-{expense.id}",
        expense=expense,
        lines=[
            {"account": _account("6000"), "debit": amount, "description": expense.concepto},
            {"account": credit_account, "credit": amount, "description": expense.get_metodo_pago_display()},
        ],
        created_by=created_by or expense.created_by,
    )


def _post_credit_card_payment_journal_entry(statement, amount, created_by=None):
    if JournalEntry.objects.filter(credit_card_statement=statement, source="credit_card").exists():
        return None
    amount = _money(amount)
    if amount <= 0:
        return None
    return _create_balanced_journal_entry(
        date_value=statement.fecha_pagado or timezone.localdate(),
        entry_type="expense",
        source="credit_card",
        concept=f"Pago tarjeta {statement.tarjeta} - {statement.periodo}",
        reference=f"CC-{statement.id}",
        credit_card_statement=statement,
        lines=[
            {"account": _account("2100"), "debit": amount, "description": "Pago de tarjeta"},
            {"account": _cash_account_for_method(statement.metodo_pago), "credit": amount, "description": statement.get_metodo_pago_display()},
        ],
        created_by=created_by or statement.created_by,
    )


def _month_range_for_date(value):
    month_start = value.replace(day=1)
    _, month_days = calendar.monthrange(month_start.year, month_start.month)
    month_end = month_start.replace(day=month_days)
    return month_start, month_end


def _is_accounting_period_closed(value):
    if not value:
        return False
    month_start, _ = _month_range_for_date(value)
    return AccountingPeriodClose.objects.filter(month_start=month_start).exists()


def _channel_comparison(web_monthly_sales, ml_extras):
    """Construye los KPIs comparativos sitio vs Mercado Libre del mes.
    Para ML usamos el NETO (después de comisiones) para comparación justa."""
    web = float(web_monthly_sales or 0)
    # ML net (después de fees) — si está en 0 caemos al bruto
    ml = float(ml_extras.get("vb_ml_net_month") or ml_extras.get("vb_ml_revenue_month") or 0)
    total = web + ml
    web_pct = round((web / total * 100), 1) if total > 0 else 0.0
    ml_pct = round((ml / total * 100), 1) if total > 0 else 0.0
    return {
        "vb_channel_total_month": total,
        "vb_channel_web_month": web,
        "vb_channel_ml_month": ml,
        "vb_channel_web_pct": web_pct,
        "vb_channel_ml_pct": ml_pct,
        "vb_channel_has_data": total > 0,
    }


def _ml_vision_extras(today, month_start):
    """Datos de Mercado Libre para el vision board del admin."""
    try:
        from mercadolibre.models import (
            MercadoLibreCredential, MercadoLibreOrder, MercadoLibreListing,
        )
    except ImportError:
        return {
            "vb_ml_connected": False,
            "vb_ml_orders_today": 0,
            "vb_ml_orders_month": 0,
            "vb_ml_revenue_month": 0.0,
            "vb_ml_listings_count": 0,
            "vb_ml_recent_orders": [],
            "vb_ml_last_sync": None,
        }

    cred = MercadoLibreCredential.objects.first()
    connected = cred is not None

    # Excluimos cancelled/invalid de revenue y conteos válidos
    VALID = ("paid", "confirmed", "shipped", "delivered")
    today_ct = MercadoLibreOrder.objects.filter(
        date_created__date=today, status__in=VALID).count()
    month_orders_all = MercadoLibreOrder.objects.filter(date_created__date__gte=month_start)
    month_orders = month_orders_all.filter(status__in=VALID)
    month_ct = month_orders.count()
    month_revenue = month_orders.aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
    month_net = month_orders.aggregate(t=Sum("net_received_amount"))["t"] or Decimal("0.00")
    month_fees = month_orders.aggregate(t=Sum("marketplace_fee"))["t"] or Decimal("0.00")
    month_shipping_cost = month_orders.aggregate(t=Sum("shipping_cost"))["t"] or Decimal("0.00")
    cancelled_ct = month_orders_all.filter(status__in=("cancelled", "invalid")).count()
    listings_ct = MercadoLibreListing.objects.count()
    recent = list(
        MercadoLibreOrder.objects.order_by("-date_created")[:4]
        .values("ml_id", "date_created", "buyer_nickname", "total_amount", "status")
    )
    last_sync = (
        MercadoLibreOrder.objects.order_by("-synced_at").values_list("synced_at", flat=True).first()
        or (cred and cred.updated_at)
    )
    return {
        "vb_ml_connected": connected,
        "vb_ml_nickname": cred.nickname if cred else "",
        "vb_ml_orders_today": today_ct,
        "vb_ml_orders_month": month_ct,
        "vb_ml_orders_cancelled": cancelled_ct,
        "vb_ml_revenue_month": float(month_revenue),
        "vb_ml_net_month": float(month_net),
        "vb_ml_fees_month": float(month_fees),
        "vb_ml_shipping_cost_month": float(month_shipping_cost),
        "vb_ml_listings_count": listings_ct,
        "vb_ml_recent_orders": recent,
        "vb_ml_last_sync": last_sync,
    }


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
    monthly_expenses = Expense.objects.filter(fecha__gte=month_start, fecha__lte=today).aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
    today_expenses = Expense.objects.filter(fecha=today).aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
    credit_card_due = (
        CreditCardStatement.objects.filter(estado="pending")
        .annotate(
            pending_balance=Case(
                When(
                    saldo_corte__gt=F("monto_pagado"),
                    then=ExpressionWrapper(
                        F("saldo_corte") - F("monto_pagado"),
                        output_field=DecimalField(max_digits=10, decimal_places=2),
                    ),
                ),
                default=Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )
        .aggregate(total=Sum("pending_balance"))["total"]
        or Decimal("0.00")
    )
    completed_orders = Order.objects.filter(status="Completed")
    monthly_orders = completed_orders.filter(
        created_at__date__gte=month_start,
        created_at__date__lte=today,
    ).prefetch_related("items")
    monthly_sales = sum(order.total_price for order in monthly_orders)
    today_orders = list(completed_orders.filter(created_at__date=today).prefetch_related("items__product"))
    today_sales = sum(order.total_price for order in today_orders)
    today_cogs = Decimal("0.00")
    for order in today_orders:
        for item in order.items.all():
            today_cogs += _product_unit_cost(item.product) * item.quantity
    today_profit = Decimal(str(today_sales)) - today_cogs - Decimal(str(today_expenses))

    # ── Vision board: serie de últimos 7 días + meta mensual + top productos ──
    from datetime import timedelta as _td
    seven_days = []
    max_day_sales = Decimal("0.00")
    for i in range(6, -1, -1):
        d = today - _td(days=i)
        day_orders = completed_orders.filter(created_at__date=d).prefetch_related("items")
        day_total = sum((o.total_price for o in day_orders), Decimal("0.00"))
        seven_days.append({
            "date": d,
            "label": d.strftime("%a %d").lower(),
            "value": float(day_total),
        })
        if day_total > max_day_sales:
            max_day_sales = day_total
    # Normalizar alturas (0-100) para CSS
    max_val = max(d["value"] for d in seven_days) or 1
    for d in seven_days:
        d["height_pct"] = round((d["value"] / max_val) * 100, 1) if max_val else 0

    yesterday = today - _td(days=1)
    yesterday_orders = completed_orders.filter(created_at__date=yesterday).prefetch_related("items")
    yesterday_sales = sum((o.total_price for o in yesterday_orders), Decimal("0.00"))
    if yesterday_sales > 0:
        pct_change = float((Decimal(str(today_sales)) - yesterday_sales) / yesterday_sales * 100)
    else:
        pct_change = None

    # Meta mensual (configurable vía settings, default $50,000)
    from django.conf import settings as _settings
    monthly_goal = Decimal(str(getattr(_settings, "MONTHLY_SALES_GOAL", 50000)))
    goal_pct = float(min(Decimal(str(monthly_sales)) / monthly_goal * 100, Decimal("100"))) if monthly_goal else 0

    # Top productos del mes (por unidades vendidas)
    from django.db.models import Count, Sum as _Sum
    from tienda.models import OrderItem as _OrderItem
    top_products_qs = (
        _OrderItem.objects.filter(order__in=monthly_orders)
        .values("product__id", "product__nombre", "product__imagen", "product__precio")
        .annotate(units=_Sum("quantity"))
        .order_by("-units")[:4]
    )

    _ml_extras = _ml_vision_extras(today, month_start)
    _ml_extras_combined = {
        **_ml_extras,
        **_channel_comparison(monthly_sales, _ml_extras),
    }
    top_products = []
    for p in top_products_qs:
        if not p["product__id"]:
            continue
        top_products.append({
            "id": p["product__id"],
            "nombre": p["product__nombre"],
            "imagen": p["product__imagen"],
            "precio": p["product__precio"],
            "units": p["units"],
            "url": reverse("admin:tienda_producto_change", args=[p["product__id"]]),
        })

    return {
        "admin_today_summary": [
            {"label": "Ventas hoy", "value": f"${today_sales:.2f}", "tone": "ok"},
            {"label": "Pedidos hoy", "value": len(today_orders), "tone": "info"},
            {"label": "Costo vendido hoy", "value": f"${today_cogs:.2f}", "tone": "warn"},
            {"label": "Gastos hoy", "value": f"${today_expenses:.2f}", "tone": "danger"},
            {"label": "Utilidad estimada hoy", "value": f"${today_profit:.2f}", "tone": "ok" if today_profit >= 0 else "danger"},
        ],
        "admin_overview_cards": [
            {
                "label": "Pedidos pendientes",
                "value": pending_orders,
                "url": reverse("admin:tienda_order_changelist") + "?status__exact=Pending",
                "tone": "warn",
            },
            {
                "label": "Pedidos por enviar",
                "value": orders_to_ship,
                "url": reverse("admin:tienda_order_changelist") + "?status__exact=Completed&shipping_status__exact=Processing",
                "tone": "info",
            },
            {
                "label": "Productos en alerta",
                "value": low_stock_products,
                "url": reverse("admin:tienda_producto_changelist") + "?stock__lte=3",
                "tone": "danger",
            },
            {
                "label": "Variantes activas",
                "value": active_variants.count(),
                "url": reverse("admin:tienda_productvariant_changelist"),
                "tone": "ok",
            },
            {
                "label": "Variantes con stock bajo",
                "value": low_stock_variants,
                "url": reverse("admin:tienda_productvariant_changelist") + "?stock__lte=3",
                "tone": "warn",
            },
            {
                "label": "Movimientos hoy",
                "value": recent_movements,
                "url": reverse("admin:tienda_inventorymovement_changelist"),
                "tone": "neutral",
            },
            {
                "label": "Gastos del mes",
                "value": f"${monthly_expenses:.2f}",
                "url": reverse("admin:tienda_expense_changelist"),
                "tone": "danger",
            },
            {
                "label": "Tarjetas por pagar",
                "value": f"${credit_card_due:.2f}",
                "url": reverse("admin:tienda_creditcardstatement_changelist") + "?estado__exact=pending",
                "tone": "warn",
            },
            {
                "label": "Ventas del mes",
                "value": f"${monthly_sales:.2f}",
                "url": reverse("admin:tienda_order_changelist") + "?status__exact=Completed",
                "tone": "ok",
            },
        ],
        "admin_quick_links": [
            {"label": "Punto de venta", "url": reverse("admin:tienda_order_point_of_sale"), "description": "Venta rápida en mostrador con descuento de inventario.", "tone": "ok"},
            {"label": "Cierre de caja", "url": reverse("admin:tienda_cashregisterclosure_daily_close"), "description": "Cuadra efectivo, tarjeta, transferencias y diferencias.", "tone": "ok"},
            {"label": "Dashboard contable", "url": reverse("admin:tienda_expense_accounting_dashboard"), "description": "Estado de resultados, gastos y utilidad.", "tone": "info"},
            {"label": "Pólizas contables", "url": reverse("admin:tienda_journalentry_changelist"), "description": "Debe, haber y partidas contables por movimiento.", "tone": "info"},
            {"label": "Inventario", "url": reverse("admin:tienda_producto_inventory_dashboard"), "description": "Alertas, valor de stock y movimientos recientes.", "tone": "warn"},
            {"label": "Recepción de compra", "url": reverse("admin:tienda_inventorymovement_receive_purchase"), "description": "Entrada de mercancía y gasto de compra.", "tone": "neutral"},
            {"label": "Calendario negocio", "url": reverse("admin:tienda_businesspayment_business_calendar"), "description": "Ventas, gastos y pagos por día.", "tone": "neutral"},
            {"label": "Tarjetas de crédito", "url": reverse("admin:tienda_creditcardstatement_changelist"), "description": "Estados de cuenta, vencimientos y saldos por pagar.", "tone": "warn"},
        ],
        "vb_today_sales": float(today_sales),
        "vb_today_orders": len(today_orders),
        "vb_today_profit": float(today_profit),
        "vb_pct_vs_yesterday": pct_change,
        "vb_seven_days": seven_days,
        "vb_monthly_sales": float(monthly_sales),
        "vb_monthly_goal": float(monthly_goal),
        "vb_goal_pct": goal_pct,
        "vb_top_products": top_products,
        **_ml_extras_combined,
        "admin_workflow_groups": [
            {
                "title": "Vender",
                "links": [
                    {"label": "Abrir punto de venta", "url": reverse("admin:tienda_order_point_of_sale")},
                    {"label": "Cerrar caja", "url": reverse("admin:tienda_cashregisterclosure_daily_close")},
                    {"label": "Pedidos de hoy", "url": reverse("admin:tienda_order_changelist") + f"?created_at__date={today.isoformat()}"},
                    {"label": "Pedidos pendientes", "url": reverse("admin:tienda_order_changelist") + "?status__exact=Pending"},
                ],
            },
            {
                "title": "Inventario",
                "links": [
                    {"label": "Dashboard inventario", "url": reverse("admin:tienda_producto_inventory_dashboard")},
                    {"label": "Mesa de inventario", "url": reverse("admin:tienda_producto_inventory_matrix")},
                    {"label": "Recepción de compra", "url": reverse("admin:tienda_inventorymovement_receive_purchase")},
                    {"label": "Variantes", "url": reverse("admin:tienda_productvariant_changelist")},
                ],
            },
            {
                "title": "Contabilidad",
                "links": [
                    {"label": "Dashboard contable", "url": reverse("admin:tienda_expense_accounting_dashboard")},
                    {"label": "Catálogo de cuentas", "url": reverse("admin:tienda_accountingaccount_changelist")},
                    {"label": "Pólizas", "url": reverse("admin:tienda_journalentry_changelist")},
                    {"label": "Registrar gasto", "url": reverse("admin:tienda_expense_add")},
                    {"label": "Gastos recurrentes", "url": reverse("admin:tienda_expense_changelist") + "?recurrencia_activa__exact=1"},
                    {"label": "Pagos programados", "url": reverse("admin:tienda_businesspayment_changelist")},
                    {"label": "Tarjetas por pagar", "url": reverse("admin:tienda_creditcardstatement_changelist") + "?estado__exact=pending"},
                ],
            },
            {
                "title": "Catálogo",
                "links": [
                    {"label": "Productos", "url": reverse("admin:tienda_producto_changelist")},
                    {"label": "Nuevo producto", "url": reverse("admin:tienda_producto_add")},
                    {"label": "Categorías", "url": reverse("admin:tienda_categoria_changelist")},
                    {"label": "Diseños", "url": reverse("admin:tienda_producto_changelist") + "?categoria__nombre=Diseños"},
                ],
            },
        ],
        "admin_watchlist": [
            {
                "label": "Variantes agotadas",
                "value": out_of_stock_variants,
                "url": reverse("admin:tienda_productvariant_changelist") + "?stock__exact=0",
            },
            {
                "label": "Stock bajo",
                "value": low_stock_variants,
                "url": reverse("admin:tienda_productvariant_changelist") + "?stock__lte=3",
            },
            {
                "label": "Pedidos pendientes",
                "value": pending_orders,
                "url": reverse("admin:tienda_order_changelist"),
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

    inventory_values = active_variants.aggregate(
        cost_value=Sum(
            ExpressionWrapper(
                F("product__costo") * F("stock"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        ),
        sale_value=Sum(
            ExpressionWrapper(
                F("product__precio") * F("stock"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        ),
    )
    total_inventory_cost_value = inventory_values["cost_value"] or Decimal("0.00")
    total_inventory_sale_value = inventory_values["sale_value"] or Decimal("0.00")

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
    completed_orders = list(
        Order.objects.filter(
            status="Completed",
            created_at__date__gte=window_start,
            created_at__date__lte=today,
        ).prefetch_related("items")
    )
    trailing_sales = sum(order.total_price for order in completed_orders)
    daily_average = (Decimal(str(trailing_sales)) / Decimal("30")) if completed_orders or trailing_sales else Decimal("0.00")

    month_start = today.replace(day=1)
    _, month_days = calendar.monthrange(today.year, today.month)
    elapsed_days = max((today - month_start).days + 1, 1)
    month_orders = Order.objects.filter(
        status="Completed",
        created_at__date__gte=month_start,
        created_at__date__lte=today,
    ).prefetch_related("items")
    month_sales = sum(
        order.total_price for order in month_orders
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


def _cash_register_metrics(target_date):
    orders = list(
        Order.objects.filter(
            status="Completed",
            sales_channel="pos",
            created_at__date=target_date,
        ).prefetch_related("items__product")
    )
    totals = {
        "cash": Decimal("0.00"),
        "card": Decimal("0.00"),
        "transfer": Decimal("0.00"),
        "other": Decimal("0.00"),
    }
    for order in orders:
        method = order.payment_method if order.payment_method in totals else "other"
        totals[method] += Decimal(str(order.total_price))

    cash_expenses = (
        Expense.objects.filter(fecha=target_date, metodo_pago="cash").aggregate(total=Sum("monto"))["total"]
        or Decimal("0.00")
    )
    return {
        "orders": orders,
        "order_count": len(orders),
        "cash_system": totals["cash"],
        "card_system": totals["card"],
        "transfer_system": totals["transfer"],
        "other_system": totals["other"],
        "cash_expenses": Decimal(str(cash_expenses)),
        "system_total": sum(totals.values(), Decimal("0.00")),
    }


def _build_business_calendar_context(year, month):
    first_day = date(year, month, 1)
    _, month_days = calendar.monthrange(year, month)
    last_day = first_day.replace(day=month_days)
    month_weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    today = timezone.localdate()

    orders = list(
        Order.objects.filter(
            status="Completed",
            created_at__date__gte=first_day,
            created_at__date__lte=last_day,
        ).prefetch_related("items__product")
    )
    expenses = list(Expense.objects.filter(fecha__gte=first_day, fecha__lte=last_day).select_related("categoria"))
    payments = list(BusinessPayment.objects.filter(fecha_programada__gte=first_day, fecha_programada__lte=last_day))
    credit_card_statements = list(
        CreditCardStatement.objects.filter(fecha_vencimiento__gte=first_day, fecha_vencimiento__lte=last_day)
        .select_related("tarjeta")
    )

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

    cards_by_day = {}
    for statement in credit_card_statements:
        day = statement.fecha_vencimiento
        bucket = cards_by_day.setdefault(day, {"count": 0, "total": Decimal("0.00"), "overdue": 0})
        bucket["count"] += 1
        bucket["total"] += statement.saldo_pendiente
        if statement.esta_vencido:
            bucket["overdue"] += 1

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
            card_statement = cards_by_day.get(day)
            if card_statement and card_statement["total"] > 0:
                events.append({
                    "label": "Tarjetas",
                    "count": card_statement["count"],
                    "total": card_statement["total"],
                    "tone": "danger" if card_statement["overdue"] else "warn",
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
    pending_credit_cards = [statement for statement in credit_card_statements if statement.estado == "pending"]
    total_pending_credit_cards = sum(statement.saldo_pendiente for statement in pending_credit_cards) if pending_credit_cards else Decimal("0.00")

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
        "pending_credit_cards_total": total_pending_credit_cards,
        "pending_credit_cards_count": len(pending_credit_cards),
        "upcoming_payments": sorted(pending_payments, key=lambda payment: (payment.fecha_programada, payment.id))[:8],
        "upcoming_credit_cards": sorted(pending_credit_cards, key=lambda statement: (statement.fecha_vencimiento, statement.id))[:8],
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

    precio_default = Decimal(str(getattr(settings, "IMPORT_DISENO_PRECIO_DEFAULT", "199.00")))
    stock_default = getattr(settings, "IMPORT_DISENO_STOCK_DEFAULT", 10)
    tallas_default = getattr(settings, "IMPORT_DISENO_TALLAS_DEFAULT", "S,M,L,XL")
    colores_default = getattr(settings, "IMPORT_DISENO_COLORES_DEFAULT", "Negro,Blanco")

    for archivo in os.listdir(ruta):
        if archivo.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
            nombre = os.path.splitext(archivo)[0]
            if not Producto.objects.filter(nombre=nombre).exists():
                Producto.objects.create(
                    nombre=nombre,
                    descripcion="Diseño importado automaticamente.",
                    precio=precio_default,
                    stock=stock_default,
                    imagen=f"diseños_nuevos/{archivo}",
                    categoria=categoria,
                    tallas_disponibles=tallas_default,
                    colores_disponibles=colores_default,
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


class CashRegisterClosureForm(forms.Form):
    fecha = forms.DateField(label="Fecha", widget=forms.DateInput(attrs={"type": "date"}))
    efectivo_contado = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10, initial=0, label="Efectivo contado")
    tarjeta_contado = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10, initial=0, label="Tarjeta contado")
    transferencia_contado = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10, initial=0, label="Transferencia contado")
    otros_contado = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10, initial=0, label="Otros contado")
    nota = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Nota")


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
    actions = [importar_disenos, marcar_disponibles, marcar_no_disponibles, generar_variantes_faltantes, "publicar_en_mercadolibre"]

    @admin.action(description="Publicar en Mercado Libre")
    def publicar_en_mercadolibre(self, request, queryset):
        from mercadolibre import api as ml_api
        from mercadolibre.models import MercadoLibreCredential, MercadoLibreListing
        cred = MercadoLibreCredential.objects.first()
        if not cred:
            self.message_user(request, "No hay cuenta de Mercado Libre conectada.", level=messages.ERROR)
            return
        ok = skipped = errs = 0
        for p in queryset:
            if MercadoLibreListing.objects.filter(producto=p).exists():
                skipped += 1
                continue
            try:
                listing = ml_api.publish_product_to_ml(cred, p)
                ok += 1
            except Exception as exc:
                errs += 1
                self.message_user(request, f"{p.nombre}: {str(exc)[:200]}", level=messages.WARNING)
        self.message_user(
            request,
            f"Publicados: {ok} · Saltados (ya estaban): {skipped} · Errores: {errs}",
            level=messages.SUCCESS if ok > 0 else messages.INFO,
        )
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

    list_per_page = 30

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .prefetch_related(
                Prefetch(
                    "variants",
                    queryset=ProductVariant.objects.filter(activo=True),
                    to_attr="active_variants",
                )
            )
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
        variants = getattr(obj, "active_variants", None)
        if variants is None:
            variants = list(obj.variants.filter(activo=True))
        if not variants:
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
        chart_labels = []
        chart_stock = []
        chart_variants_by_product = {}

        for product in active_products.prefetch_related("variants"):
            variants = [variant for variant in product.variants.all() if variant.activo]
            if variants:
                products_using_variants += 1
                p_stock = sum(variant.stock for variant in variants)
                total_units += p_stock
                total_inventory_cost_value += sum(_variant_unit_cost(variant) * Decimal(str(variant.stock)) for variant in variants)
                total_inventory_sale_value += sum(_variant_sale_price(variant) * Decimal(str(variant.stock)) for variant in variants)
                chart_variants_by_product[product.nombre] = [
                    {"color": v.color, "talla": v.talla, "stock": v.stock} for v in variants
                ]
            else:
                products_using_general_stock += 1
                p_stock = product.stock
                total_units += p_stock
                total_inventory_cost_value += _product_unit_cost(product) * Decimal(str(product.stock))
                total_inventory_sale_value += _money(product.precio) * Decimal(str(product.stock))
                chart_variants_by_product[product.nombre] = [
                    {"color": "—", "talla": "General", "stock": product.stock}
                ]
            chart_labels.append(product.nombre)
            chart_stock.append(p_stock)

        all_variants_list = list(active_variants)
        variants_ok = sum(1 for v in all_variants_list if v.stock > 3)
        variants_low = sum(1 for v in all_variants_list if 0 < v.stock <= 3)
        variants_out = sum(1 for v in all_variants_list if v.stock == 0)
        all_variants_data = [
            {"producto": v.product.nombre, "color": v.color, "talla": v.talla, "stock": v.stock}
            for v in all_variants_list
        ]

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
            chart_labels_json=json.dumps(chart_labels),
            chart_stock_json=json.dumps(chart_stock),
            chart_health_json=json.dumps([variants_ok, variants_low, variants_out]),
            chart_variants_by_product_json=json.dumps(chart_variants_by_product),
            all_variants_data_json=json.dumps(all_variants_data),
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
                                now = timezone.now()
                                backdated = now.replace(
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

        context = dict(
            self.admin_site.each_context(request),
            title="Punto de venta",
            subtitle="Venta rápida con descuento de inventario y pedido completado",
            header_form=header_form,
            formset=formset,
            rows=rows,
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

    list_per_page = 50

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
                    expense = Expense.objects.create(
                        fecha=receipt_date,
                        categoria=self._purchase_expense_category(),
                        concepto=f"Recepción de compra ({movements} variantes)",
                        monto=total_purchase_amount,
                        metodo_pago="transfer",
                        proveedor=supplier or "",
                        nota=note,
                        created_by=request.user,
                    )
                    _post_expense_journal_entry(expense, created_by=request.user)

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


def _credit_card_expense_category():
    category, _ = ExpenseCategory.objects.get_or_create(
        nombre="Pagos tarjetas de credito",
        defaults={"descripcion": "Pagos de estados de cuenta de tarjetas de credito."},
    )
    return category


@admin.action(description="Marcar estados de cuenta como pagados")
def marcar_estados_tarjeta_pagados(modeladmin, request, queryset):
    today = timezone.localdate()
    updated = 0

    for statement in queryset.select_related("tarjeta", "created_by"):
        if statement.estado == "paid":
            continue
        payment_amount = statement.saldo_pendiente or statement.saldo_corte
        statement.estado = "paid"
        statement.fecha_pagado = today
        statement.monto_pagado = statement.saldo_corte
        statement.save(update_fields=["estado", "fecha_pagado", "monto_pagado"])

        concept = f"Pago tarjeta {statement.tarjeta} - {statement.periodo}"
        duplicate = Expense.objects.filter(
            fecha=today,
            concepto=concept,
            monto=payment_amount,
        ).exists()
        if not duplicate and payment_amount > 0:
            expense = Expense.objects.create(
                fecha=today,
                categoria=_credit_card_expense_category(),
                concepto=concept,
                monto=payment_amount,
                metodo_pago=statement.metodo_pago,
                proveedor=str(statement.tarjeta),
                nota=statement.nota or "",
                created_by=request.user if request.user.is_authenticated else statement.created_by,
            )
            _post_credit_card_payment_journal_entry(
                statement,
                payment_amount,
                created_by=request.user if request.user.is_authenticated else statement.created_by,
            )
        updated += 1

    modeladmin.message_user(request, f"{updated} estados de cuenta marcados como pagados.", level=messages.SUCCESS)


@admin.register(CreditCardAccount)
class CreditCardAccountAdmin(admin.ModelAdmin):
    list_display = ("nombre", "banco", "ultimos_4", "limite_credito", "saldo_pendiente_display", "dia_corte", "dia_pago", "activa")
    list_filter = ("activa", "banco")
    search_fields = ("nombre", "banco", "ultimos_4")
    list_editable = ("activa",)
    fieldsets = (
        ("Tarjeta", {
            "fields": ("nombre", "banco", "ultimos_4", "limite_credito", "activa")
        }),
        ("Fechas", {
            "fields": ("dia_corte", "dia_pago")
        }),
        ("Detalle", {
            "fields": ("nota",)
        }),
    )

    @admin.display(description="Saldo pendiente")
    def saldo_pendiente_display(self, obj):
        return f"${obj.saldo_pendiente:.2f}"


@admin.register(CreditCardStatement)
class CreditCardStatementAdmin(admin.ModelAdmin):
    list_display = (
        "tarjeta",
        "periodo",
        "fecha_corte",
        "fecha_vencimiento",
        "saldo_corte",
        "pago_minimo",
        "saldo_pendiente_display",
        "estado_badge",
    )
    list_filter = ("estado", "tarjeta", "fecha_vencimiento", "metodo_pago")
    search_fields = ("tarjeta__nombre", "tarjeta__banco", "tarjeta__ultimos_4", "periodo", "nota")
    autocomplete_fields = ("tarjeta", "created_by")
    readonly_fields = ("created_at", "saldo_pendiente_display", "vencido_badge")
    date_hierarchy = "fecha_vencimiento"
    actions = [marcar_estados_tarjeta_pagados]
    fieldsets = (
        ("Estado de cuenta", {
            "fields": ("tarjeta", "periodo", "fecha_corte", "fecha_vencimiento")
        }),
        ("Monto", {
            "fields": ("saldo_corte", "pago_minimo", "monto_pagado", "saldo_pendiente_display")
        }),
        ("Pago", {
            "fields": ("estado", "fecha_pagado", "metodo_pago", "vencido_badge")
        }),
        ("Detalle", {
            "fields": ("nota", "created_by", "created_at")
        }),
    )

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        if obj.estado == "paid" and not obj.fecha_pagado:
            obj.fecha_pagado = timezone.localdate()
        super().save_model(request, obj, form, change)

    @admin.display(description="Saldo pendiente")
    def saldo_pendiente_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        return f"${obj.saldo_pendiente:.2f}"

    @admin.display(description="Vencido")
    def vencido_badge(self, obj):
        if not obj or not obj.pk:
            return "-"
        if not obj.esta_vencido:
            return format_html('<span style="color:#05603a;font-weight:700;">No</span>')
        return format_html('<span style="color:#b42318;font-weight:800;">Sí</span>')

    @admin.display(description="Estado")
    def estado_badge(self, obj):
        tones = {
            "pending": ("#9a6700", "#fff4d6"),
            "paid": ("#05603a", "#d1fadf"),
            "canceled": ("#6b7280", "#ececf3"),
        }
        if obj.esta_vencido:
            color, bg = "#b42318", "#fee2e2"
        else:
            color, bg = tones.get(obj.estado, ("#6b7280", "#ececf3"))
        return format_html(
            '<span style="display:inline-block;padding:0.28rem 0.65rem;border-radius:999px;background:{};color:{};font-weight:700;">{}</span>',
            bg,
            color,
            "Vencido" if obj.esta_vencido else obj.get_estado_display(),
        )


@admin.register(AccountingAccount)
class AccountingAccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "account_type", "parent", "is_active", "ledger_link")
    list_filter = ("account_type", "is_active")
    search_fields = ("code", "name")
    list_editable = ("is_active",)
    autocomplete_fields = ("parent",)
    fieldsets = (
        ("Cuenta", {
            "fields": ("code", "name", "account_type", "parent", "is_active")
        }),
        ("Detalle", {
            "fields": ("description",)
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:account_id>/ledger/",
                self.admin_site.admin_view(self.ledger_view),
                name="tienda_accountingaccount_ledger",
            ),
            path(
                "<int:account_id>/ledger/export/",
                self.admin_site.admin_view(self.export_ledger_view),
                name="tienda_accountingaccount_ledger_export",
            ),
        ]
        return custom_urls + urls

    @admin.display(description="Mayor")
    def ledger_link(self, obj):
        return format_html(
            '<a class="button" href="{}">Ver mayor</a>',
            reverse("admin:tienda_accountingaccount_ledger", args=[obj.id]),
        )

    def ledger_view(self, request, account_id):
        account = self.get_object(request, account_id)
        if not account:
            self.message_user(request, "No encontramos esa cuenta contable.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:tienda_accountingaccount_changelist"))

        today = timezone.localdate()
        year, month = _coerce_month(request.GET.get("month"), today)
        month_start = date(year, month, 1)
        _, month_days = calendar.monthrange(year, month)
        month_end = month_start.replace(day=month_days)
        previous_month = (month_start - timedelta(days=1)).strftime("%Y-%m")
        next_month = (month_end + timedelta(days=1)).strftime("%Y-%m")

        lines = list(
            JournalEntryLine.objects.filter(
                account=account,
                journal_entry__is_posted=True,
                journal_entry__date__gte=month_start,
                journal_entry__date__lte=month_end,
            )
            .select_related("journal_entry", "account")
            .order_by("journal_entry__date", "journal_entry__id", "id")
        )
        running_balance = Decimal("0.00")
        rows = []
        for line in lines:
            running_balance += line.debit - line.credit
            rows.append({"line": line, "balance": running_balance})

        context = dict(
            self.admin_site.each_context(request),
            title=f"Libro mayor - {account.code} {account.name}",
            account=account,
            rows=rows,
            debit_total=sum(line.debit for line in lines),
            credit_total=sum(line.credit for line in lines),
            ending_balance=running_balance,
            month_label=month_start.strftime("%B %Y").capitalize(),
            current_month_value=month_start.strftime("%Y-%m"),
            previous_month=previous_month,
            next_month=next_month,
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/accounting_ledger.html", context)

    def export_ledger_view(self, request, account_id):
        account = self.get_object(request, account_id)
        if not account:
            self.message_user(request, "No encontramos esa cuenta contable.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:tienda_accountingaccount_changelist"))

        today = timezone.localdate()
        year, month = _coerce_month(request.GET.get("month"), today)
        month_start = date(year, month, 1)
        _, month_days = calendar.monthrange(year, month)
        month_end = month_start.replace(day=month_days)
        lines = list(
            JournalEntryLine.objects.filter(
                account=account,
                journal_entry__is_posted=True,
                journal_entry__date__gte=month_start,
                journal_entry__date__lte=month_end,
            ).select_related("journal_entry").order_by("journal_entry__date", "journal_entry__id", "id")
        )
        filename = f"mayor-{account.code}-{month_start:%Y-%m}.xls"
        response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("\ufeff")
        response.write("<html><head><meta charset='utf-8'></head><body>")
        response.write(f"<h2>Libro mayor - {account.code} {account.name} - {month_start:%B %Y}</h2>")
        response.write("<table border='1'><tr><th>Fecha</th><th>Póliza</th><th>Concepto</th><th>Debe</th><th>Haber</th><th>Saldo</th></tr>")
        balance = Decimal("0.00")
        for line in lines:
            balance += line.debit - line.credit
            response.write(
                "<tr>"
                f"<td>{line.journal_entry.date:%Y-%m-%d}</td>"
                f"<td>{line.journal_entry.reference or line.journal_entry_id}</td>"
                f"<td>{line.journal_entry.concept}</td>"
                f"<td>{line.debit:.2f}</td>"
                f"<td>{line.credit:.2f}</td>"
                f"<td>{balance:.2f}</td>"
                "</tr>"
            )
        response.write("</table></body></html>")
        return response


class JournalEntryLineInlineFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        debit_total = Decimal("0.00")
        credit_total = Decimal("0.00")
        active_lines = 0

        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
                continue
            account = form.cleaned_data.get("account")
            debit = form.cleaned_data.get("debit") or Decimal("0.00")
            credit = form.cleaned_data.get("credit") or Decimal("0.00")
            if not account and not debit and not credit:
                continue
            if debit and credit:
                raise forms.ValidationError("Una partida no puede tener debe y haber al mismo tiempo.")
            if not debit and not credit:
                raise forms.ValidationError("Cada partida debe tener importe en debe o haber.")
            debit_total += debit
            credit_total += credit
            active_lines += 1

        if active_lines and debit_total != credit_total:
            raise forms.ValidationError(f"La póliza no cuadra: debe ${debit_total:.2f}, haber ${credit_total:.2f}.")
        if active_lines == 1:
            raise forms.ValidationError("Una póliza necesita al menos dos partidas.")


class JournalEntryLineInline(admin.TabularInline):
    model = JournalEntryLine
    formset = JournalEntryLineInlineFormSet
    extra = 2
    autocomplete_fields = ("account",)
    fields = ("account", "description", "debit", "credit")


class JournalEntryAdminForm(forms.ModelForm):
    class Meta:
        model = JournalEntry
        fields = "__all__"

    def clean_date(self):
        value = self.cleaned_data["date"]
        if _is_accounting_period_closed(value):
            raise forms.ValidationError("No puedes crear o mover una póliza a un mes contable cerrado.")
        return value


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    form = JournalEntryAdminForm
    inlines = [JournalEntryLineInline]
    list_display = ("date", "entry_type", "source", "concept", "reference", "total_debit_display", "total_credit_display", "balanced_badge")
    list_filter = ("entry_type", "source", "date", "is_posted")
    search_fields = ("concept", "reference", "lines__account__code", "lines__account__name")
    autocomplete_fields = ("order", "expense", "credit_card_statement", "created_by")
    readonly_fields = ("created_at", "total_debit_display", "total_credit_display", "balanced_badge")
    date_hierarchy = "date"
    fieldsets = (
        ("Póliza", {
            "fields": ("date", "entry_type", "source", "concept", "reference", "is_posted")
        }),
        ("Origen", {
            "fields": ("order", "expense", "credit_card_statement")
        }),
        ("Control", {
            "fields": ("created_by", "created_at", "total_debit_display", "total_credit_display", "balanced_badge")
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "trial-balance/",
                self.admin_site.admin_view(self.trial_balance_view),
                name="tienda_journalentry_trial_balance",
            ),
            path(
                "unbalanced/",
                self.admin_site.admin_view(self.unbalanced_view),
                name="tienda_journalentry_unbalanced",
            ),
            path(
                "trial-balance/export/",
                self.admin_site.admin_view(self.export_trial_balance_view),
                name="tienda_journalentry_trial_balance_export",
            ),
            path(
                "generate-missing/",
                self.admin_site.admin_view(self.generate_missing_entries_view),
                name="tienda_journalentry_generate_missing",
            ),
            path(
                "income-statement/",
                self.admin_site.admin_view(self.income_statement_view),
                name="tienda_journalentry_income_statement",
            ),
            path(
                "balance-sheet/",
                self.admin_site.admin_view(self.balance_sheet_view),
                name="tienda_journalentry_balance_sheet",
            ),
            path(
                "income-statement/export/",
                self.admin_site.admin_view(self.export_income_statement_view),
                name="tienda_journalentry_income_statement_export",
            ),
            path(
                "balance-sheet/export/",
                self.admin_site.admin_view(self.export_balance_sheet_view),
                name="tienda_journalentry_balance_sheet_export",
            ),
            path(
                "entries/export/",
                self.admin_site.admin_view(self.export_entries_view),
                name="tienda_journalentry_entries_export",
            ),
        ]
        return custom_urls + urls

    def has_change_permission(self, request, obj=None):
        if obj and _is_accounting_period_closed(obj.date):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and _is_accounting_period_closed(obj.date):
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Debe")
    def total_debit_display(self, obj):
        if not obj or not obj.pk:
            return "$0.00"
        return f"${obj.total_debit:.2f}"

    @admin.display(description="Haber")
    def total_credit_display(self, obj):
        if not obj or not obj.pk:
            return "$0.00"
        return f"${obj.total_credit:.2f}"

    @admin.display(description="Cuadra")
    def balanced_badge(self, obj):
        if not obj or not obj.pk:
            return "-"
        if obj.is_balanced:
            return format_html('<span style="display:inline-block;padding:0.25rem 0.6rem;border-radius:999px;background:#dcfce7;color:#166534;font-weight:800;">Sí</span>')
        return format_html('<span style="display:inline-block;padding:0.25rem 0.6rem;border-radius:999px;background:#fee2e2;color:#991b1b;font-weight:800;">No</span>')

    def _month_bounds(self, request):
        today = timezone.localdate()
        year, month = _coerce_month(request.GET.get("month"), today)
        month_start = date(year, month, 1)
        _, month_days = calendar.monthrange(year, month)
        month_end = month_start.replace(day=month_days)
        return {
            "month_start": month_start,
            "month_end": month_end,
            "month_label": month_start.strftime("%B %Y").capitalize(),
            "current_month_value": month_start.strftime("%Y-%m"),
            "previous_month": (month_start - timedelta(days=1)).strftime("%Y-%m"),
            "next_month": (month_end + timedelta(days=1)).strftime("%Y-%m"),
        }

    def _trial_balance_rows(self, bounds):
        accounts = list(AccountingAccount.objects.filter(is_active=True).order_by("code"))
        rows = []
        total_debit = Decimal("0.00")
        total_credit = Decimal("0.00")
        total_debit_balance = Decimal("0.00")
        total_credit_balance = Decimal("0.00")

        for account in accounts:
            lines = JournalEntryLine.objects.filter(
                account=account,
                journal_entry__is_posted=True,
                journal_entry__date__gte=bounds["month_start"],
                journal_entry__date__lte=bounds["month_end"],
            )
            debit = lines.aggregate(total=Sum("debit"))["total"] or Decimal("0.00")
            credit = lines.aggregate(total=Sum("credit"))["total"] or Decimal("0.00")
            balance = debit - credit
            debit_balance = balance if balance > 0 else Decimal("0.00")
            credit_balance = abs(balance) if balance < 0 else Decimal("0.00")
            if debit or credit or debit_balance or credit_balance:
                rows.append(
                    {
                        "account": account,
                        "debit": debit,
                        "credit": credit,
                        "debit_balance": debit_balance,
                        "credit_balance": credit_balance,
                    }
                )
            total_debit += debit
            total_credit += credit
            total_debit_balance += debit_balance
            total_credit_balance += credit_balance
        return {
            "rows": rows,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "total_debit_balance": total_debit_balance,
            "total_credit_balance": total_credit_balance,
            "is_balanced": total_debit == total_credit,
        }

    def trial_balance_view(self, request):
        bounds = self._month_bounds(request)
        trial_balance = self._trial_balance_rows(bounds)

        context = dict(
            self.admin_site.each_context(request),
            title="Balanza de comprobación",
            opts=self.model._meta,
            **bounds,
            **trial_balance,
        )
        return TemplateResponse(request, "admin/tienda/trial_balance.html", context)

    def export_trial_balance_view(self, request):
        bounds = self._month_bounds(request)
        trial_balance = self._trial_balance_rows(bounds)
        filename = f"balanza-{bounds['current_month_value']}.xls"
        response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("\ufeff")
        response.write("<html><head><meta charset='utf-8'></head><body>")
        response.write(f"<h2>Balanza de comprobación - {bounds['month_label']}</h2>")
        response.write("<table border='1'>")
        response.write("<tr><th>Código</th><th>Cuenta</th><th>Debe</th><th>Haber</th><th>Saldo deudor</th><th>Saldo acreedor</th></tr>")
        for row in trial_balance["rows"]:
            response.write(
                "<tr>"
                f"<td>{row['account'].code}</td>"
                f"<td>{row['account'].name}</td>"
                f"<td>{row['debit']:.2f}</td>"
                f"<td>{row['credit']:.2f}</td>"
                f"<td>{row['debit_balance']:.2f}</td>"
                f"<td>{row['credit_balance']:.2f}</td>"
                "</tr>"
            )
        response.write(
            "<tr>"
            "<td colspan='2'><strong>Totales</strong></td>"
            f"<td><strong>{trial_balance['total_debit']:.2f}</strong></td>"
            f"<td><strong>{trial_balance['total_credit']:.2f}</strong></td>"
            f"<td><strong>{trial_balance['total_debit_balance']:.2f}</strong></td>"
            f"<td><strong>{trial_balance['total_credit_balance']:.2f}</strong></td>"
            "</tr>"
        )
        response.write("</table></body></html>")
        return response

    def generate_missing_entries_view(self, request):
        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:tienda_journalentry_changelist"))

        created_orders = 0
        created_expenses = 0
        created_card_payments = 0
        skipped = 0
        errors = []

        with transaction.atomic():
            orders = (
                Order.objects.filter(status="Completed")
                .exclude(journal_entries__source="pos")
                .prefetch_related("items__product")
            )
            for order in orders:
                lines = [
                    {"product": item.product, "quantity": item.quantity}
                    for item in order.items.all()
                    if item.product_id and item.quantity > 0
                ]
                if not lines:
                    skipped += 1
                    continue
                try:
                    if _post_order_journal_entry(order, lines, created_by=request.user if request.user.is_authenticated else None):
                        created_orders += 1
                except ValueError as exc:
                    errors.append(f"Pedido #{order.id}: {exc}")

            expenses = Expense.objects.exclude(journal_entries__source="expense")
            for expense in expenses:
                try:
                    if _post_expense_journal_entry(expense, created_by=request.user if request.user.is_authenticated else None):
                        created_expenses += 1
                except ValueError as exc:
                    errors.append(f"Gasto #{expense.id}: {exc}")

            statements = CreditCardStatement.objects.filter(estado="paid").exclude(journal_entries__source="credit_card")
            for statement in statements:
                amount = statement.monto_pagado or statement.saldo_corte
                try:
                    if _post_credit_card_payment_journal_entry(statement, amount, created_by=request.user if request.user.is_authenticated else None):
                        created_card_payments += 1
                except ValueError as exc:
                    errors.append(f"Tarjeta #{statement.id}: {exc}")

        self.message_user(
            request,
            f"Pólizas generadas: {created_orders} ventas, {created_expenses} gastos, {created_card_payments} pagos de tarjeta. Omitidas: {skipped}.",
            level=messages.SUCCESS if not errors else messages.WARNING,
        )
        for error in errors[:5]:
            self.message_user(request, error, level=messages.ERROR)
        if len(errors) > 5:
            self.message_user(request, f"Hay {len(errors) - 5} errores adicionales no mostrados.", level=messages.ERROR)
        next_url = request.POST.get("next") or reverse("admin:tienda_journalentry_changelist")
        return HttpResponseRedirect(next_url)

    def _account_activity(self, *, account_type=None, codes=None, date_from=None, date_to=None):
        queryset = JournalEntryLine.objects.filter(journal_entry__is_posted=True).select_related("account", "journal_entry")
        if account_type:
            queryset = queryset.filter(account__account_type=account_type)
        if codes:
            queryset = queryset.filter(account__code__in=codes)
        if date_from:
            queryset = queryset.filter(journal_entry__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(journal_entry__date__lte=date_to)

        rows_by_account = {}
        for line in queryset.order_by("account__code"):
            row = rows_by_account.setdefault(
                line.account_id,
                {
                    "account": line.account,
                    "debit": Decimal("0.00"),
                    "credit": Decimal("0.00"),
                },
            )
            row["debit"] += line.debit
            row["credit"] += line.credit
        return list(rows_by_account.values())

    def income_statement_view(self, request):
        bounds = self._month_bounds(request)
        statement = self._income_statement_data(bounds)
        context = dict(
            self.admin_site.each_context(request),
            title="Estado de resultados desde pólizas",
            opts=self.model._meta,
            **bounds,
            **statement,
        )
        return TemplateResponse(request, "admin/tienda/journal_income_statement.html", context)

    def _income_statement_data(self, bounds):
        income_rows = self._account_activity(
            account_type="income",
            date_from=bounds["month_start"],
            date_to=bounds["month_end"],
        )
        cost_rows = self._account_activity(
            account_type="cost",
            date_from=bounds["month_start"],
            date_to=bounds["month_end"],
        )
        expense_rows = self._account_activity(
            account_type="expense",
            date_from=bounds["month_start"],
            date_to=bounds["month_end"],
        )

        for row in income_rows:
            row["amount"] = row["credit"] - row["debit"]
        for row in cost_rows + expense_rows:
            row["amount"] = row["debit"] - row["credit"]

        total_income = sum(row["amount"] for row in income_rows)
        total_cost = sum(row["amount"] for row in cost_rows)
        total_expense = sum(row["amount"] for row in expense_rows)
        gross_profit = total_income - total_cost
        net_profit = gross_profit - total_expense
        return {
            "income_rows": income_rows,
            "cost_rows": cost_rows,
            "expense_rows": expense_rows,
            "total_income": total_income,
            "total_cost": total_cost,
            "total_expense": total_expense,
            "gross_profit": gross_profit,
            "net_profit": net_profit,
        }

    def balance_sheet_view(self, request):
        bounds = self._month_bounds(request)
        balance_sheet = self._balance_sheet_data(bounds)
        context = dict(
            self.admin_site.each_context(request),
            title="Balance general",
            opts=self.model._meta,
            **bounds,
            **balance_sheet,
        )
        return TemplateResponse(request, "admin/tienda/balance_sheet.html", context)

    def _balance_sheet_data(self, bounds):
        end_date = bounds["month_end"]
        asset_rows = self._account_activity(account_type="asset", date_to=end_date)
        liability_rows = self._account_activity(account_type="liability", date_to=end_date)
        equity_rows = self._account_activity(account_type="equity", date_to=end_date)
        result_rows = self._account_activity(
            account_type="income",
            date_to=end_date,
        ) + self._account_activity(
            account_type="cost",
            date_to=end_date,
        ) + self._account_activity(
            account_type="expense",
            date_to=end_date,
        )

        for row in asset_rows:
            row["amount"] = row["debit"] - row["credit"]
        for row in liability_rows + equity_rows:
            row["amount"] = row["credit"] - row["debit"]

        accumulated_result = Decimal("0.00")
        for row in result_rows:
            if row["account"].account_type == "income":
                accumulated_result += row["credit"] - row["debit"]
            else:
                accumulated_result -= row["debit"] - row["credit"]

        total_assets = sum(row["amount"] for row in asset_rows)
        total_liabilities = sum(row["amount"] for row in liability_rows)
        total_equity = sum(row["amount"] for row in equity_rows)
        total_liabilities_equity = total_liabilities + total_equity + accumulated_result
        difference = total_assets - total_liabilities_equity
        return {
            "asset_rows": asset_rows,
            "liability_rows": liability_rows,
            "equity_rows": equity_rows,
            "accumulated_result": accumulated_result,
            "total_assets": total_assets,
            "total_liabilities": total_liabilities,
            "total_equity": total_equity,
            "total_liabilities_equity": total_liabilities_equity,
            "difference": difference,
            "end_date": end_date,
        }

    def export_income_statement_view(self, request):
        bounds = self._month_bounds(request)
        statement = self._income_statement_data(bounds)
        filename = f"estado-resultados-{bounds['current_month_value']}.xls"
        response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("\ufeff<html><head><meta charset='utf-8'></head><body>")
        response.write(f"<h2>Estado de resultados - {bounds['month_label']}</h2>")
        response.write("<table border='1'><tr><th>Sección</th><th>Código</th><th>Cuenta</th><th>Importe</th></tr>")
        for section, rows in (("Ingresos", statement["income_rows"]), ("Costo de ventas", statement["cost_rows"]), ("Gastos", statement["expense_rows"])):
            for row in rows:
                response.write(f"<tr><td>{section}</td><td>{row['account'].code}</td><td>{row['account'].name}</td><td>{row['amount']:.2f}</td></tr>")
        response.write(f"<tr><td colspan='3'><strong>Utilidad bruta</strong></td><td><strong>{statement['gross_profit']:.2f}</strong></td></tr>")
        response.write(f"<tr><td colspan='3'><strong>Utilidad neta</strong></td><td><strong>{statement['net_profit']:.2f}</strong></td></tr>")
        response.write("</table></body></html>")
        return response

    def export_balance_sheet_view(self, request):
        bounds = self._month_bounds(request)
        balance_sheet = self._balance_sheet_data(bounds)
        filename = f"balance-general-{bounds['current_month_value']}.xls"
        response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("\ufeff<html><head><meta charset='utf-8'></head><body>")
        response.write(f"<h2>Balance general al {balance_sheet['end_date']:%Y-%m-%d}</h2>")
        response.write("<table border='1'><tr><th>Sección</th><th>Código</th><th>Cuenta</th><th>Importe</th></tr>")
        for section, rows in (("Activo", balance_sheet["asset_rows"]), ("Pasivo", balance_sheet["liability_rows"]), ("Capital", balance_sheet["equity_rows"])):
            for row in rows:
                response.write(f"<tr><td>{section}</td><td>{row['account'].code}</td><td>{row['account'].name}</td><td>{row['amount']:.2f}</td></tr>")
        response.write(f"<tr><td>Capital</td><td>R</td><td>Resultado acumulado</td><td>{balance_sheet['accumulated_result']:.2f}</td></tr>")
        response.write(f"<tr><td colspan='3'><strong>Total activos</strong></td><td><strong>{balance_sheet['total_assets']:.2f}</strong></td></tr>")
        response.write(f"<tr><td colspan='3'><strong>Total pasivo + capital</strong></td><td><strong>{balance_sheet['total_liabilities_equity']:.2f}</strong></td></tr>")
        response.write(f"<tr><td colspan='3'><strong>Diferencia</strong></td><td><strong>{balance_sheet['difference']:.2f}</strong></td></tr>")
        response.write("</table></body></html>")
        return response

    def export_entries_view(self, request):
        bounds = self._month_bounds(request)
        entries = JournalEntry.objects.filter(date__gte=bounds["month_start"], date__lte=bounds["month_end"]).prefetch_related("lines__account").order_by("date", "id")
        filename = f"polizas-{bounds['current_month_value']}.xls"
        response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("\ufeff<html><head><meta charset='utf-8'></head><body>")
        response.write(f"<h2>Pólizas - {bounds['month_label']}</h2>")
        response.write("<table border='1'><tr><th>Fecha</th><th>Póliza</th><th>Concepto</th><th>Cuenta</th><th>Descripción</th><th>Debe</th><th>Haber</th></tr>")
        for entry in entries:
            for line in entry.lines.all():
                response.write(
                    f"<tr><td>{entry.date:%Y-%m-%d}</td><td>{entry.reference or entry.id}</td><td>{entry.concept}</td>"
                    f"<td>{line.account.code} {line.account.name}</td><td>{line.description}</td><td>{line.debit:.2f}</td><td>{line.credit:.2f}</td></tr>"
                )
        response.write("</table></body></html>")
        return response

    def unbalanced_view(self, request):
        bounds = self._month_bounds(request)
        entries = list(
            JournalEntry.objects.filter(
                is_posted=True,
                date__gte=bounds["month_start"],
                date__lte=bounds["month_end"],
            ).prefetch_related("lines")
        )
        rows = []
        for entry in entries:
            debit = entry.total_debit
            credit = entry.total_credit
            difference = debit - credit
            if difference != 0:
                rows.append(
                    {
                        "entry": entry,
                        "debit": debit,
                        "credit": credit,
                        "difference": difference,
                    }
                )

        context = dict(
            self.admin_site.each_context(request),
            title="Auditoría de pólizas descuadradas",
            rows=rows,
            unbalanced_count=len(rows),
            opts=self.model._meta,
            **bounds,
        )
        return TemplateResponse(request, "admin/tienda/unbalanced_journal_entries.html", context)


@admin.register(AccountingPeriodClose)
class AccountingPeriodCloseAdmin(admin.ModelAdmin):
    list_display = ("month_start", "month_end", "total_debit", "total_credit", "difference_badge", "unbalanced_count", "closed_by", "created_at")
    list_filter = ("month_start", "closed_by")
    search_fields = ("note", "closed_by__username")
    autocomplete_fields = ("closed_by",)
    readonly_fields = ("month_end", "total_debit", "total_credit", "difference", "unbalanced_count", "created_at", "close_guide")
    date_hierarchy = "month_start"
    fieldsets = (
        ("Cierre", {
            "fields": ("close_guide", "month_start", "month_end", "closed_by", "note")
        }),
        ("Resultado", {
            "fields": ("total_debit", "total_credit", "difference", "unbalanced_count", "created_at")
        }),
    )

    def save_model(self, request, obj, form, change):
        obj.month_start = obj.month_start.replace(day=1)
        month_start, month_end = _month_range_for_date(obj.month_start)
        obj.month_start = month_start
        obj.month_end = month_end
        if not obj.closed_by:
            obj.closed_by = request.user

        lines = JournalEntryLine.objects.filter(
            journal_entry__is_posted=True,
            journal_entry__date__gte=month_start,
            journal_entry__date__lte=month_end,
        )
        obj.total_debit = lines.aggregate(total=Sum("debit"))["total"] or Decimal("0.00")
        obj.total_credit = lines.aggregate(total=Sum("credit"))["total"] or Decimal("0.00")
        obj.difference = obj.total_debit - obj.total_credit

        entries = JournalEntry.objects.filter(
            is_posted=True,
            date__gte=month_start,
            date__lte=month_end,
        ).prefetch_related("lines")
        obj.unbalanced_count = sum(1 for entry in entries if entry.total_debit != entry.total_credit)
        if obj.difference != 0 or obj.unbalanced_count:
            messages.warning(
                request,
                f"El mes se guardó cerrado, pero requiere revisión: diferencia ${obj.difference:.2f}, pólizas descuadradas {obj.unbalanced_count}.",
            )
        super().save_model(request, obj, form, change)

    @admin.display(description="Guía de cierre")
    def close_guide(self, obj=None):
        return format_html(
            """
            <div style="padding:0.85rem 1rem;border-radius:12px;background:#f8fafc;border:1px solid #e2e8f0;">
              Al guardar un cierre, el sistema calcula debe, haber y pólizas descuadradas del mes. Las pólizas de meses cerrados quedan bloqueadas para edición y borrado.
            </div>
            """
        )

    @admin.display(description="Diferencia")
    def difference_badge(self, obj):
        if obj.difference == 0:
            return format_html('<span style="display:inline-block;padding:0.25rem 0.6rem;border-radius:999px;background:#dcfce7;color:#166534;font-weight:800;">$0.00</span>')
        return format_html(
            '<span style="display:inline-block;padding:0.25rem 0.6rem;border-radius:999px;background:#fee2e2;color:#991b1b;font-weight:800;">${}</span>',
            f"{obj.difference:.2f}",
        )


@admin.action(description="Marcar movimientos como conciliados")
def marcar_movimientos_conciliados(modeladmin, request, queryset):
    updated = queryset.update(is_reconciled=True, reconciled_at=timezone.now())
    modeladmin.message_user(request, f"{updated} movimientos marcados como conciliados.", level=messages.SUCCESS)


@admin.action(description="Marcar movimientos como no conciliados")
def marcar_movimientos_no_conciliados(modeladmin, request, queryset):
    updated = queryset.update(is_reconciled=False, reconciled_at=None)
    modeladmin.message_user(request, f"{updated} movimientos regresaron a no conciliados.", level=messages.SUCCESS)


@admin.register(MoneyAccount)
class MoneyAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "bank_name", "account_last4", "accounting_account", "opening_balance", "is_active", "reconciliation_link")
    list_filter = ("kind", "is_active", "bank_name")
    search_fields = ("name", "bank_name", "account_last4", "accounting_account__code", "accounting_account__name")
    autocomplete_fields = ("accounting_account",)
    list_editable = ("is_active",)
    fieldsets = (
        ("Cuenta", {
            "fields": ("name", "kind", "accounting_account", "is_active")
        }),
        ("Banco", {
            "fields": ("bank_name", "account_last4", "opening_balance")
        }),
        ("Detalle", {
            "fields": ("note",)
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:account_id>/reconciliation/",
                self.admin_site.admin_view(self.reconciliation_view),
                name="tienda_moneyaccount_reconciliation",
            ),
            path(
                "<int:account_id>/reconciliation/auto-match/",
                self.admin_site.admin_view(self.auto_match_reconciliation_view),
                name="tienda_moneyaccount_reconciliation_auto_match",
            ),
            path(
                "<int:account_id>/reconciliation/export/",
                self.admin_site.admin_view(self.export_reconciliation_view),
                name="tienda_moneyaccount_reconciliation_export",
            ),
        ]
        return custom_urls + urls

    @admin.display(description="Conciliación")
    def reconciliation_link(self, obj):
        return format_html(
            '<a class="button" href="{}">Conciliar</a>',
            reverse("admin:tienda_moneyaccount_reconciliation", args=[obj.id]),
        )

    def reconciliation_view(self, request, account_id):
        account = self.get_object(request, account_id)
        if not account:
            self.message_user(request, "No encontramos esa cuenta de dinero.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:tienda_moneyaccount_changelist"))

        today = timezone.localdate()
        year, month = _coerce_month(request.GET.get("month"), today)
        month_start = date(year, month, 1)
        _, month_days = calendar.monthrange(year, month)
        month_end = month_start.replace(day=month_days)
        previous_month = (month_start - timedelta(days=1)).strftime("%Y-%m")
        next_month = (month_end + timedelta(days=1)).strftime("%Y-%m")

        movements = list(
            BankMovement.objects.filter(
                money_account=account,
                date__gte=month_start,
                date__lte=month_end,
            ).select_related("journal_entry").order_by("date", "id")
        )
        reconciled_total = sum(movement.signed_amount for movement in movements if movement.is_reconciled)
        unreconciled_total = sum(movement.signed_amount for movement in movements if not movement.is_reconciled)
        bank_total = sum(movement.signed_amount for movement in movements)

        system_total = Decimal("0.00")
        if account.accounting_account:
            lines = JournalEntryLine.objects.filter(
                account=account.accounting_account,
                journal_entry__is_posted=True,
                journal_entry__date__gte=month_start,
                journal_entry__date__lte=month_end,
            )
            system_total = (lines.aggregate(total=Sum("debit"))["total"] or Decimal("0.00")) - (
                lines.aggregate(total=Sum("credit"))["total"] or Decimal("0.00")
            )
        difference = bank_total - system_total

        context = dict(
            self.admin_site.each_context(request),
            title=f"Conciliación - {account}",
            account=account,
            movements=movements,
            bank_total=bank_total,
            system_total=system_total,
            reconciled_total=reconciled_total,
            unreconciled_total=unreconciled_total,
            difference=difference,
            month_label=month_start.strftime("%B %Y").capitalize(),
            current_month_value=month_start.strftime("%Y-%m"),
            previous_month=previous_month,
            next_month=next_month,
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/bank_reconciliation.html", context)

    def _reconciliation_month_bounds(self, request):
        today = timezone.localdate()
        year, month = _coerce_month(request.GET.get("month") or request.POST.get("month"), today)
        month_start = date(year, month, 1)
        _, month_days = calendar.monthrange(year, month)
        return month_start, month_start.replace(day=month_days)

    def _matching_journal_entry_for_movement(self, account, movement, used_entry_ids=None):
        if not account.accounting_account:
            return None

        amount = abs(_money(movement.signed_amount))
        if amount == Decimal("0.00"):
            return None

        window_start = movement.date - timedelta(days=3)
        window_end = movement.date + timedelta(days=3)
        line_filters = {
            "account": account.accounting_account,
            "journal_entry__is_posted": True,
            "journal_entry__date__gte": window_start,
            "journal_entry__date__lte": window_end,
        }
        if movement.signed_amount >= 0:
            line_filters.update({"debit": amount, "credit": Decimal("0.00")})
        else:
            line_filters.update({"credit": amount, "debit": Decimal("0.00")})

        lines = JournalEntryLine.objects.filter(**line_filters).select_related("journal_entry")
        if used_entry_ids:
            lines = lines.exclude(journal_entry_id__in=used_entry_ids)
        lines = lines.exclude(journal_entry__bank_movements__is_reconciled=True)

        candidates = [line.journal_entry for line in lines]
        if not candidates:
            return None

        reference = (movement.reference or "").strip().lower()

        def score(entry):
            entry_reference = (entry.reference or "").strip().lower()
            reference_score = 0 if reference and reference in entry_reference else 1
            return (reference_score, abs((entry.date - movement.date).days), entry.id)

        return sorted(candidates, key=score)[0]

    def auto_match_reconciliation_view(self, request, account_id):
        account = self.get_object(request, account_id)
        if not account:
            self.message_user(request, "No encontramos esa cuenta de dinero.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:tienda_moneyaccount_changelist"))
        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:tienda_moneyaccount_reconciliation", args=[account.id]))
        if not account.accounting_account:
            self.message_user(request, "Esta cuenta no tiene cuenta contable ligada; no se puede auto-conciliar.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:tienda_moneyaccount_reconciliation", args=[account.id]))

        month_start, month_end = self._reconciliation_month_bounds(request)
        used_entry_ids = set()
        matched = 0
        reviewed = 0
        with transaction.atomic():
            movements = BankMovement.objects.select_for_update().filter(
                money_account=account,
                is_reconciled=False,
                date__gte=month_start,
                date__lte=month_end,
            ).order_by("date", "id")
            for movement in movements:
                reviewed += 1
                entry = self._matching_journal_entry_for_movement(account, movement, used_entry_ids)
                if not entry:
                    continue
                movement.journal_entry = entry
                movement.is_reconciled = True
                movement.reconciled_at = timezone.now()
                movement.save(update_fields=["journal_entry", "is_reconciled", "reconciled_at"])
                used_entry_ids.add(entry.id)
                matched += 1

        pending = reviewed - matched
        self.message_user(
            request,
            f"Auto-conciliación terminada: {matched} movimientos conciliados y {pending} pendientes de revisión.",
            level=messages.SUCCESS if matched else messages.WARNING,
        )
        return HttpResponseRedirect(
            f"{reverse('admin:tienda_moneyaccount_reconciliation', args=[account.id])}?month={month_start:%Y-%m}"
        )

    def export_reconciliation_view(self, request, account_id):
        account = self.get_object(request, account_id)
        if not account:
            self.message_user(request, "No encontramos esa cuenta de dinero.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:tienda_moneyaccount_changelist"))

        month_start, month_end = self._reconciliation_month_bounds(request)
        movements = list(
            BankMovement.objects.filter(
                money_account=account,
                date__gte=month_start,
                date__lte=month_end,
            ).select_related("journal_entry").order_by("date", "id")
        )
        bank_total = sum(movement.signed_amount for movement in movements)
        reconciled_total = sum(movement.signed_amount for movement in movements if movement.is_reconciled)
        unreconciled_total = bank_total - reconciled_total

        system_total = Decimal("0.00")
        if account.accounting_account:
            lines = JournalEntryLine.objects.filter(
                account=account.accounting_account,
                journal_entry__is_posted=True,
                journal_entry__date__gte=month_start,
                journal_entry__date__lte=month_end,
            )
            system_total = (lines.aggregate(total=Sum("debit"))["total"] or Decimal("0.00")) - (
                lines.aggregate(total=Sum("credit"))["total"] or Decimal("0.00")
            )
        difference = bank_total - system_total

        filename = f"conciliacion-{account.id}-{month_start:%Y-%m}.xls"
        response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("\ufeff<html><head><meta charset='utf-8'></head><body>")
        response.write(f"<h2>Conciliación - {escape(account)} - {month_start:%B %Y}</h2>")
        response.write("<table border='1'>")
        response.write(f"<tr><th>Total banco</th><td>{bank_total:.2f}</td></tr>")
        response.write(f"<tr><th>Total sistema</th><td>{system_total:.2f}</td></tr>")
        response.write(f"<tr><th>Total conciliado</th><td>{reconciled_total:.2f}</td></tr>")
        response.write(f"<tr><th>Total pendiente</th><td>{unreconciled_total:.2f}</td></tr>")
        response.write(f"<tr><th>Diferencia</th><td>{difference:.2f}</td></tr>")
        response.write("</table><br>")
        response.write("<table border='1'><tr><th>Fecha</th><th>Descripción</th><th>Referencia</th><th>Tipo</th><th>Importe</th><th>Póliza</th><th>Estado</th></tr>")
        for movement in movements:
            entry_label = ""
            if movement.journal_entry:
                entry_label = movement.journal_entry.reference or str(movement.journal_entry_id)
            response.write(
                "<tr>"
                f"<td>{movement.date:%Y-%m-%d}</td>"
                f"<td>{escape(movement.description)}</td>"
                f"<td>{escape(movement.reference or '')}</td>"
                f"<td>{escape(movement.get_movement_type_display())}</td>"
                f"<td>{movement.signed_amount:.2f}</td>"
                f"<td>{escape(entry_label)}</td>"
                f"<td>{'Conciliado' if movement.is_reconciled else 'Pendiente'}</td>"
                "</tr>"
            )
        response.write("</table></body></html>")
        return response


@admin.register(BankMovement)
class BankMovementAdmin(admin.ModelAdmin):
    list_display = ("date", "money_account", "description", "movement_type", "amount", "signed_amount_display", "journal_entry", "reconciled_badge")
    list_filter = ("money_account", "movement_type", "is_reconciled", "date")
    search_fields = ("description", "reference", "note", "money_account__name", "journal_entry__concept", "journal_entry__reference")
    autocomplete_fields = ("money_account", "journal_entry", "created_by")
    date_hierarchy = "date"
    actions = [marcar_movimientos_conciliados, marcar_movimientos_no_conciliados]
    fieldsets = (
        ("Movimiento", {
            "fields": ("money_account", "date", "description", "movement_type", "amount", "reference")
        }),
        ("Conciliación", {
            "fields": ("journal_entry", "is_reconciled", "reconciled_at")
        }),
        ("Detalle", {
            "fields": ("note", "created_by")
        }),
    )

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        if obj.is_reconciled and not obj.reconciled_at:
            obj.reconciled_at = timezone.now()
        if not obj.is_reconciled:
            obj.reconciled_at = None
        super().save_model(request, obj, form, change)

    @admin.display(description="Importe firmado")
    def signed_amount_display(self, obj):
        return f"${obj.signed_amount:.2f}"

    @admin.display(description="Conciliado")
    def reconciled_badge(self, obj):
        if obj.is_reconciled:
            return format_html('<span style="display:inline-block;padding:0.25rem 0.6rem;border-radius:999px;background:#dcfce7;color:#166534;font-weight:800;">Sí</span>')
        return format_html('<span style="display:inline-block;padding:0.25rem 0.6rem;border-radius:999px;background:#fee2e2;color:#991b1b;font-weight:800;">No</span>')


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

        created_expense = Expense.objects.create(
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
        _post_expense_journal_entry(created_expense, created_by=expense.created_by or request.user)
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
        _post_expense_journal_entry(obj, created_by=request.user if request.user.is_authenticated else None)

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

        # ── Mercado Libre del mes (si la app está instalada) ──
        ml_sales = Decimal("0.00")
        ml_fees = Decimal("0.00")
        ml_shipping_cost = Decimal("0.00")
        ml_net = Decimal("0.00")
        ml_orders_count = 0
        ml_cancelled_count = 0
        VALID_ML = ("paid", "confirmed", "shipped", "delivered")
        try:
            from mercadolibre.models import MercadoLibreOrder
            ml_qs = MercadoLibreOrder.objects.filter(
                date_created__date__gte=month_start, date_created__date__lte=month_end,
            )
            ml_valid = ml_qs.filter(status__in=VALID_ML)
            ml_sales = ml_valid.aggregate(t=Sum("total_amount"))["t"] or Decimal("0.00")
            ml_fees = ml_valid.aggregate(t=Sum("marketplace_fee"))["t"] or Decimal("0.00")
            ml_shipping_cost = ml_valid.aggregate(t=Sum("shipping_cost"))["t"] or Decimal("0.00")
            ml_net = ml_valid.aggregate(t=Sum("net_received_amount"))["t"] or Decimal("0.00")
            ml_orders_count = ml_valid.count()
            ml_cancelled_count = ml_qs.filter(status__in=("cancelled", "invalid")).count()
        except Exception:
            pass

        estimated_cogs = Decimal("0.00")
        for order in month_orders:
            for item in order.items.all():
                estimated_cogs += _product_unit_cost(item.product) * item.quantity

        # COGS de ML: para cada item de pedido ML válido del mes, busca la publicación
        # enlazada (MercadoLibreListing.producto) y suma el costo unitario × cantidad.
        ml_cogs = Decimal("0.00")
        try:
            from mercadolibre.models import MercadoLibreOrderItem, MercadoLibreListing
            # Mapa item_id (ML) → Producto local (una sola query)
            ml_to_producto = {
                lst.ml_id: lst.producto
                for lst in MercadoLibreListing.objects.select_related("producto").filter(producto__isnull=False)
            }
            ml_items_qs = MercadoLibreOrderItem.objects.filter(
                order__date_created__date__gte=month_start,
                order__date_created__date__lte=month_end,
                order__status__in=VALID_ML,
            ).only("item_id", "quantity")
            for it in ml_items_qs:
                prod = ml_to_producto.get(it.item_id)
                if prod is not None:
                    ml_cogs += _product_unit_cost(prod) * Decimal(str(it.quantity or 0))
        except Exception:
            pass
        web_cogs = estimated_cogs
        estimated_cogs += ml_cogs

        # Métricas combinadas web + ML
        combined_sales = Decimal(str(product_sales)) + ml_sales
        combined_net_sales = Decimal(str(product_sales)) + ml_net  # ML neto (después de fees) + web bruto
        gross_profit = combined_net_sales - estimated_cogs - ml_shipping_cost
        net_profit = gross_profit - total_expenses
        gross_margin = (gross_profit / combined_net_sales * 100) if combined_net_sales else Decimal("0.00")
        net_margin = (net_profit / combined_net_sales * 100) if combined_net_sales else Decimal("0.00")

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
            {"label": "Ventas sitio web", "amount": product_sales, "tone": "ok", "section": "ingresos"},
        ]
        if ml_sales:
            ml_fee_pct = (ml_fees / ml_sales * 100) if ml_sales else Decimal("0.00")
            ml_avg_shipping = (ml_shipping_cost / ml_orders_count) if ml_orders_count else Decimal("0.00")
            income_statement += [
                {"label": "Ventas Mercado Libre (bruto)", "amount": ml_sales, "tone": "ok", "section": "ingresos"},
                {"label": f"Comisión Mercado Libre ({ml_fee_pct:.1f}% efectivo)", "amount": -ml_fees, "tone": "danger", "section": "ingresos", "indent": True},
                {"label": f"Costo envío Mercado Libre (${ml_avg_shipping:.2f}/pedido)", "amount": -ml_shipping_cost, "tone": "warn", "section": "ingresos", "indent": True},
            ]
        income_statement += [
            {"label": "Ingresos netos", "amount": combined_net_sales, "tone": "ok", "section": "subtotal"},
            {"label": "Costo de producto vendido (COGS)", "amount": -estimated_cogs, "tone": "warn", "section": "costos"},
            {"label": "Utilidad bruta", "amount": gross_profit, "tone": "ok" if gross_profit >= 0 else "danger", "section": "subtotal"},
            {"label": "Gastos únicos", "amount": -total_one_time_expenses, "tone": "danger", "section": "gastos"},
            {"label": "Gastos recurrentes", "amount": -total_recurring_expenses, "tone": "danger", "section": "gastos"},
            {"label": "Utilidad neta estimada", "amount": net_profit, "tone": "ok" if net_profit >= 0 else "danger", "section": "total"},
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
            ml_sales=ml_sales,
            ml_fees=ml_fees,
            ml_shipping_cost=ml_shipping_cost,
            ml_net=ml_net,
            ml_orders_count=ml_orders_count,
            ml_cancelled_count=ml_cancelled_count,
            ml_cogs=ml_cogs,
            web_cogs=web_cogs,
            combined_sales=combined_sales,
            combined_net_sales=combined_net_sales,
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


@admin.register(CashRegisterClosure)
class CashRegisterClosureAdmin(admin.ModelAdmin):
    list_display = ("fecha", "total_sistema_display", "total_contado_display", "gastos_efectivo", "diferencia_badge", "closed_by", "created_at")
    list_filter = ("fecha", "closed_by")
    search_fields = ("nota", "closed_by__username")
    autocomplete_fields = ("closed_by",)
    readonly_fields = (
        "efectivo_sistema",
        "tarjeta_sistema",
        "transferencia_sistema",
        "otros_sistema",
        "gastos_efectivo",
        "diferencia",
        "created_at",
    )
    date_hierarchy = "fecha"
    fieldsets = (
        ("Cierre", {
            "fields": ("fecha", "closed_by", "nota")
        }),
        ("Contado", {
            "fields": ("efectivo_contado", "tarjeta_contado", "transferencia_contado", "otros_contado")
        }),
        ("Sistema", {
            "fields": ("efectivo_sistema", "tarjeta_sistema", "transferencia_sistema", "otros_sistema", "gastos_efectivo", "diferencia", "created_at")
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "daily-close/",
                self.admin_site.admin_view(self.daily_close_view),
                name="tienda_cashregisterclosure_daily_close",
            ),
        ]
        return custom_urls + urls

    @admin.display(description="Sistema")
    def total_sistema_display(self, obj):
        return f"${obj.total_sistema:.2f}"

    @admin.display(description="Contado")
    def total_contado_display(self, obj):
        return f"${obj.total_contado:.2f}"

    @admin.display(description="Diferencia")
    def diferencia_badge(self, obj):
        color = "#05603a" if obj.diferencia == 0 else "#b42318"
        bg = "#dcfce7" if obj.diferencia == 0 else "#fee2e2"
        return format_html(
            '<span style="display:inline-block;padding:0.28rem 0.65rem;border-radius:999px;background:{};color:{};font-weight:700;">${}</span>',
            bg,
            color,
            f"{obj.diferencia:.2f}",
        )

    def daily_close_view(self, request):
        today = timezone.localdate()
        target_date = today
        existing_closure = None

        if request.method == "POST":
            form = CashRegisterClosureForm(request.POST)
            if form.is_valid():
                target_date = form.cleaned_data["fecha"]
                metrics = _cash_register_metrics(target_date)
                counted_total = (
                    form.cleaned_data["efectivo_contado"]
                    + form.cleaned_data["tarjeta_contado"]
                    + form.cleaned_data["transferencia_contado"]
                    + form.cleaned_data["otros_contado"]
                )
                expected_total = metrics["system_total"] - metrics["cash_expenses"]
                closure, _ = CashRegisterClosure.objects.update_or_create(
                    fecha=target_date,
                    defaults={
                        "efectivo_contado": form.cleaned_data["efectivo_contado"],
                        "tarjeta_contado": form.cleaned_data["tarjeta_contado"],
                        "transferencia_contado": form.cleaned_data["transferencia_contado"],
                        "otros_contado": form.cleaned_data["otros_contado"],
                        "efectivo_sistema": metrics["cash_system"],
                        "tarjeta_sistema": metrics["card_system"],
                        "transferencia_sistema": metrics["transfer_system"],
                        "otros_sistema": metrics["other_system"],
                        "gastos_efectivo": metrics["cash_expenses"],
                        "diferencia": counted_total - expected_total,
                        "nota": form.cleaned_data.get("nota", ""),
                        "closed_by": request.user if request.user.is_authenticated else None,
                    },
                )
                self.message_user(request, f"Cierre de caja guardado para {target_date}.", level=messages.SUCCESS)
                return HttpResponseRedirect(reverse("admin:tienda_cashregisterclosure_change", args=[closure.id]))
        else:
            raw_date = request.GET.get("fecha")
            try:
                target_date = date.fromisoformat(raw_date) if raw_date else today
            except ValueError:
                target_date = today
            existing_closure = CashRegisterClosure.objects.filter(fecha=target_date).first()
            initial = {
                "fecha": target_date,
                "efectivo_contado": existing_closure.efectivo_contado if existing_closure else 0,
                "tarjeta_contado": existing_closure.tarjeta_contado if existing_closure else 0,
                "transferencia_contado": existing_closure.transferencia_contado if existing_closure else 0,
                "otros_contado": existing_closure.otros_contado if existing_closure else 0,
                "nota": existing_closure.nota if existing_closure else "",
            }
            form = CashRegisterClosureForm(initial=initial)

        metrics = _cash_register_metrics(target_date)
        expected_total = metrics["system_total"] - metrics["cash_expenses"]
        recent_closures = CashRegisterClosure.objects.order_by("-fecha")[:8]
        context = dict(
            self.admin_site.each_context(request),
            title="Cierre de caja",
            subtitle="Cuadra ventas POS, gastos en efectivo y dinero contado",
            form=form,
            metrics=metrics,
            target_date=target_date,
            expected_total=expected_total,
            existing_closure=existing_closure,
            recent_closures=recent_closures,
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/cash_register_closure.html", context)


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


from .models import NewsletterSubscriber

@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "source", "coupon_sent", "created_at")
    list_filter = ("source", "coupon_sent", "created_at")
    search_fields = ("email",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
