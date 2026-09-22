from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .forms import ShippingAddressForm
from .models import (
    AccountingAccount,
    BankMovement,
    Categoria,
    Expense,
    ExpenseCategory,
    JournalEntry,
    JournalEntryLine,
    MoneyAccount,
    Order,
    OrderItem,
    Producto,
    Reseña,
)

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
        order = Order.objects.create(customer=self.user, status="Completed")
        OrderItem.objects.create(order=order, product=self.producto, quantity=1, price=self.producto.precio)
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


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="ventas@example.com",
)
class ShippingNotificationSignalTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.customer = user_model.objects.create_user(
            username="cliente",
            email="cliente@example.com",
            password="password",
        )

    def test_shipping_email_only_sends_when_status_changes_to_shipped(self):
        order = Order.objects.create(customer=self.customer, status="Completed")

        order.tracking_number = "TRACK-123"
        order.shipping_status = "Shipped"
        order.save()

        self.assertEqual(len(mail.outbox), 1)

        order.internal_note = "Nota administrativa"
        order.save(update_fields=["internal_note"])

        self.assertEqual(len(mail.outbox), 1)


# ---------------------------------------------------------------------------
# Costo congelado en la venta
# ---------------------------------------------------------------------------


class FrozenUnitCostTests(TestCase):
    """El costo de lo vendido se guarda en la línea, no se recalcula después."""

    def setUp(self):
        self.producto = make_producto(precio=500)
        self.producto.costo = Decimal("100.00")
        self.producto.save(update_fields=["costo"])

    def test_costo_de_la_linea_no_cambia_si_cambia_el_costo_del_producto(self):
        order = Order.objects.create(status="Completed")
        item = OrderItem.objects.create(
            order=order,
            product=self.producto,
            quantity=2,
            price=Decimal("500.00"),
            unit_cost=Decimal("100.00"),
        )
        self.assertEqual(item.cost_total, Decimal("200.00"))
        self.assertEqual(item.profit_total, Decimal("800.00"))

        self.producto.costo = Decimal("400.00")
        self.producto.save(update_fields=["costo"])
        item.refresh_from_db()

        self.assertEqual(item.effective_unit_cost, Decimal("100.00"))
        self.assertEqual(item.cost_total, Decimal("200.00"))
        self.assertEqual(item.profit_total, Decimal("800.00"))

    def test_venta_vieja_sin_costo_congelado_cae_al_costo_del_producto(self):
        order = Order.objects.create(status="Completed")
        item = OrderItem.objects.create(
            order=order,
            product=self.producto,
            quantity=1,
            price=Decimal("500.00"),
        )
        self.assertIsNone(item.unit_cost)
        self.assertEqual(item.effective_unit_cost, Decimal("100.00"))

    def test_checkout_congela_el_costo_al_armar_el_pedido(self):
        from tienda.views import _build_order_from_cart

        order = Order.objects.create(status="Pending")
        carrito = {
            f"{self.producto.id}-M-negro--": {"cantidad": 3, "precio": "500.00"},
        }
        _build_order_from_cart(order, carrito, reset_checkout_state=False)

        item = order.items.get()
        self.assertEqual(item.unit_cost, Decimal("100.00"))
        self.assertEqual(item.quantity, 3)


# ---------------------------------------------------------------------------
# Sección de finanzas
# ---------------------------------------------------------------------------


class FinanceSectionTests(TestCase):
    """Flujo de efectivo y razones calculados sobre pólizas reales."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="finanzas", email="fin@example.com", password="clave-secreta"
        )
        self.client = Client()
        self.client.force_login(self.user)

        def cuenta(code, name, tipo):
            return AccountingAccount.objects.get_or_create(
                code=code, defaults={"name": name, "account_type": tipo}
            )[0]

        self.caja = cuenta("1000", "Caja", "asset")
        self.ventas = cuenta("4000", "Ventas", "income")
        self.gastos = cuenta("6000", "Gastos generales", "expense")

        # Una venta cobrada en efectivo y un gasto pagado en efectivo.
        venta = JournalEntry.objects.create(
            date=date(2026, 5, 10), entry_type="income", source="pos",
            concept="Venta de prueba", is_posted=True,
        )
        JournalEntryLine.objects.create(journal_entry=venta, account=self.caja, debit=Decimal("1000"), credit=Decimal("0"))
        JournalEntryLine.objects.create(journal_entry=venta, account=self.ventas, debit=Decimal("0"), credit=Decimal("1000"))

        gasto = JournalEntry.objects.create(
            date=date(2026, 5, 20), entry_type="expense", source="expense",
            concept="Gasto de prueba", is_posted=True,
        )
        JournalEntryLine.objects.create(journal_entry=gasto, account=self.gastos, debit=Decimal("300"), credit=Decimal("0"))
        JournalEntryLine.objects.create(journal_entry=gasto, account=self.caja, debit=Decimal("0"), credit=Decimal("300"))

    def _admin(self):
        from django.contrib import admin as dj_admin

        return dj_admin.site._registry[JournalEntry]

    def _bounds(self, mes="2026-05"):
        class Req:
            GET = {"month": mes}

        return self._admin()._month_bounds(Req())

    def test_flujo_clasifica_entradas_y_salidas(self):
        data = self._admin()._cash_flow_data(self._bounds())
        self.assertEqual(data["flow_total_in"], Decimal("1000"))
        self.assertEqual(data["flow_total_out"], Decimal("300"))
        self.assertEqual(data["flow_net_change"], Decimal("700"))
        self.assertTrue(data["flow_check_ok"])

        operacion = next(s for s in data["flow_sections"] if s["key"] == "operacion")
        etiquetas = {row["label"] for row in operacion["rows"]}
        self.assertIn("Cobros de venta", etiquetas)
        self.assertIn("Gastos de operación", etiquetas)

    def test_metodo_indirecto_llega_al_mismo_efectivo(self):
        data = self._admin()._cash_flow_data(self._bounds())
        self.assertTrue(data["flow_indirect_matches"])
        self.assertEqual(data["flow_indirect_total"], data["flow_net_change"])

    def test_razones_se_calculan_y_se_formatean(self):
        data = self._admin()._financial_ratios_data(self._bounds())
        base = data["ratio_base"]
        self.assertEqual(base["ingresos"], Decimal("1000"))
        self.assertEqual(base["gastos"], Decimal("300"))
        self.assertEqual(base["utilidad_neta"], Decimal("700"))

        razones = {r["nombre"]: r for g in data["ratio_groups"] for r in g["razones"]}
        self.assertEqual(razones["Margen neto"]["display"], "70.0%")
        self.assertEqual(razones["Peso de los gastos"]["display"], "30.0%")
        # sin pasivos no hay razón corriente: debe decirlo, no tronar
        self.assertEqual(razones["Razón corriente"]["display"], "Sin dato")

    def test_las_tres_pantallas_responden(self):
        for url in (
            reverse("admin:tienda_journalentry_finance_dashboard"),
            reverse("admin:tienda_journalentry_cash_flow"),
            reverse("admin:tienda_journalentry_financial_ratios"),
        ):
            response = self.client.get(url, {"month": "2026-05"})
            self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# Las compras capitalizan inventario, no son gasto
# ---------------------------------------------------------------------------


class PurchaseCapitalizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="compras", email="compras@example.com", password="clave-secreta"
        )
        for code, name, tipo in (
            ("1000", "Caja", "asset"),
            ("1010", "Bancos", "asset"),
            ("1100", "Inventario", "asset"),
            ("2000", "Proveedores", "liability"),
            ("2100", "Tarjetas de credito por pagar", "liability"),
            ("6000", "Gastos generales", "expense"),
        ):
            AccountingAccount.objects.get_or_create(
                code=code, defaults={"name": name, "account_type": tipo}
            )

    def _lineas(self, entry):
        return {line.account.code: (line.debit, line.credit) for line in entry.lines.all()}

    def test_cada_forma_de_pago_abona_su_cuenta(self):
        from tienda.admin import _post_purchase_journal_entry

        esperado = {"transfer": "1010", "cash": "1000", "card": "2100", "credit": "2000"}
        for metodo, cuenta in esperado.items():
            entry = _post_purchase_journal_entry(
                date_value=date(2026, 9, 17),
                amount=Decimal("2500"),
                payment_method=metodo,
                supplier="Proveedor X",
                note="compra",
                reference=f"REC-{metodo}",
                created_by=self.user,
            )
            lineas = self._lineas(entry)
            self.assertEqual(lineas["1100"][0], Decimal("2500"), f"{metodo}: inventario debe ir al debe")
            self.assertEqual(lineas[cuenta][1], Decimal("2500"), f"{metodo}: contrapartida equivocada")
            self.assertNotIn("6000", lineas, f"{metodo}: la compra no debe tocar gastos")

    def test_gasto_de_categoria_compras_tambien_capitaliza(self):
        from tienda.admin import _post_expense_journal_entry

        categoria = ExpenseCategory.objects.create(nombre="Compras inventario")
        gasto = Expense.objects.create(
            fecha=date(2026, 9, 17),
            categoria=categoria,
            concepto="Compra a proveedor",
            monto=Decimal("1200"),
            metodo_pago="transfer",
            created_by=self.user,
        )
        entry = _post_expense_journal_entry(gasto, created_by=self.user)
        lineas = self._lineas(entry)
        self.assertEqual(lineas["1100"][0], Decimal("1200"))
        self.assertNotIn("6000", lineas)

    def test_un_gasto_normal_si_va_a_gastos(self):
        from tienda.admin import _post_expense_journal_entry

        categoria = ExpenseCategory.objects.create(nombre="Renta")
        gasto = Expense.objects.create(
            fecha=date(2026, 9, 17),
            categoria=categoria,
            concepto="Renta del local",
            monto=Decimal("8000"),
            metodo_pago="transfer",
            created_by=self.user,
        )
        entry = _post_expense_journal_entry(gasto, created_by=self.user)
        lineas = self._lineas(entry)
        self.assertEqual(lineas["6000"][0], Decimal("8000"))
        self.assertNotIn("1100", lineas)


# ---------------------------------------------------------------------------
# Contabilidad automática: Mercado Libre, tarjeta y cierre del mes
# ---------------------------------------------------------------------------


class AutomaticAccountingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="conta", email="conta@example.com", password="clave-secreta"
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.producto = make_producto(precio=500)
        self.producto.costo = Decimal("100.00")
        self.producto.save(update_fields=["costo"])

    def _ml_order(self, ml_id=999, status="paid"):
        from mercadolibre.models import MercadoLibreListing, MercadoLibreOrder, MercadoLibreOrderItem
        from django.utils import timezone as tz

        listing = MercadoLibreListing.objects.create(
            ml_id="MLM123", title="Playera", price=Decimal("400"), producto=self.producto
        )
        order = MercadoLibreOrder.objects.create(
            ml_id=ml_id, status=status, date_created=tz.now(),
            total_amount=Decimal("400.00"), marketplace_fee=Decimal("78.00"),
            shipping_cost=Decimal("67.60"), net_received_amount=Decimal("254.40"),
        )
        MercadoLibreOrderItem.objects.create(
            order=order, item_id=listing.ml_id, title="Playera", quantity=1, unit_price=Decimal("400")
        )
        return order

    def test_venta_de_ml_genera_poliza_completa(self):
        from tienda.accounting import post_ml_order_entry

        entry = post_ml_order_entry(self._ml_order(), created_by=self.user)
        self.assertIsNotNone(entry)
        lineas = {l.account.code: (l.debit, l.credit) for l in entry.lines.all()}
        self.assertEqual(lineas["1030"][0], Decimal("254.40"))   # lo que ML depositará
        self.assertEqual(lineas["6200"][0], Decimal("78.00"))    # comisión
        self.assertEqual(lineas["6100"][0], Decimal("67.60"))    # envío
        self.assertEqual(lineas["4000"][1], Decimal("400.00"))   # venta completa
        self.assertEqual(lineas["5000"][0], Decimal("100.00"))   # costo
        self.assertEqual(lineas["1100"][1], Decimal("100.00"))   # sale de inventario
        self.assertEqual(entry.total_debit, entry.total_credit)

    def test_no_duplica_la_poliza_de_ml(self):
        from tienda.accounting import post_ml_order_entry

        order = self._ml_order()
        self.assertIsNotNone(post_ml_order_entry(order))
        self.assertIsNone(post_ml_order_entry(order))
        self.assertEqual(JournalEntry.objects.filter(reference=f"ML-{order.ml_id}").count(), 1)

    def test_cancelacion_de_ml_genera_la_reversa(self):
        from tienda.accounting import post_ml_order_entry, sincronizar_poliza_ml

        order = self._ml_order()
        post_ml_order_entry(order)
        order.status = "cancelled"
        order.save(update_fields=["status"])
        reversa = sincronizar_poliza_ml(order)
        self.assertIsNotNone(reversa)
        # la reversa deja el efecto neto en cero
        neto = Decimal("0")
        for entry in JournalEntry.objects.filter(reference__contains=str(order.ml_id)):
            for linea in entry.lines.all():
                if linea.account.code == "4000":
                    neto += linea.credit - linea.debit
        self.assertEqual(neto, Decimal("0"))

    def test_pago_de_tarjeta_baja_la_deuda(self):
        from tienda.accounting import cuenta, crear_poliza, post_card_payment

        # deuda inicial: una compra con la tarjeta
        crear_poliza(
            date_value=date(2026, 9, 1), concept="Compra", reference="TEST-COMPRA",
            lines=[
                {"account": cuenta("1100"), "debit": Decimal("1000")},
                {"account": cuenta("2100"), "credit": Decimal("1000")},
            ],
        )
        pago = post_card_payment(fecha=date(2026, 9, 20), monto=Decimal("400"), origen="cash", created_by=self.user)
        self.assertIsNotNone(pago)
        lineas = {l.account.code: (l.debit, l.credit) for l in pago.lines.all()}
        self.assertEqual(lineas["2100"][0], Decimal("400"))
        self.assertEqual(lineas["1000"][1], Decimal("400"))

    def test_el_mismo_pago_no_se_registra_dos_veces(self):
        from tienda.accounting import post_card_payment

        datos = dict(fecha=date(2026, 9, 20), monto=Decimal("400"), origen="cash", nota="septiembre")
        self.assertIsNotNone(post_card_payment(**datos))
        self.assertIsNone(post_card_payment(**datos))

    def test_cierre_detecta_una_venta_sin_poliza(self):
        from django.contrib import admin as dj_admin
        from django.utils import timezone as tz

        order = Order.objects.create(status="Completed")
        Order.objects.filter(pk=order.pk).update(created_at=tz.now())
        OrderItem.objects.create(order=order, product=self.producto, quantity=1, price=Decimal("500"))

        ja = dj_admin.site._registry[JournalEntry]
        class Req:
            GET = {"month": tz.localdate().strftime("%Y-%m")}
        datos = ja._month_close_checklist(ja._month_bounds(Req()))
        ventas = next(p for p in datos["checklist"] if p["titulo"] == "Ventas del mes registradas")
        self.assertNotEqual(ventas["estado"], "ok")
        self.assertIn("no tienen póliza", ventas["detalle"])

    def test_las_pantallas_nuevas_responden(self):
        for nombre in ("tienda_journalentry_month_close", "tienda_journalentry_card_payment"):
            response = self.client.get(reverse(f"admin:{nombre}"))
            self.assertEqual(response.status_code, 200)
