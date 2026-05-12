from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

from .models import (
    AccountingAccount,
    BankMovement,
    Categoria,
    JournalEntry,
    JournalEntryLine,
    MoneyAccount,
    Order,
    OrderItem,
    Producto,
    Reseña,
)
from .forms import ShippingAddressForm

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(username="testuser", password="pass1234"):
    return User.objects.create_user(username=username, email=f"{username}@example.com", password=password)


def make_producto(nombre="Playera Test", precio=500, stock=10):
    categoria, _ = Categoria.objects.get_or_create(nombre="cortes")
    return Producto.objects.create(
        nombre=nombre,
        descripcion="Descripción de prueba",
        precio=Decimal(str(precio)),
        stock=stock,
        tallas_disponibles="S,M,L",
        colores_disponibles="negro,blanco",
        categoria=categoria,
    )


# ---------------------------------------------------------------------------
# Cart tests
# ---------------------------------------------------------------------------

class CartTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = make_user()
        self.producto = make_producto()

    def test_add_to_cart_post(self):
        url = reverse("tienda:agregar_al_carrito", args=[self.producto.id])
        response = self.client.post(url, {"talla": "M", "color": "negro", "action": "add_to_cart"})
        self.assertIn(response.status_code, [200, 302])
        carrito = self.client.session.get("carrito", {})
        self.assertTrue(len(carrito) > 0)

    def test_add_to_cart_no_stock(self):
        self.producto.stock = 0
        self.producto.save()
        url = reverse("tienda:agregar_al_carrito", args=[self.producto.id])
        response = self.client.post(url, {"talla": "M", "color": "negro", "action": "add_to_cart"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get("carrito", {}), {})

    def test_remove_from_cart(self):
        session = self.client.session
        session["carrito"] = {
            f"{self.producto.id}-M-negro--": {
                "nombre": self.producto.nombre,
                "precio": float(self.producto.precio),
                "cantidad": 1,
                "talla": "M",
                "color": "negro",
                "diseño_pecho": "",
                "diseño_espalda": "",
            }
        }
        session.save()
        url = reverse("tienda:eliminar_del_carrito", args=[self.producto.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get("carrito", {}), {})

    def test_carrito_view_renders(self):
        response = self.client.get(reverse("tienda:carrito"))
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# Review tests
# ---------------------------------------------------------------------------

class ReseñaTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = make_user()
        self.producto = make_producto()

    def test_submit_reseña_requires_login(self):
        url = reverse("tienda:submit_reseña", args=[self.producto.id])
        response = self.client.post(url, {"calificacion": 5, "comentario": "Muy buena"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Reseña.objects.exists())

    def test_submit_reseña_authenticated(self):
        self.client.force_login(self.user)
        url = reverse("tienda:submit_reseña", args=[self.producto.id])
        response = self.client.post(url, {"calificacion": 4, "comentario": "Buena calidad"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Reseña.objects.count(), 1)
        r = Reseña.objects.first()
        self.assertEqual(r.calificacion, 4)
        self.assertEqual(r.usuario, self.user)

    def test_submit_reseña_invalid_calificacion(self):
        self.client.force_login(self.user)
        url = reverse("tienda:submit_reseña", args=[self.producto.id])
        response = self.client.post(url, {"calificacion": 99, "comentario": "Test"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Reseña.objects.count(), 0)

    def test_detalle_producto_shows_reseñas(self):
        Reseña.objects.create(
            usuario=self.user, producto=self.producto, comentario="Excelente", calificacion=5
        )
        url = reverse("tienda:detalle_producto", args=[self.producto.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Excelente")


# ---------------------------------------------------------------------------
# ShippingAddressForm validation tests
# ---------------------------------------------------------------------------

class ShippingAddressFormTests(TestCase):
    base_data = {
        "phone": "5512345678",
        "address_line1": "Insurgentes Sur 1234",
        "address_line2": "Col. Del Valle",
        "city": "CDMX",
        "state": "Ciudad de México",
        "postal_code": "03100",
        "country": "México",
    }

    def test_valid_form(self):
        form = ShippingAddressForm(data=self.base_data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_postal_code_letters(self):
        data = {**self.base_data, "postal_code": "ABC12"}
        form = ShippingAddressForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("postal_code", form.errors)

    def test_invalid_postal_code_too_short(self):
        data = {**self.base_data, "postal_code": "1234"}
        form = ShippingAddressForm(data=data)
        self.assertFalse(form.is_valid())

    def test_invalid_phone(self):
        data = {**self.base_data, "phone": "123"}
        form = ShippingAddressForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("phone", form.errors)


# ---------------------------------------------------------------------------
# MoneyAccount reconciliation admin tests (pre-existing)
# ---------------------------------------------------------------------------

class MoneyAccountReconciliationAdminTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
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
