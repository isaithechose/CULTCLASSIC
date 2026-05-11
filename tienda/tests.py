from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import AccountingAccount, BankMovement, JournalEntry, JournalEntryLine, MoneyAccount


class MoneyAccountReconciliationAdminTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.user)
        self.bank_account, _ = AccountingAccount.objects.get_or_create(
            code="1010",
            defaults={"name": "Bancos", "account_type": "asset"},
        )
        self.sales_account, _ = AccountingAccount.objects.get_or_create(
            code="4000",
            defaults={"name": "Ventas", "account_type": "income"},
        )
        self.money_account = MoneyAccount.objects.create(
            name="Santander",
            kind="bank",
            accounting_account=self.bank_account,
        )

    def test_auto_match_reconciles_movement_with_matching_journal_line(self):
        entry = JournalEntry.objects.create(
            date=date(2026, 5, 10),
            entry_type="income",
            source="manual",
            concept="Venta tienda",
            reference="DEP-001",
            is_posted=True,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=self.bank_account,
            debit=Decimal("500.00"),
            credit=Decimal("0.00"),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=self.sales_account,
            debit=Decimal("0.00"),
            credit=Decimal("500.00"),
        )
        movement = BankMovement.objects.create(
            money_account=self.money_account,
            date=date(2026, 5, 11),
            description="Depósito tarjeta",
            movement_type="deposit",
            amount=Decimal("500.00"),
            reference="DEP-001",
            created_by=self.user,
        )

        response = self.client.post(
            reverse("admin:tienda_moneyaccount_reconciliation_auto_match", args=[self.money_account.id]),
            {"month": "2026-05"},
        )

        self.assertEqual(response.status_code, 302)
        movement.refresh_from_db()
        self.assertTrue(movement.is_reconciled)
        self.assertEqual(movement.journal_entry, entry)

    def test_reconciliation_export_downloads_spreadsheet(self):
        BankMovement.objects.create(
            money_account=self.money_account,
            date=date(2026, 5, 11),
            description="Comisión banco",
            movement_type="fee",
            amount=Decimal("15.00"),
            created_by=self.user,
        )

        response = self.client.get(
            reverse("admin:tienda_moneyaccount_reconciliation_export", args=[self.money_account.id]),
            {"month": "2026-05"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/vnd.ms-excel", response["Content-Type"])
        self.assertIn("conciliacion", response["Content-Disposition"])
