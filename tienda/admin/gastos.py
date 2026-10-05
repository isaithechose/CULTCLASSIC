"""Admin de gastos: tarjetas de credito, gastos recurrentes, cortes de caja y pagos."""

from django.contrib import admin
from django.contrib import messages
from django import forms
from django.db import models
from django.db.models import Sum
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
import calendar
from datetime import date, timedelta
from ..models import (
    BusinessPayment,
    CashRegisterClosure,
    CreditCardAccount,
    CreditCardStatement,
    Order,
    ExpenseCategory,
    Expense,
)
from decimal import Decimal
from django.utils import timezone

from .comun import (
    _add_months,
    _build_business_calendar_context,
    _cash_register_metrics,
    _coerce_month,
    _money,
    _next_expense_recurrence_date,
    _post_credit_card_payment_journal_entry,
    _post_expense_journal_entry,
    _product_unit_cost,
)


class CashRegisterClosureForm(forms.Form):
    fecha = forms.DateField(label="Fecha", widget=forms.DateInput(attrs={"type": "date"}))
    efectivo_contado = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10, initial=0, label="Efectivo contado")
    tarjeta_contado = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10, initial=0, label="Tarjeta contado")
    transferencia_contado = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10, initial=0, label="Transferencia contado")
    otros_contado = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10, initial=0, label="Otros contado")
    nota = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Nota")


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
    list_select_related = ("tarjeta",)
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


def _ultima_ocurrencia_recurrente(expense):
    """El gasto mas reciente de esa serie (el origen o el ultimo generado)."""
    origen = expense.gasto_origen or expense
    return (
        Expense.objects.filter(models.Q(pk=origen.pk) | models.Q(gasto_origen=origen))
        .order_by("-fecha", "-id")
        .first()
    ) or expense


def _ocurrencias_pendientes(expense, hasta=None):
    """Fechas que ya se debieron registrar y todavia no existen.

    Cada ocurrencia se calcula desde la fecha ORIGINAL de la serie, no desde la
    ultima generada: si el gasto nace un 31 de enero, la siguiente es el 28 de
    febrero pero la de marzo vuelve al 31. Encadenar una fecha tras otra hacia
    correrse el dia mes con mes.
    """
    hasta = hasta or timezone.localdate()
    origen = expense.gasto_origen or expense
    if not origen.recurrencia_activa or origen.recurrencia == "none":
        return []

    existentes = set(
        Expense.objects.filter(models.Q(pk=origen.pk) | models.Q(gasto_origen=origen))
        .values_list("fecha", flat=True)
    )

    pendientes = []
    for n in range(1, 121):  # tope de seguridad
        if origen.recurrencia == "weekly":
            fecha = origen.fecha + timedelta(days=7 * n)
        elif origen.recurrencia == "monthly":
            fecha = _add_months(origen.fecha, n)
        elif origen.recurrencia == "yearly":
            fecha = _add_months(origen.fecha, 12 * n)
        else:
            break
        if fecha > hasta:
            break
        if origen.recurrencia_fin and fecha > origen.recurrencia_fin:
            break
        if fecha not in existentes:
            pendientes.append(fecha)
    return pendientes


def _generar_ocurrencia(expense, fecha, usuario=None):
    """Crea el gasto de esa fecha con su poliza. Idempotente."""
    origen = expense.gasto_origen or expense
    if Expense.objects.filter(gasto_origen=origen, fecha=fecha, concepto=expense.concepto).exists():
        return None
    nuevo = Expense.objects.create(
        fecha=fecha,
        categoria=expense.categoria,
        concepto=expense.concepto,
        monto=expense.monto,
        metodo_pago=expense.metodo_pago,
        proveedor=expense.proveedor,
        nota=expense.nota,
        recurrencia=expense.recurrencia,
        recurrencia_activa=expense.recurrencia_activa,
        recurrencia_fin=expense.recurrencia_fin,
        gasto_origen=origen,
        created_by=usuario or expense.created_by,
    )
    _post_expense_journal_entry(nuevo, created_by=usuario or expense.created_by)
    return nuevo


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
    change_list_template = "admin/tienda/expense_change_list.html"
    list_select_related = ("categoria", "created_by")
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
            path(
                "recurrentes/",
                self.admin_site.admin_view(self.recurring_expenses_view),
                name="tienda_expense_recurring",
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

    def recurring_expenses_view(self, request):
        """Pantalla de gastos recurrentes: qué se repite y qué falta registrar."""
        hoy = timezone.localdate()

        if request.method == "POST":
            objetivo = request.POST.get("expense_id")
            series = Expense.objects.filter(
                recurrencia_activa=True, gasto_origen__isnull=True
            ).exclude(recurrencia="none")
            if objetivo:
                series = series.filter(pk=objetivo)

            creados = 0
            for serie in series:
                for fecha in _ocurrencias_pendientes(serie, hasta=hoy):
                    if _generar_ocurrencia(serie, fecha, usuario=request.user):
                        creados += 1

            if creados:
                self.message_user(
                    request,
                    f"Se registraron {creados} gasto{'s' if creados != 1 else ''} con su póliza contable.",
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(request, "No había gastos pendientes por registrar.", level=messages.INFO)
            return HttpResponseRedirect(reverse("admin:tienda_expense_recurring"))

        filas = []
        total_pendiente = Decimal("0.00")
        total_mensual = Decimal("0.00")
        for serie in (
            Expense.objects.filter(recurrencia_activa=True, gasto_origen__isnull=True)
            .exclude(recurrencia="none")
            .select_related("categoria")
            .order_by("concepto")
        ):
            ultima = _ultima_ocurrencia_recurrente(serie)
            pendientes = _ocurrencias_pendientes(serie, hasta=hoy)
            generados = Expense.objects.filter(gasto_origen=serie).count()
            if serie.recurrencia == "monthly":
                total_mensual += _money(serie.monto)
            total_pendiente += _money(serie.monto) * len(pendientes)
            filas.append({
                "expense": serie,
                "ultima_fecha": ultima.fecha,
                "generados": generados,
                "pendientes": pendientes,
                "proxima": _next_expense_recurrence_date(ultima),
                "atrasado": bool(pendientes),
            })

        inactivos = (
            Expense.objects.filter(recurrencia_activa=False)
            .exclude(recurrencia="none")
            .select_related("categoria")
            .order_by("-fecha")[:10]
        )

        context = dict(
            self.admin_site.each_context(request),
            title="Gastos recurrentes",
            opts=self.model._meta,
            filas=filas,
            inactivos=inactivos,
            total_pendiente=total_pendiente,
            total_mensual=total_mensual,
            pendientes_totales=sum(len(f["pendientes"]) for f in filas),
            hoy=hoy,
        )
        return TemplateResponse(request, "admin/tienda/recurring_expenses.html", context)

    def accounting_dashboard_view(self, request):
        today = timezone.localdate()
        year, month = _coerce_month(request.GET.get("month"), today)
        month_start = date(year, month, 1)
        _, month_days = calendar.monthrange(year, month)
        month_end = month_start.replace(day=month_days)
        previous_month = (month_start - timedelta(days=1)).strftime("%Y-%m")
        next_month = (month_end + timedelta(days=1)).strftime("%Y-%m")
        completed_orders = list(Order.objects.filter(status="Completed").prefetch_related("items__product"))
        month_orders = [order for order in completed_orders if month_start <= timezone.localdate(order.created_at) <= month_end]

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
                estimated_cogs += item.effective_unit_cost * item.quantity

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
    list_select_related = ("closed_by",)
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
