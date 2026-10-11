"""Helpers compartidos del admin: dinero, polizas contables, resumen y calendario."""

from django.core.cache import cache
from django.db.models import Case, DecimalField, ExpressionWrapper, F, Sum, Value, When
from django.urls import reverse
import calendar
from datetime import date, timedelta
from ..sat import CODIGO_SAT_POR_CUENTA
from ..models import (
    BusinessPayment,
    AccountingAccount,
    AccountingPeriodClose,
    CreditCardStatement,
    JournalEntry,
    JournalEntryLine,
    Order,
    Producto,
    ProductVariant,
    InventoryMovement,
    Expense,
)
from decimal import Decimal
from django.utils import timezone


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
        defaults={"name": name, "account_type": account_type, "sat_code_id": CODIGO_SAT_POR_CUENTA.get(code)},
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
        # el mismo costo que se congela en OrderItem.unit_cost
        unit_cost = line.get("unit_cost")
        if unit_cost is None:
            unit_cost = _product_unit_cost(line["product"])
        cogs += _money(unit_cost) * Decimal(str(line["quantity"]))

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
        date_value=timezone.localdate(order.created_at) if order.created_at else timezone.localdate(),
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
    # La compra de mercancía capitaliza inventario aunque se capture como gasto.
    es_compra_inventario = bool(expense.categoria and expense.categoria.nombre == "Compras inventario")
    debit_account = _account("1100") if es_compra_inventario else _account("6000")
    return _create_balanced_journal_entry(
        date_value=expense.fecha,
        entry_type="expense",
        source="expense",
        concept=f"Gasto: {expense.concepto}",
        reference=f"EXP-{expense.id}",
        expense=expense,
        lines=[
            {"account": debit_account, "debit": amount, "description": expense.concepto},
            {"account": credit_account, "credit": amount, "description": expense.get_metodo_pago_display()},
        ],
        created_by=created_by or expense.created_by,
    )


# Formas de pago de una recepción de compra y la cuenta que se abona.
PURCHASE_PAYMENT_CHOICES = [
    ("transfer", "Transferencia / banco"),
    ("cash", "Efectivo"),
    ("card", "Tarjeta de crédito"),
    ("credit", "A crédito con el proveedor"),
]
PURCHASE_PAYMENT_ACCOUNTS = {
    "transfer": "1010",   # Bancos
    "cash": "1000",       # Caja
    "card": "2100",       # Tarjetas de credito por pagar
    "credit": "2000",     # Proveedores
}


def _post_purchase_journal_entry(*, date_value, amount, payment_method, supplier="", note="", reference="", created_by=None):
    """Capitaliza la compra: cargo a Inventario contra la forma de pago.

    Una compra de mercancía no es un gasto: se vuelve costo cuando se vende, y
    ahí la póliza de la venta ya carga 5000 Costo de ventas contra 1100
    Inventario. Mandarla a gastos al comprar duplicaba el costo y dejaba la
    cuenta de inventario en negativo.
    """
    amount = _money(amount)
    if amount <= 0:
        return None
    credit_code = PURCHASE_PAYMENT_ACCOUNTS.get(payment_method, "1010")
    payment_label = dict(PURCHASE_PAYMENT_CHOICES).get(payment_method, payment_method)
    concept = "Compra de inventario"
    if supplier:
        concept = f"{concept} — {supplier}"
    return _create_balanced_journal_entry(
        date_value=date_value,
        entry_type="diary",
        source="inventory",
        concept=concept[:180],
        reference=reference,
        lines=[
            {"account": _account("1100"), "debit": amount, "description": note[:180] or "Recepción de compra"},
            {"account": _account(credit_code), "credit": amount, "description": payment_label},
        ],
        created_by=created_by,
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


ADMIN_OVERVIEW_CACHE_KEY = "cc_admin_overview_v1"
ADMIN_OVERVIEW_CACHE_TTL = 30


def _admin_overview_context():
    """Resumen del admin (contadores, vision board) cacheado unos segundos.

    Lo consumen el context processor —o sea, TODAS las páginas del admin— y la
    portada, que además lo pedía dos veces por carga. Son ~35 consultas, así que
    se cachea; las señales de tienda.signals lo invalidan en cuanto hay una
    venta, un gasto o un movimiento de inventario.
    """
    cached = cache.get(ADMIN_OVERVIEW_CACHE_KEY)
    if cached is not None:
        return cached
    data = _compute_admin_overview_context()
    cache.set(ADMIN_OVERVIEW_CACHE_KEY, data, ADMIN_OVERVIEW_CACHE_TTL)
    return data


def _compute_admin_overview_context():
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
            today_cogs += item.effective_unit_cost * item.quantity
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
            {"label": "Resumen de ventas", "url": reverse("admin:tienda_producto_sales_summary"), "description": "Piezas vendidas, top colores/tallas y recomendación de compra (90 días).", "tone": "info"},
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
                    {"label": "Resumen de ventas (90d)", "url": reverse("admin:tienda_producto_sales_summary")},
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
        day = timezone.localdate(order.created_at)
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
