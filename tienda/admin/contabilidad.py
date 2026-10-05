"""Admin contable: catalogo de cuentas, polizas, cierres, cuentas de dinero y bancos."""

from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django import forms
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import escape, format_html
import calendar
from datetime import date, timedelta
from ..models import (
    AccountingAccount,
    AccountingPeriodClose,
    CreditCardStatement,
    JournalEntry,
    JournalEntryLine,
    MoneyAccount,
    BankMovement,
    Order,
    ProductVariant,
    InventoryMovement,
    Expense,
    SatGroupingCode,
)
from decimal import Decimal, InvalidOperation
from django.utils import timezone

from .comun import (
    _coerce_month,
    _is_accounting_period_closed,
    _money,
    _month_range_for_date,
    _post_credit_card_payment_journal_entry,
    _post_expense_journal_entry,
    _post_order_journal_entry,
)


@admin.register(SatGroupingCode)
class SatGroupingCodeAdmin(admin.ModelAdmin):
    """Catalogo oficial: se consulta y se liga a las cuentas, no se edita."""

    list_display = ("code", "name", "level", "accounts_count")
    list_filter = ("level",)
    search_fields = ("code", "name")

    @admin.display(description="Cuentas ligadas")
    def accounts_count(self, obj):
        return obj.accounts.count() or ""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SatCodeAssignedFilter(admin.SimpleListFilter):
    title = "código SAT"
    parameter_name = "sat"

    def lookups(self, request, model_admin):
        return (("si", "Asignado"), ("no", "Sin asignar"))

    def queryset(self, request, queryset):
        if self.value() == "si":
            return queryset.filter(sat_code__isnull=False)
        if self.value() == "no":
            return queryset.filter(sat_code__isnull=True)
        return queryset


@admin.register(AccountingAccount)
class AccountingAccountAdmin(admin.ModelAdmin):
    list_select_related = ("parent", "sat_code")
    list_display = ("code", "name", "account_type", "sat_code", "parent", "is_active", "ledger_link")
    list_filter = ("account_type", "is_active", SatCodeAssignedFilter)
    search_fields = ("code", "name", "sat_code__code", "sat_code__name")
    list_editable = ("is_active",)
    autocomplete_fields = ("parent", "sat_code")
    fieldsets = (
        ("Cuenta", {
            "fields": ("code", "name", "account_type", "sat_code", "parent", "is_active")
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
    list_per_page = 50
    show_full_result_count = False
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
                "cierre-mensual/",
                self.admin_site.admin_view(self.month_close_view),
                name="tienda_journalentry_month_close",
            ),
            path(
                "contabilidad-electronica/",
                self.admin_site.admin_view(self.sat_xml_view),
                name="tienda_journalentry_sat_xml",
            ),
            path(
                "pago-tarjeta/",
                self.admin_site.admin_view(self.card_payment_view),
                name="tienda_journalentry_card_payment",
            ),
            path(
                "finanzas/",
                self.admin_site.admin_view(self.finance_dashboard_view),
                name="tienda_journalentry_finance_dashboard",
            ),
            path(
                "flujo-de-efectivo/",
                self.admin_site.admin_view(self.cash_flow_view),
                name="tienda_journalentry_cash_flow",
            ),
            path(
                "razones-financieras/",
                self.admin_site.admin_view(self.financial_ratios_view),
                name="tienda_journalentry_financial_ratios",
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

    def get_queryset(self, request):
        # total_debit / total_credit / is_balanced recorren entry.lines:
        # sin prefetch son tres consultas por fila del listado.
        return super().get_queryset(request).prefetch_related("lines")

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

    # ── Finanzas ────────────────────────────────────────────────────────────
    # Cuentas que representan dinero disponible. El flujo de efectivo mira los
    # movimientos de estas cuentas y clasifica cada uno por su contrapartida.
    CASH_CODES = ("1000", "1010")

    # Clasificación del flujo por la cuenta de contrapartida.
    FLOW_CATEGORIES = {
        "4000": ("operacion", "Cobros de venta"),
        "4010": ("operacion", "Descuentos sobre ventas"),
        "4020": ("operacion", "Cobros por envío"),
        "1020": ("operacion", "Cobros de terminal"),
        "1200": ("operacion", "Cobranza a clientes"),
        "1100": ("operacion", "Compra de inventario"),
        "5000": ("operacion", "Costo de ventas pagado"),
        "6000": ("operacion", "Gastos de operación"),
        "2000": ("operacion", "Pago a proveedores"),
        "2100": ("financiamiento", "Tarjetas de crédito"),
        "3000": ("financiamiento", "Aportaciones y retiros de capital"),
    }
    FLOW_SECTION_LABELS = {
        "operacion": "Actividades de operación",
        "inversion": "Actividades de inversión",
        "financiamiento": "Actividades de financiamiento",
    }

    def _cash_balance_at(self, date_to, date_from=None):
        """Saldo (o variación) de las cuentas de efectivo hasta una fecha."""
        queryset = JournalEntryLine.objects.filter(
            journal_entry__is_posted=True,
            account__code__in=self.CASH_CODES,
            journal_entry__date__lte=date_to,
        )
        if date_from:
            queryset = queryset.filter(journal_entry__date__gte=date_from)
        totals = queryset.aggregate(d=Sum("debit"), c=Sum("credit"))
        return _money(totals["d"]) - _money(totals["c"])

    def _cash_flow_data(self, bounds):
        start, end = bounds["month_start"], bounds["month_end"]
        opening = self._cash_balance_at(start - timedelta(days=1))

        # Pólizas del periodo que tocan efectivo, con todas sus líneas.
        entries = (
            JournalEntry.objects.filter(
                is_posted=True,
                date__gte=start,
                date__lte=end,
                lines__account__code__in=self.CASH_CODES,
            )
            .distinct()
            .prefetch_related("lines__account")
            .order_by("date", "id")
        )

        sections = {key: {} for key in self.FLOW_SECTION_LABELS}
        sin_clasificar = []
        total_in = Decimal("0.00")
        total_out = Decimal("0.00")

        for entry in entries:
            cash_delta = Decimal("0.00")
            counterparts = []
            for line in entry.lines.all():
                if line.account.code in self.CASH_CODES:
                    cash_delta += line.debit - line.credit
                else:
                    counterparts.append(line)
            if cash_delta == 0:
                continue

            # La contrapartida de mayor importe define la categoría.
            counterparts.sort(key=lambda l: max(l.debit, l.credit), reverse=True)
            code = counterparts[0].account.code if counterparts else ""
            section, label = self.FLOW_CATEGORIES.get(code, ("operacion", "Otros movimientos"))
            if not counterparts:
                sin_clasificar.append(entry)

            bucket = sections[section].setdefault(
                label, {"label": label, "inflow": Decimal("0.00"), "outflow": Decimal("0.00"), "count": 0}
            )
            if cash_delta > 0:
                bucket["inflow"] += cash_delta
                total_in += cash_delta
            else:
                bucket["outflow"] += -cash_delta
                total_out += -cash_delta
            bucket["count"] += 1

        flow_sections = []
        for key, label in self.FLOW_SECTION_LABELS.items():
            rows = sorted(sections[key].values(), key=lambda r: r["inflow"] + r["outflow"], reverse=True)
            for row in rows:
                row["net"] = row["inflow"] - row["outflow"]
            net = sum((row["net"] for row in rows), Decimal("0.00"))
            flow_sections.append({"key": key, "label": label, "rows": rows, "net": net})

        net_change = total_in - total_out
        closing = opening + net_change
        closing_real = self._cash_balance_at(end)

        # ── Método indirecto: de la utilidad al efectivo ──
        statement = self._income_statement_data(bounds)
        working_capital_codes = {
            "1100": "Inventario",
            "1200": "Clientes",
            "2000": "Proveedores",
            "2100": "Tarjetas de crédito",
        }
        adjustments = []
        for code, name in working_capital_codes.items():
            totals = JournalEntryLine.objects.filter(
                journal_entry__is_posted=True,
                journal_entry__date__gte=start,
                journal_entry__date__lte=end,
                account__code=code,
            ).aggregate(d=Sum("debit"), c=Sum("credit"))
            variation = _money(totals["d"]) - _money(totals["c"])
            if variation == 0:
                continue
            account = AccountingAccount.objects.filter(code=code).first()
            is_asset = account is not None and account.account_type == "asset"
            # Más activo circulante consume efectivo; más pasivo lo libera.
            effect = -variation if is_asset else variation
            adjustments.append({
                "name": name,
                "variation": variation,
                "effect": effect,
                "hint": ("Aumento de activo: consume efectivo" if is_asset and variation > 0 else
                         "Disminución de activo: libera efectivo" if is_asset else
                         "Aumento de pasivo: libera efectivo" if variation > 0 else
                         "Pago de pasivo: consume efectivo"),
            })
        indirect_total = statement["net_profit"] + sum((a["effect"] for a in adjustments), Decimal("0.00"))

        return {
            "flow_sections": flow_sections,
            "flow_opening": opening,
            "flow_total_in": total_in,
            "flow_total_out": total_out,
            "flow_net_change": net_change,
            "flow_closing": closing,
            "flow_closing_real": closing_real,
            "flow_check_ok": abs(closing - closing_real) < Decimal("0.01"),
            "flow_unclassified": len(sin_clasificar),
            "flow_net_profit": statement["net_profit"],
            "flow_adjustments": adjustments,
            "flow_indirect_total": indirect_total,
            "flow_indirect_matches": abs(indirect_total - net_change) < Decimal("0.01"),
        }

    def _ratio(self, numerator, denominator):
        if not denominator:
            return None
        return Decimal(str(numerator)) / Decimal(str(denominator))

    def _financial_ratios_data(self, bounds):
        start, end = bounds["month_start"], bounds["month_end"]
        balance = self._balance_sheet_data(bounds)
        statement = self._income_statement_data(bounds)

        def saldo(code, date_to=None):
            totals = JournalEntryLine.objects.filter(
                journal_entry__is_posted=True,
                account__code=code,
                journal_entry__date__lte=date_to or end,
            ).aggregate(d=Sum("debit"), c=Sum("credit"))
            account = AccountingAccount.objects.filter(code=code).first()
            debit, credit = _money(totals["d"]), _money(totals["c"])
            if account is not None and account.account_type in ("liability", "equity", "income"):
                return credit - debit
            return debit - credit

        efectivo = self._cash_balance_at(end)
        inventario = saldo("1100")
        clientes = saldo("1200")
        terminal = saldo("1020")
        proveedores = saldo("2000")
        tarjetas = saldo("2100")

        activo_circulante = efectivo + inventario + clientes + terminal
        pasivo_corto = proveedores + tarjetas
        capital_trabajo = activo_circulante - pasivo_corto

        ingresos = statement["total_income"]
        costo = statement["total_cost"]
        gastos = statement["total_expense"]
        utilidad_bruta = statement["gross_profit"]
        utilidad_neta = statement["net_profit"]
        activos = balance["total_assets"]
        pasivos = balance["total_liabilities"]
        capital = balance["total_equity"] + balance["accumulated_result"]

        dias = (end - start).days + 1
        margen_contribucion = self._ratio(utilidad_bruta, ingresos)

        grupos = [
            {
                "titulo": "Liquidez",
                "descripcion": "Si el negocio puede cubrir lo que debe en el corto plazo.",
                "razones": [
                    {"nombre": "Razón corriente", "formula": "Activo circulante ÷ Pasivo corto plazo",
                     "valor": self._ratio(activo_circulante, pasivo_corto), "formato": "veces",
                     "referencia": "Sano arriba de 1.5",
                     "lectura": "Por cada peso que debes a corto plazo, tienes este tanto disponible."},
                    {"nombre": "Prueba ácida", "formula": "(Activo circulante − Inventario) ÷ Pasivo corto plazo",
                     "valor": self._ratio(activo_circulante - inventario, pasivo_corto), "formato": "veces",
                     "referencia": "Sano arriba de 1.0",
                     "lectura": "Lo mismo, pero sin contar con vender el inventario."},
                    {"nombre": "Capital de trabajo", "formula": "Activo circulante − Pasivo corto plazo",
                     "valor": capital_trabajo, "formato": "dinero",
                     "referencia": "Debe ser positivo",
                     "lectura": "Lo que te queda si pagas hoy todo lo de corto plazo."},
                ],
            },
            {
                "titulo": "Rentabilidad",
                "descripcion": "Cuánto de lo que vendes se convierte en utilidad.",
                "razones": [
                    {"nombre": "Margen bruto", "formula": "Utilidad bruta ÷ Ingresos",
                     "valor": self._ratio(utilidad_bruta, ingresos), "formato": "porcentaje",
                     "referencia": "En ropa, 50% o más",
                     "lectura": "De cada peso vendido, esto queda después del costo del producto."},
                    {"nombre": "Margen neto", "formula": "Utilidad neta ÷ Ingresos",
                     "valor": self._ratio(utilidad_neta, ingresos), "formato": "porcentaje",
                     "referencia": "Depende del giro",
                     "lectura": "De cada peso vendido, esto queda ya con todos los gastos."},
                    {"nombre": "Peso de los gastos", "formula": "Gastos ÷ Ingresos",
                     "valor": self._ratio(gastos, ingresos), "formato": "porcentaje",
                     "referencia": "Mientras más bajo, mejor",
                     "lectura": "Qué parte de tus ventas se va en gastos de operación."},
                    {"nombre": "Rendimiento sobre activos", "formula": "Utilidad neta ÷ Activo total",
                     "valor": self._ratio(utilidad_neta, activos), "formato": "porcentaje",
                     "referencia": "Comparar contra meses previos",
                     "lectura": "Cuánta utilidad saca el negocio de todo lo que tiene invertido."},
                ],
            },
            {
                "titulo": "Endeudamiento",
                "descripcion": "Qué parte del negocio se financia con deuda.",
                "razones": [
                    {"nombre": "Deuda sobre activos", "formula": "Pasivo total ÷ Activo total",
                     "valor": self._ratio(pasivos, activos), "formato": "porcentaje",
                     "referencia": "Prudente abajo de 50%",
                     "lectura": "Qué porcentaje de lo que tienes le pertenece a terceros."},
                    {"nombre": "Deuda sobre capital", "formula": "Pasivo total ÷ Capital contable",
                     "valor": self._ratio(pasivos, capital), "formato": "veces",
                     "referencia": "Prudente abajo de 1.0",
                     "lectura": "Cuántos pesos debes por cada peso propio metido en el negocio."},
                ],
            },
            {
                "titulo": "Actividad",
                "descripcion": "Qué tan rápido se mueve el inventario y el dinero.",
                "razones": [
                    {"nombre": "Rotación de inventario", "formula": "Costo de ventas ÷ Inventario",
                     "valor": self._ratio(costo, inventario) if inventario > 0 else None, "formato": "veces",
                     "referencia": "Más alto es mejor",
                     "lectura": "Cuántas veces vendiste tu inventario completo en el periodo."},
                    {"nombre": "Días de inventario", "formula": "Días del periodo ÷ Rotación",
                     "valor": (self._ratio(Decimal(dias), self._ratio(costo, inventario))
                               if inventario > 0 and costo else None), "formato": "dias",
                     "referencia": "Mientras menos, mejor",
                     "lectura": "Cuántos días tarda en venderse lo que tienes en bodega."},
                    {"nombre": "Días de cobro", "formula": "(Clientes ÷ Ingresos) × Días",
                     "valor": (self._ratio(clientes, ingresos) * Decimal(dias)) if ingresos and clientes else None,
                     "formato": "dias", "referencia": "Mientras menos, mejor",
                     "lectura": "Cuánto tardas en cobrar lo que vendes a crédito."},
                    {"nombre": "Días de pago", "formula": "(Proveedores ÷ Costo de ventas) × Días",
                     "valor": (self._ratio(proveedores, costo) * Decimal(dias)) if costo and proveedores else None,
                     "formato": "dias", "referencia": "Más días te dan aire",
                     "lectura": "Cuánto tardas en pagarle a tus proveedores."},
                ],
            },
            {
                "titulo": "Punto de equilibrio",
                "descripcion": "Cuánto necesitas vender para no perder.",
                "razones": [
                    {"nombre": "Ventas de equilibrio", "formula": "Gastos ÷ Margen bruto",
                     "valor": (self._ratio(gastos, margen_contribucion) if margen_contribucion else None),
                     "formato": "dinero", "referencia": f"Vendiste ${ingresos:,.2f}",
                     "lectura": "Con este nivel de ventas cubres costo y gastos, sin ganar ni perder."},
                    {"nombre": "Margen de seguridad", "formula": "(Ventas − Equilibrio) ÷ Ventas",
                     "valor": (self._ratio(ingresos - (gastos / margen_contribucion), ingresos)
                               if margen_contribucion and ingresos else None),
                     "formato": "porcentaje", "referencia": "Mientras más alto, mejor",
                     "lectura": "Cuánto pueden caer tus ventas antes de empezar a perder."},
                ],
            },
        ]

        # Con inventario negativo (compras que no capitalizan) varias razones
        # mienten: la prueba ácida sale MAYOR que la razón corriente porque
        # restar un negativo suma. Se marcan en vez de presentarlas como buenas.
        inventario_confiable = inventario >= 0
        afectadas_por_inventario = {
            "Razón corriente", "Prueba ácida", "Capital de trabajo",
            "Rotación de inventario", "Días de inventario",
            "Rendimiento sobre activos", "Deuda sobre activos", "Deuda sobre capital",
        }
        if not inventario_confiable:
            for grupo in grupos:
                for razon in grupo["razones"]:
                    if razon["nombre"] in afectadas_por_inventario:
                        razon["afectada"] = "El inventario tiene saldo negativo: este número no es confiable."

        self._format_ratios(grupos)

        return {
            "ratio_groups": grupos,
            "inventario_confiable": inventario_confiable,
            "ratio_base": {
                "efectivo": efectivo, "inventario": inventario, "clientes": clientes,
                "terminal": terminal, "proveedores": proveedores, "tarjetas": tarjetas,
                "activo_circulante": activo_circulante, "pasivo_corto": pasivo_corto,
                "capital_trabajo": capital_trabajo, "ingresos": ingresos, "costo": costo,
                "gastos": gastos, "utilidad_bruta": utilidad_bruta, "utilidad_neta": utilidad_neta,
                "activos": activos, "pasivos": pasivos, "capital": capital, "dias": dias,
            },
        }

    # Umbrales para colorear. Solo se pinta lo que tiene una lectura clara;
    # el resto queda neutro para no inventar semáforos.
    RATIO_THRESHOLDS = {
        "Razón corriente": (Decimal("1.5"), Decimal("1"), "mayor"),
        "Prueba ácida": (Decimal("1"), Decimal("0.8"), "mayor"),
        "Capital de trabajo": (Decimal("0.01"), Decimal("0"), "mayor"),
        "Margen bruto": (Decimal("0.5"), Decimal("0.3"), "mayor"),
        "Margen neto": (Decimal("0.1"), Decimal("0"), "mayor"),
        "Peso de los gastos": (Decimal("0.3"), Decimal("0.5"), "menor"),
        "Deuda sobre activos": (Decimal("0.5"), Decimal("0.7"), "menor"),
        "Deuda sobre capital": (Decimal("1"), Decimal("2"), "menor"),
        "Margen de seguridad": (Decimal("0.3"), Decimal("0"), "mayor"),
    }

    def _format_ratios(self, grupos):
        for grupo in grupos:
            for razon in grupo["razones"]:
                valor = razon.get("valor")
                formato = razon.get("formato")
                if valor is None:
                    razon["display"] = "Sin dato"
                    razon["tono"] = "muted"
                    razon["detalle_sin_dato"] = True
                    continue
                valor = Decimal(str(valor))
                if formato == "porcentaje":
                    razon["display"] = f"{valor * 100:.1f}%"
                elif formato == "veces":
                    razon["display"] = f"{valor:.2f}×"
                elif formato == "dias":
                    razon["display"] = f"{valor:.0f} días"
                else:
                    razon["display"] = f"${valor:,.2f}"

                if razon.get("afectada"):
                    # no se pinta de verde algo calculado sobre datos rotos
                    razon["tono"] = "warn"
                    continue

                bien, regular, sentido = self.RATIO_THRESHOLDS.get(razon["nombre"], (None, None, None))
                if bien is None:
                    razon["tono"] = "neutral"
                elif sentido == "mayor":
                    razon["tono"] = "ok" if valor >= bien else ("warn" if valor >= regular else "danger")
                else:
                    razon["tono"] = "ok" if valor <= bien else ("warn" if valor <= regular else "danger")

    def _finance_health_checks(self, bounds):
        """Avisos sobre datos que harían mentir a los estados financieros."""
        end = bounds["month_end"]
        avisos = []

        balance = self._balance_sheet_data(bounds)
        if abs(balance["difference"]) >= Decimal("0.01"):
            avisos.append({
                "tono": "danger",
                "titulo": "El balance no cuadra",
                "detalle": f"Activo y pasivo+capital difieren en ${balance['difference']:,.2f}.",
                "url": reverse("admin:tienda_journalentry_unbalanced"),
                "accion": "Ver pólizas descuadradas",
            })

        inventario = JournalEntryLine.objects.filter(
            journal_entry__is_posted=True, account__code="1100", journal_entry__date__lte=end
        ).aggregate(d=Sum("debit"), c=Sum("credit"))
        cargos, abonos = _money(inventario["d"]), _money(inventario["c"])
        saldo_inv = cargos - abonos
        if saldo_inv < 0 and cargos > 0:
            avisos.append({
                "tono": "warn",
                "titulo": "Inventario negativo por arrastre histórico",
                "detalle": (
                    f"La cuenta 1100 tiene ${saldo_inv:,.2f}. Las compras nuevas ya capitalizan "
                    "inventario, pero el saldo viene en negativo de las compras viejas que se "
                    "registraron como gasto. Se corrige con una póliza de ajuste."
                ),
                "url": reverse("admin:tienda_journalentry_add"),
                "accion": "Crear póliza de ajuste",
            })
        elif saldo_inv < 0:
            avisos.append({
                "tono": "danger",
                "titulo": "Inventario con saldo negativo",
                "detalle": (
                    f"La cuenta 1100 tiene ${saldo_inv:,.2f}: salen costos de venta pero las compras "
                    "no entran a inventario, se registran directo como gasto. Eso duplica el costo en "
                    "el estado de resultados y deja el balance mal."
                ),
                "url": reverse("admin:tienda_inventorymovement_receive_purchase"),
                "accion": "Ver recepción de compra",
            })
        elif cargos == 0 and abonos > 0:
            avisos.append({
                "tono": "warn",
                "titulo": "Las compras no capitalizan inventario",
                "detalle": "Nunca se ha cargado la cuenta 1100; el inventario no aparece como activo.",
                "url": reverse("admin:tienda_inventorymovement_receive_purchase"),
                "accion": "Ver recepción de compra",
            })

        sin_contabilizar = JournalEntry.objects.filter(is_posted=False).count()
        if sin_contabilizar:
            avisos.append({
                "tono": "warn",
                "titulo": f"{sin_contabilizar} póliza(s) sin contabilizar",
                "detalle": "No entran a los estados financieros hasta marcarlas como contabilizadas.",
                "url": reverse("admin:tienda_journalentry_changelist") + "?is_posted__exact=0",
                "accion": "Revisar",
            })

        capital = JournalEntryLine.objects.filter(
            journal_entry__is_posted=True, account__code="3000"
        ).exists()
        if not capital:
            avisos.append({
                "tono": "info",
                "titulo": "Sin aportación de capital registrada",
                "detalle": "La cuenta 3000 nunca se ha usado, así que el capital contable sale solo del resultado acumulado.",
                "url": reverse("admin:tienda_journalentry_add"),
                "accion": "Registrar póliza",
            })
        return avisos

    def _month_close_checklist(self, bounds):
        """Qué falta para dar por cerrado el mes.

        Cada punto revisa un hecho del negocio contra su registro contable: si
        vendiste, debe haber póliza; si compraste, debe estar en inventario; si
        le abonaste a la tarjeta, debe verse. La idea es que el mes se cierre
        mirando esta lista, no la memoria.
        """
        from mercadolibre.models import MercadoLibreOrder
        from ..accounting import ML_ESTADOS_VALIDOS, ML_REFERENCIA

        inicio, fin = bounds["month_start"], bounds["month_end"]
        puntos = []

        def punto(titulo, ok, detalle, url=None, accion=None, tono_falla="danger"):
            puntos.append({
                "titulo": titulo,
                "estado": "ok" if ok else tono_falla,
                "detalle": detalle,
                "url": url,
                "accion": accion,
            })

        # ── Ventas propias (punto de venta y tienda) ──
        pedidos = Order.objects.filter(
            status="Completed", created_at__date__gte=inicio, created_at__date__lte=fin
        )
        sin_poliza = pedidos.filter(journal_entries__isnull=True).distinct().count()
        punto(
            "Ventas del mes registradas",
            sin_poliza == 0,
            (f"{pedidos.count()} pedidos completados, todos con su póliza."
             if sin_poliza == 0 else
             f"{sin_poliza} de {pedidos.count()} pedidos no tienen póliza contable."),
            reverse("admin:tienda_order_changelist") + "?status__exact=Completed",
            "Ver pedidos",
        )

        # ── Ventas de Mercado Libre ──
        ml = MercadoLibreOrder.objects.filter(
            status__in=ML_ESTADOS_VALIDOS,
            date_created__date__gte=inicio,
            date_created__date__lte=fin,
        )
        ml_sin = [o for o in ml if not JournalEntry.objects.filter(reference=ML_REFERENCIA.format(ml_id=o.ml_id)).exists()]
        punto(
            "Ventas de Mercado Libre registradas",
            not ml_sin,
            (f"{ml.count()} pedidos de ML, todos contabilizados." if not ml_sin else
             f"{len(ml_sin)} pedidos de ML sin póliza. Corre la sincronización para registrarlos."),
            reverse("admin:mercadolibre_mercadolibreorder_changelist"),
            "Ver pedidos ML",
        )

        # ── Compras recibidas ──
        recepciones = InventoryMovement.objects.filter(
            movement_type="purchase", created_at__date__gte=inicio, created_at__date__lte=fin
        )
        # Lo que importa no es de qué pantalla salió la póliza sino que la
        # mercancía haya entrado a la cuenta de inventario en el periodo.
        entradas_inventario = _money(
            JournalEntryLine.objects.filter(
                journal_entry__is_posted=True,
                journal_entry__date__gte=inicio,
                journal_entry__date__lte=fin,
                account__code="1100",
            ).aggregate(d=Sum("debit"))["d"]
        )
        piezas = recepciones.aggregate(s=Sum("quantity_change"))["s"] or 0
        punto(
            "Compras capitalizadas",
            not (recepciones.exists() and entradas_inventario <= 0),
            (f"{recepciones.count()} recepciones ({piezas} piezas) y ${entradas_inventario:,.2f} cargados a inventario."
             + (" Si esa mercancía se compró en un mes anterior, está bien." if entradas_inventario <= 0 else "")
             if recepciones.exists() else
             (f"No hubo recepciones; entraron ${entradas_inventario:,.2f} a inventario."
              if entradas_inventario else "No hubo recepciones de mercancía este mes.")),
            reverse("admin:tienda_inventorymovement_receive_purchase"),
            "Ir a recepción",
            tono_falla="warn",
        )

        # ── Gastos ──
        gastos_mes = Expense.objects.filter(fecha__gte=inicio, fecha__lte=fin)
        mes_previo_fin = inicio - timedelta(days=1)
        mes_previo_inicio = mes_previo_fin.replace(day=1)
        conceptos_previos = set(
            Expense.objects.filter(fecha__gte=mes_previo_inicio, fecha__lte=mes_previo_fin)
            .values_list("concepto", flat=True)
        )
        conceptos_mes = set(gastos_mes.values_list("concepto", flat=True))
        faltantes = sorted(conceptos_previos - conceptos_mes)
        gastos_contables = _money(
            JournalEntryLine.objects.filter(
                journal_entry__is_posted=True,
                journal_entry__date__gte=inicio,
                journal_entry__date__lte=fin,
                account__account_type="expense",
            ).aggregate(d=Sum("debit"))["d"]
        )
        punto(
            "Gastos del mes capturados",
            not faltantes,
            (f"{gastos_mes.count()} gastos capturados, ${gastos_contables:,.2f} en total."
             if not faltantes else
             "El mes pasado registraste gastos que este mes no aparecen: " + ", ".join(faltantes[:4])),
            reverse("admin:tienda_expense_changelist"),
            "Ver gastos",
            tono_falla="warn",
        )

        # ── Tarjeta ──
        deuda = self._saldo_cuenta("2100", naturaleza="acreedora", date_to=fin)
        pagos_mes = JournalEntry.objects.filter(
            source="credit_card", is_posted=True, date__gte=inicio, date__lte=fin
        )
        total_pagos = sum((e.total_debit for e in pagos_mes), Decimal("0.00"))
        punto(
            "Pagos a la tarjeta registrados",
            bool(pagos_mes) or deuda <= 0,
            (f"{pagos_mes.count()} pagos por ${total_pagos:,.2f} este mes." if pagos_mes else
             f"No registraste ningún abono y debes ${deuda:,.2f}. Si pagaste, captúralo para que la deuda baje."),
            reverse("admin:tienda_journalentry_card_payment"),
            "Registrar pago",
            tono_falla="warn",
        )

        # ── Dinero de Mercado Libre pendiente ──
        por_cobrar = self._saldo_cuenta("1030", date_to=fin)
        punto(
            "Depósitos de Mercado Libre aplicados",
            por_cobrar <= 0,
            ("No hay dinero de ML pendiente de registrar." if por_cobrar <= 0 else
             f"Quedan ${por_cobrar:,.2f} en 'Mercado Libre por cobrar'. Si ya te depositaron, registra la entrada."),
            reverse("admin:tienda_journalentry_add"),
            "Registrar depósito",
            tono_falla="warn",
        )

        # ── Pólizas en orden ──
        sin_contabilizar = JournalEntry.objects.filter(is_posted=False, date__gte=inicio, date__lte=fin).count()
        punto(
            "Pólizas contabilizadas",
            sin_contabilizar == 0,
            ("Todas las pólizas del mes están contabilizadas." if sin_contabilizar == 0 else
             f"{sin_contabilizar} pólizas siguen como borrador y no entran a los estados."),
            reverse("admin:tienda_journalentry_changelist") + "?is_posted__exact=0",
            "Revisar",
        )

        descuadradas = [
            e for e in JournalEntry.objects.filter(is_posted=True, date__gte=inicio, date__lte=fin).prefetch_related("lines")
            if not e.is_balanced
        ]
        punto(
            "Pólizas cuadradas",
            not descuadradas,
            ("Cada póliza del mes cuadra debe contra haber." if not descuadradas else
             f"{len(descuadradas)} pólizas no cuadran."),
            reverse("admin:tienda_journalentry_unbalanced"),
            "Ver descuadradas",
        )

        # ── Inventario contra la bodega (a hoy, no al cierre) ──
        contable = self._saldo_cuenta("1100")
        fisico = Decimal("0.00")
        for variante in ProductVariant.objects.filter(activo=True).select_related("product"):
            fisico += _money(variante.product.costo) * Decimal(str(variante.stock or 0))
        diferencia = contable - fisico
        punto(
            "Inventario contable igual al físico",
            abs(diferencia) < Decimal("1"),
            (f"Ambos dan ${fisico:,.2f}." if abs(diferencia) < Decimal("1") else
             f"La cuenta 1100 tiene ${contable:,.2f} y la bodega vale ${fisico:,.2f}: sobran ${diferencia:,.2f}."),
            reverse("admin:tienda_producto_inventory_matrix"),
            "Ver inventario",
            tono_falla="warn",
        )

        # ── Balance ──
        balance = self._balance_sheet_data(bounds)
        punto(
            "Balance cuadrado",
            abs(balance["difference"]) < Decimal("0.01"),
            ("Activo igual a pasivo más capital." if abs(balance["difference"]) < Decimal("0.01") else
             f"Hay una diferencia de ${balance['difference']:,.2f}."),
            reverse("admin:tienda_journalentry_balance_sheet") + f"?month={bounds['current_month_value']}",
            "Ver balance",
        )

        pendientes = [p for p in puntos if p["estado"] != "ok"]
        return {
            "checklist": puntos,
            "checklist_pendientes": len(pendientes),
            "checklist_total": len(puntos),
            "checklist_listo": not pendientes,
        }

    def month_close_view(self, request):
        bounds = self._month_bounds(request)
        contexto = self._month_close_checklist(bounds)
        statement = self._income_statement_data(bounds)
        context = dict(
            self.admin_site.each_context(request),
            title="Cierre del mes",
            opts=self.model._meta,
            **bounds,
            **contexto,
            **statement,
        )
        return TemplateResponse(request, "admin/tienda/month_close.html", context)

    def sat_xml_view(self, request):
        """Catalogo de cuentas y balanza de comprobacion en XML para el SAT."""
        from .. import contabilidad_electronica as ce

        hoy = timezone.localdate()
        # Por defecto el mes anterior: es el que toca enviar.
        anterior = hoy.replace(day=1) - timedelta(days=1)
        anio, mes = _coerce_month(request.GET.get("month") or anterior.strftime("%Y-%m"), hoy)
        rfc = (request.GET.get("rfc") or settings.SAT_RFC or "").strip().upper()
        tipo_envio = "C" if request.GET.get("tipo") == "C" else "N"
        fecha_mod = None
        if request.GET.get("fecha_mod"):
            try:
                fecha_mod = date.fromisoformat(request.GET["fecha_mod"])
            except ValueError:
                fecha_mod = None

        descargar = request.GET.get("descargar")
        if descargar in ("catalogo", "balanza"):
            try:
                if descargar == "catalogo":
                    contenido = ce.catalogo_xml(rfc=rfc, anio=anio, mes=mes)
                    tipo = "CT"
                else:
                    contenido = ce.balanza_xml(
                        rfc=rfc, anio=anio, mes=mes, tipo_envio=tipo_envio, fecha_mod=fecha_mod
                    )
                    tipo = "B" + tipo_envio
            except ce.ContabilidadElectronicaError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                params = request.GET.copy()
                params.pop("descargar", None)
                return HttpResponseRedirect(f"{request.path}?{params.urlencode()}")
            nombre = ce.nombre_archivo(rfc=rfc, anio=anio, mes=mes, tipo=tipo)
            response = HttpResponse(ce.empaquetar(nombre, contenido), content_type="application/zip")
            response["Content-Disposition"] = f'attachment; filename="{nombre}.zip"'
            return response

        cuentas = ce.cuentas_del_catalogo()
        filas = ce.balanza(anio=anio, mes=mes)
        hojas = [f for f in filas if f["es_hoja"]]
        total_debe = sum((f["debe"] for f in hojas), Decimal("0.00"))
        total_haber = sum((f["haber"] for f in hojas), Decimal("0.00"))
        try:
            ce.validar_rfc(rfc)
            rfc_valido = True
        except ce.ContabilidadElectronicaError:
            rfc_valido = False
        month_start = date(anio, mes, 1)
        context = dict(
            self.admin_site.each_context(request),
            title="Contabilidad electrónica SAT",
            opts=self.model._meta,
            rfc=rfc,
            rfc_valido=rfc_valido,
            tipo_envio=tipo_envio,
            fecha_mod=fecha_mod,
            current_month_value=month_start.strftime("%Y-%m"),
            month_label=month_start.strftime("%B %Y").capitalize(),
            previous_month=(month_start - timedelta(days=1)).strftime("%Y-%m"),
            next_month=(month_start + timedelta(days=32)).strftime("%Y-%m"),
            cuentas_total=len(cuentas),
            faltantes=ce.cuentas_sin_codigo_sat(cuentas),
            filas=filas,
            total_debe=total_debe,
            total_haber=total_haber,
            cuadra=total_debe == total_haber,
            nombre_catalogo=ce.nombre_archivo(rfc=rfc or "RFC", anio=anio, mes=mes, tipo="CT"),
            nombre_balanza=ce.nombre_archivo(rfc=rfc or "RFC", anio=anio, mes=mes, tipo="B" + tipo_envio),
        )
        return TemplateResponse(request, "admin/tienda/sat_xml.html", context)

    def card_payment_view(self, request):
        """Registrar un abono a la tarjeta para que la deuda baje en los libros."""
        from ..accounting import post_card_payment

        hoy = timezone.localdate()
        valores = {"fecha": str(hoy), "monto": "", "origen": "cash", "nota": ""}

        if request.method == "POST":
            valores = {
                "fecha": request.POST.get("fecha", "").strip() or str(hoy),
                "monto": request.POST.get("monto", "").strip(),
                "origen": request.POST.get("origen", "cash"),
                "nota": request.POST.get("nota", "").strip(),
            }
            try:
                monto = Decimal(valores["monto"] or "0")
            except (InvalidOperation, ValueError):
                monto = Decimal("0")

            if monto <= 0:
                self.message_user(request, "Captura un monto mayor a cero.", level=messages.ERROR)
            else:
                entry = post_card_payment(
                    fecha=valores["fecha"],
                    monto=monto,
                    origen=valores["origen"],
                    nota=valores["nota"],
                    created_by=request.user,
                )
                if entry is None:
                    self.message_user(request, "Ese pago ya estaba registrado.", level=messages.WARNING)
                else:
                    self.message_user(
                        request,
                        f"Pago registrado. La deuda de la tarjeta bajó ${monto:,.2f} (póliza #{entry.id}).",
                        level=messages.SUCCESS,
                    )
                    return HttpResponseRedirect(
                        reverse("admin:tienda_journalentry_card_payment")
                    )

        saldo_tarjeta = self._saldo_cuenta("2100", naturaleza="acreedora")
        efectivo = self._saldo_cuenta("1000") + self._saldo_cuenta("1010")
        pagos = (
            JournalEntry.objects.filter(source="credit_card", is_posted=True)
            .order_by("-date", "-id")[:12]
        )
        context = dict(
            self.admin_site.each_context(request),
            title="Pago de tarjeta",
            opts=self.model._meta,
            valores=valores,
            saldo_tarjeta=saldo_tarjeta,
            efectivo_disponible=efectivo,
            pagos=pagos,
        )
        return TemplateResponse(request, "admin/tienda/card_payment.html", context)

    def _saldo_cuenta(self, code, naturaleza="deudora", date_to=None):
        queryset = JournalEntryLine.objects.filter(
            journal_entry__is_posted=True, account__code=code
        )
        if date_to:
            queryset = queryset.filter(journal_entry__date__lte=date_to)
        totales = queryset.aggregate(d=Sum("debit"), c=Sum("credit"))
        saldo = _money(totales["d"]) - _money(totales["c"])
        return -saldo if naturaleza == "acreedora" else saldo

    def finance_dashboard_view(self, request):
        bounds = self._month_bounds(request)
        statement = self._income_statement_data(bounds)
        balance = self._balance_sheet_data(bounds)
        flow = self._cash_flow_data(bounds)
        ratios = self._financial_ratios_data(bounds)
        context = dict(
            self.admin_site.each_context(request),
            title="Finanzas",
            subtitle="Estado del negocio en números",
            opts=self.model._meta,
            avisos=self._finance_health_checks(bounds),
            **bounds,
            **statement,
            **balance,
            **flow,
            **ratios,
        )
        return TemplateResponse(request, "admin/tienda/finance_dashboard.html", context)

    def cash_flow_view(self, request):
        bounds = self._month_bounds(request)
        context = dict(
            self.admin_site.each_context(request),
            title="Flujo de efectivo",
            opts=self.model._meta,
            avisos=self._finance_health_checks(bounds),
            **bounds,
            **self._cash_flow_data(bounds),
        )
        return TemplateResponse(request, "admin/tienda/cash_flow.html", context)

    def financial_ratios_view(self, request):
        bounds = self._month_bounds(request)
        context = dict(
            self.admin_site.each_context(request),
            title="Razones financieras",
            opts=self.model._meta,
            avisos=self._finance_health_checks(bounds),
            **bounds,
            **self._financial_ratios_data(bounds),
        )
        return TemplateResponse(request, "admin/tienda/financial_ratios.html", context)

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
    list_select_related = ("closed_by",)
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
    list_select_related = ("accounting_account",)
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
    list_select_related = ("money_account", "journal_entry")
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
