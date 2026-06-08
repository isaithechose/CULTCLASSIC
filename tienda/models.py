from django.db import models, transaction
from django.contrib.auth.models import User
from django.conf import settings
from django.core.exceptions import ValidationError
from decimal import Decimal


def _normalize_variant_value(value):
    cleaned = str(value or "").strip().lower().replace("_", " ")
    return " ".join(cleaned.split())

class Categoria(models.Model):
    nombre = models.CharField(max_length=50)
    descripcion = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.nombre

class Subcategoria(models.Model):
    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE, related_name="subcategorias")
    nombre = models.CharField(max_length=50)
    descripcion = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.nombre

class Producto(models.Model):
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField()
    costo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField()
    categoria = models.ForeignKey(Categoria, on_delete=models.SET_NULL, null=True, blank=True)
    subcategoria = models.ForeignKey(Subcategoria, on_delete=models.SET_NULL, null=True, blank=True)
    tallas_disponibles = models.CharField(max_length=100)
    colores_disponibles = models.CharField(max_length=100, help_text="negro, blanco, guinda, azul, offwhite, slate_blue, latte")
    imagen = models.ImageField(upload_to="productos/", blank=True, null=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)
    disponible = models.BooleanField(default=True)
    slug_imagen = models.SlugField(max_length=255, blank=True)

    def __str__(self):
        return self.nombre

    def uses_variant_inventory(self):
        return self.variants.filter(activo=True).exists()

    def variant_stock_total(self):
        return sum(variant.stock for variant in self.variants.filter(activo=True))

    def sync_stock_from_variants(self, save=True):
        total = self.variant_stock_total()
        self.stock = total
        if save:
            self.save(update_fields=["stock", "fecha_actualizacion"])
        return total

class Order(models.Model):
    SALES_CHANNEL_CHOICES = [
        ("online", "Tienda online"),
        ("pos", "Punto de venta"),
        ("manual", "Manual"),
    ]

    PAYMENT_METHOD_CHOICES = [
        ("cash", "Efectivo"),
        ("card", "Tarjeta"),
        ("transfer", "Transferencia"),
        ("stripe", "Stripe"),
        ("other", "Otro"),
    ]

    customer = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=[
        ('Pending', 'Pending'), ('Completed', 'Completed'), ('Canceled', 'Canceled')
    ], default='Pending')
    sales_channel = models.CharField(max_length=20, choices=SALES_CHANNEL_CHOICES, default="online")
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default="stripe")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    promo_code = models.ForeignKey("PromoCode", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    internal_note = models.TextField(blank=True, null=True)
    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="pos_orders")
    shipping_status = models.CharField(max_length=20, choices=[
        ('Processing', 'Processing'), ('Shipped', 'Shipped'), ('Delivered', 'Delivered')
    ], default='Processing')
    tracking_number = models.CharField(max_length=50, blank=True, null=True)
    skydrop_quotation_id = models.CharField(max_length=80, blank=True, null=True)
    skydrop_rate_id = models.CharField(max_length=80, blank=True, null=True)
    skydrop_shipment_id = models.CharField(max_length=80, blank=True, null=True)
    skydrop_label_url = models.URLField(blank=True, null=True)
    skydrop_tracking_url = models.URLField(blank=True, null=True)
    skydrop_carrier = models.CharField(max_length=80, blank=True, null=True)
    skydrop_service = models.CharField(max_length=120, blank=True, null=True)
    skydrop_last_error = models.TextField(blank=True, null=True)
    skydrop_last_payload = models.JSONField(blank=True, null=True)
    shipping_quote_amount = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    shipping_quote_currency = models.CharField(max_length=10, blank=True, null=True, default="MXN")
    stripe_session_id = models.CharField(max_length=255, blank=True, null=True)

    @property
    def subtotal_price(self):
        return sum(item.price * item.quantity for item in self.items.all())

    @property
    def shipping_total(self):
        return self.shipping_quote_amount or Decimal("0.00")

    @property
    def total_price(self):
        total = self.subtotal_price + self.shipping_total - (self.discount_amount or Decimal("0.00"))
        return max(total, Decimal("0.00"))

    def __str__(self):
        return f"Orden {self.id} de {self.customer}"


class ProductVariant(models.Model):
    product = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=80, blank=True, null=True)
    talla = models.CharField(max_length=10)
    color = models.CharField(max_length=40)
    imagen = models.ImageField(upload_to="productos/variantes/", blank=True, null=True)
    stock = models.PositiveIntegerField(default=0)
    costo = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    precio_override = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    activo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "talla", "color"], name="unique_product_variant"),
        ]
        ordering = ["product__nombre", "color", "talla"]

    def __str__(self):
        return f"{self.product.nombre} / {self.color} / {self.talla}"

    @property
    def display_image_name(self):
        from .utils.variant_image_assignment import get_variant_display_image_name

        return get_variant_display_image_name(self)

    @property
    def display_image_url(self):
        image_name = self.display_image_name
        if not image_name:
            return None
        media_url = settings.MEDIA_URL.rstrip("/")
        return f"{media_url}/{image_name.lstrip('/')}"

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            self.product.sync_stock_from_variants()

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            product = self.product
            super().delete(*args, **kwargs)
            product.sync_stock_from_variants()


class InventoryMovement(models.Model):
    MOVEMENT_CHOICES = [
        ("purchase", "Compra"),
        ("sale", "Venta"),
        ("return", "Devolución"),
        ("adjustment", "Ajuste"),
        ("manual_in", "Entrada manual"),
        ("manual_out", "Salida manual"),
    ]

    product = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="inventory_movements")
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_movements",
    )
    order = models.ForeignKey("Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="inventory_movements")
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_CHOICES)
    quantity_change = models.IntegerField()
    stock_before = models.IntegerField(blank=True, null=True)
    stock_after = models.IntegerField(blank=True, null=True)
    note = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.product.nombre} ({self.get_movement_type_display()}: {self.quantity_change:+})"


class ExpenseCategory(models.Model):
    nombre = models.CharField(max_length=80, unique=True)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Categoría de gasto"
        verbose_name_plural = "Categorías de gasto"

    def __str__(self):
        return self.nombre


class Expense(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("cash", "Efectivo"),
        ("card", "Tarjeta"),
        ("transfer", "Transferencia"),
        ("other", "Otro"),
    ]

    RECURRENCE_CHOICES = [
        ("none", "No recurrente"),
        ("weekly", "Semanal"),
        ("monthly", "Mensual"),
        ("yearly", "Anual"),
    ]

    fecha = models.DateField()
    categoria = models.ForeignKey(ExpenseCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses")
    concepto = models.CharField(max_length=140)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    metodo_pago = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default="transfer")
    proveedor = models.CharField(max_length=120, blank=True, null=True)
    nota = models.TextField(blank=True, null=True)
    recurrencia = models.CharField(max_length=20, choices=RECURRENCE_CHOICES, default="none")
    recurrencia_activa = models.BooleanField(default=False)
    recurrencia_fin = models.DateField(blank=True, null=True)
    gasto_origen = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gastos_generados",
    )
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        verbose_name = "Gasto"
        verbose_name_plural = "Gastos"

    def __str__(self):
        return f"{self.concepto} - ${self.monto}"


class BusinessPayment(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pendiente"),
        ("paid", "Pagado"),
        ("canceled", "Cancelado"),
    ]

    CATEGORY_CHOICES = [
        ("rent", "Renta"),
        ("payroll", "Nomina"),
        ("supplier", "Proveedor"),
        ("tax", "Impuestos"),
        ("services", "Servicios"),
        ("marketing", "Marketing"),
        ("logistics", "Logistica"),
        ("other", "Otro"),
    ]

    PAYMENT_METHOD_CHOICES = [
        ("cash", "Efectivo"),
        ("card", "Tarjeta"),
        ("transfer", "Transferencia"),
        ("other", "Otro"),
    ]

    fecha_programada = models.DateField()
    concepto = models.CharField(max_length=140)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    categoria = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="other")
    estado = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    fecha_pagado = models.DateField(blank=True, null=True)
    metodo_pago = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default="transfer")
    proveedor = models.CharField(max_length=120, blank=True, null=True)
    nota = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["fecha_programada", "estado", "id"]
        verbose_name = "Pago programado"
        verbose_name_plural = "Pagos programados"

    def __str__(self):
        return f"{self.concepto} - ${self.monto} ({self.get_estado_display()})"


class CreditCardAccount(models.Model):
    nombre = models.CharField(max_length=100)
    banco = models.CharField(max_length=100, blank=True, null=True)
    ultimos_4 = models.CharField(max_length=4, blank=True, null=True)
    limite_credito = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    dia_corte = models.PositiveSmallIntegerField(default=1)
    dia_pago = models.PositiveSmallIntegerField(default=20)
    activa = models.BooleanField(default=True)
    nota = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Tarjeta de crédito"
        verbose_name_plural = "Tarjetas de crédito"

    @property
    def saldo_pendiente(self):
        total = Decimal("0.00")
        for statement in self.statements.exclude(estado__in=["paid", "canceled"]):
            total += statement.saldo_pendiente
        return total

    def __str__(self):
        suffix = f" ****{self.ultimos_4}" if self.ultimos_4 else ""
        return f"{self.nombre}{suffix}"


class CreditCardStatement(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pendiente"),
        ("paid", "Pagado"),
        ("canceled", "Cancelado"),
    ]

    PAYMENT_METHOD_CHOICES = [
        ("cash", "Efectivo"),
        ("transfer", "Transferencia"),
        ("other", "Otro"),
    ]

    tarjeta = models.ForeignKey(CreditCardAccount, on_delete=models.CASCADE, related_name="statements")
    periodo = models.CharField(max_length=80, help_text="Ejemplo: Mayo 2026")
    fecha_corte = models.DateField()
    fecha_vencimiento = models.DateField()
    saldo_corte = models.DecimalField(max_digits=10, decimal_places=2)
    pago_minimo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    estado = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    monto_pagado = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fecha_pagado = models.DateField(blank=True, null=True)
    metodo_pago = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default="transfer")
    nota = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["fecha_vencimiento", "estado", "id"]
        verbose_name = "Estado de cuenta de tarjeta"
        verbose_name_plural = "Estados de cuenta de tarjetas"

    @property
    def saldo_pendiente(self):
        return max((self.saldo_corte or Decimal("0.00")) - (self.monto_pagado or Decimal("0.00")), Decimal("0.00"))

    @property
    def esta_vencido(self):
        from django.utils import timezone

        return self.estado == "pending" and self.fecha_vencimiento < timezone.localdate()

    def __str__(self):
        return f"{self.tarjeta} - {self.periodo} - ${self.saldo_corte}"


class AccountingAccount(models.Model):
    ACCOUNT_TYPE_CHOICES = [
        ("asset", "Activo"),
        ("liability", "Pasivo"),
        ("equity", "Capital"),
        ("income", "Ingreso"),
        ("cost", "Costo"),
        ("expense", "Gasto"),
    ]

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES)
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children")
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Cuenta contable"
        verbose_name_plural = "Catálogo de cuentas"

    def __str__(self):
        return f"{self.code} {self.name}"


class JournalEntry(models.Model):
    ENTRY_TYPE_CHOICES = [
        ("income", "Ingreso"),
        ("expense", "Egreso"),
        ("diary", "Diario"),
    ]

    SOURCE_CHOICES = [
        ("manual", "Manual"),
        ("pos", "Punto de venta"),
        ("expense", "Gasto"),
        ("credit_card", "Tarjeta de credito"),
        ("inventory", "Inventario"),
    ]

    date = models.DateField()
    entry_type = models.CharField(max_length=20, choices=ENTRY_TYPE_CHOICES, default="diary")
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="manual")
    concept = models.CharField(max_length=180)
    reference = models.CharField(max_length=80, blank=True, null=True)
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="journal_entries")
    expense = models.ForeignKey(Expense, on_delete=models.SET_NULL, null=True, blank=True, related_name="journal_entries")
    credit_card_statement = models.ForeignKey(CreditCardStatement, on_delete=models.SET_NULL, null=True, blank=True, related_name="journal_entries")
    is_posted = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "Póliza contable"
        verbose_name_plural = "Pólizas contables"

    @property
    def total_debit(self):
        return sum(line.debit for line in self.lines.all())

    @property
    def total_credit(self):
        return sum(line.credit for line in self.lines.all())

    @property
    def is_balanced(self):
        return self.total_debit == self.total_credit

    def __str__(self):
        return f"{self.date} - {self.concept}"


class JournalEntryLine(models.Model):
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(AccountingAccount, on_delete=models.PROTECT, related_name="journal_lines")
    description = models.CharField(max_length=180, blank=True)
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]
        verbose_name = "Partida de póliza"
        verbose_name_plural = "Partidas de póliza"

    def clean(self):
        if self.debit < 0 or self.credit < 0:
            raise ValidationError("Cargo y abono no pueden ser negativos.")
        if self.debit and self.credit:
            raise ValidationError("Una partida no puede tener cargo y abono al mismo tiempo.")

    def __str__(self):
        amount = self.debit if self.debit else self.credit
        side = "Debe" if self.debit else "Haber"
        return f"{self.account} - {side} ${amount}"


class AccountingPeriodClose(models.Model):
    month_start = models.DateField(unique=True)
    month_end = models.DateField()
    total_debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    difference = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    unbalanced_count = models.PositiveIntegerField(default=0)
    note = models.TextField(blank=True, null=True)
    closed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-month_start"]
        verbose_name = "Cierre contable"
        verbose_name_plural = "Cierres contables"

    def __str__(self):
        return f"Cierre contable {self.month_start:%Y-%m}"


class MoneyAccount(models.Model):
    ACCOUNT_KIND_CHOICES = [
        ("cash", "Efectivo"),
        ("bank", "Banco"),
        ("processor", "Procesador de pago"),
        ("other", "Otro"),
    ]

    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=ACCOUNT_KIND_CHOICES, default="bank")
    accounting_account = models.ForeignKey(AccountingAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="money_accounts")
    bank_name = models.CharField(max_length=100, blank=True, null=True)
    account_last4 = models.CharField(max_length=4, blank=True, null=True)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    note = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Cuenta de dinero"
        verbose_name_plural = "Cuentas de dinero"

    def __str__(self):
        suffix = f" ****{self.account_last4}" if self.account_last4 else ""
        return f"{self.name}{suffix}"


class BankMovement(models.Model):
    MOVEMENT_TYPE_CHOICES = [
        ("deposit", "Depósito"),
        ("withdrawal", "Retiro"),
        ("payment", "Pago"),
        ("fee", "Comisión"),
        ("transfer", "Transferencia"),
        ("other", "Otro"),
    ]

    money_account = models.ForeignKey(MoneyAccount, on_delete=models.CASCADE, related_name="bank_movements")
    date = models.DateField()
    description = models.CharField(max_length=180)
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPE_CHOICES, default="other")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name="bank_movements")
    is_reconciled = models.BooleanField(default=False)
    reconciled_at = models.DateTimeField(blank=True, null=True)
    reference = models.CharField(max_length=100, blank=True, null=True)
    note = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "Movimiento bancario"
        verbose_name_plural = "Movimientos bancarios"

    @property
    def signed_amount(self):
        if self.movement_type in ["withdrawal", "payment", "fee"]:
            return -abs(self.amount)
        return self.amount

    def __str__(self):
        return f"{self.date} - {self.money_account} - ${self.amount}"


class CashRegisterClosure(models.Model):
    fecha = models.DateField(unique=True)
    efectivo_contado = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tarjeta_contado = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    transferencia_contado = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    otros_contado = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    efectivo_sistema = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tarjeta_sistema = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    transferencia_sistema = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    otros_sistema = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gastos_efectivo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    diferencia = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    nota = models.TextField(blank=True, null=True)
    closed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha"]
        verbose_name = "Cierre de caja"
        verbose_name_plural = "Cierres de caja"

    @property
    def total_contado(self):
        return self.efectivo_contado + self.tarjeta_contado + self.transferencia_contado + self.otros_contado

    @property
    def total_sistema(self):
        return self.efectivo_sistema + self.tarjeta_sistema + self.transferencia_sistema + self.otros_sistema

    def __str__(self):
        return f"Cierre {self.fecha} - diferencia ${self.diferencia}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Producto, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    talla = models.CharField(max_length=10, blank=True, null=True)
    color = models.CharField(max_length=40, blank=True, null=True)
    diseño_pecho = models.CharField(max_length=255, blank=True, null=True)
    diseño_espalda = models.CharField(max_length=255, blank=True, null=True)

    @property
    def subtotal(self):
        return self.price * self.quantity

    def __str__(self):
        return f"{self.product.nombre} x {self.quantity}"

class Carrito(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.CASCADE)
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE)
    cantidad = models.PositiveIntegerField(default=1)

    def subtotal(self):
        return self.producto.precio * self.cantidad

    def __str__(self):
        return f"{self.producto.nombre} x {self.cantidad} ({self.usuario.username})"

class Reseña(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.CASCADE)
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='reseñas')
    comentario = models.TextField()
    calificacion = models.PositiveIntegerField(choices=[(i, i) for i in range(1, 6)])
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('usuario', 'producto')]

    def __str__(self):
        return f"{self.usuario.username} - {self.calificacion} estrellas"

class ShippingAddress(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='shipping_address')
    phone = models.CharField(max_length=30, blank=True, null=True)
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.address_line1}, {self.city}"

class ShippingUpdate(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='shipping_updates')
    status_message = models.TextField()
    updated_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Orden {self.order.id} - {self.updated_at:%Y-%m-%d %H:%M}"


class PromoCode(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ("percentage", "Porcentaje (%)"),
        ("fixed", "Monto fijo ($)"),
        ("free_shipping", "Envío gratis"),
    ]
    code = models.CharField(max_length=50, unique=True)
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    min_purchase = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Compra mínima para aplicar")
    max_uses = models.PositiveIntegerField(null=True, blank=True, help_text="Vacío = ilimitado")
    uses_count = models.PositiveIntegerField(default=0)
    expiration_date = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    description = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Código de descuento"
        verbose_name_plural = "Códigos de descuento"
        ordering = ["-created_at"]

    def is_valid(self, subtotal=None):
        from django.utils import timezone
        if not self.active:
            return False, "Código inactivo"
        if self.expiration_date and self.expiration_date < timezone.localdate():
            return False, "Código expirado"
        if self.max_uses is not None and self.uses_count >= self.max_uses:
            return False, "Código agotado"
        if subtotal is not None and subtotal < self.min_purchase:
            return False, f"Compra mínima de ${self.min_purchase}"
        return True, None

    def compute_discount(self, subtotal):
        if self.discount_type == "percentage":
            return (subtotal * self.discount_value / 100).quantize(Decimal("0.01"))
        if self.discount_type == "fixed":
            return min(self.discount_value, subtotal)
        return Decimal("0.00")

    def __str__(self):
        return self.code


class ProductImage(models.Model):
    product = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="productos/gallery/")
    alt_text = models.CharField(max_length=200, blank=True)
    order = models.PositiveSmallIntegerField(default=0, help_text="Menor = aparece primero")

    class Meta:
        verbose_name = "Imagen de producto"
        verbose_name_plural = "Galería de imágenes"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.product.nombre} — imagen {self.order}"


class OrderReturn(models.Model):
    REASON_CHOICES = [
        ("defective", "Defectuoso / dañado"),
        ("wrong_size", "Talla incorrecta"),
        ("wrong_item", "Artículo incorrecto"),
        ("not_as_described", "No era como se describía"),
        ("changed_mind", "Cambio de opinión"),
        ("other", "Otro"),
    ]
    STATUS_CHOICES = [
        ("requested", "Solicitada"),
        ("approved", "Aprobada"),
        ("received", "Recibida en bodega"),
        ("restocked", "Reingresada a inventario"),
        ("refunded", "Reembolsada"),
        ("rejected", "Rechazada"),
    ]
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="returns")
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="requested")
    notes = models.TextField(blank=True)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    restock = models.BooleanField(default=True, help_text="Reingresar al inventario al aprobar")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Devolución"
        verbose_name_plural = "Devoluciones"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Dev #{self.id} — Orden #{self.order_id} ({self.get_status_display()})"


class SizeChart(models.Model):
    TALLA_ORDER = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4, "XXL": 5, "XXXL": 6}
    product = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="size_chart")
    talla = models.CharField(max_length=10)
    pecho = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, help_text="cm")
    cintura = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, help_text="cm")
    largo = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, help_text="cm")
    hombro = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, help_text="cm")
    manga = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, help_text="cm (opcional)")

    class Meta:
        verbose_name = "Medida de talla"
        verbose_name_plural = "Tabla de tallas"
        unique_together = [["product", "talla"]]
        ordering = ["product"]

    def __str__(self):
        return f"{self.product.nombre} — {self.talla}"


def find_variant_for_selection(product, talla=None, color=None):
    variants = list(product.variants.filter(activo=True))
    if not variants:
        return None

    talla_key = _normalize_variant_value(talla)
    color_key = _normalize_variant_value(color)

    for variant in variants:
        if (
            _normalize_variant_value(variant.talla) == talla_key
            and _normalize_variant_value(variant.color) == color_key
        ):
            return variant

    return None


def available_stock_for_selection(product, talla=None, color=None):
    variant = find_variant_for_selection(product, talla=talla, color=color)
    if variant:
        return variant.stock
    return product.stock


def record_inventory_movement(
    *,
    product,
    movement_type,
    quantity_change,
    variant=None,
    order=None,
    note="",
    created_by=None,
    metadata=None,
):
    with transaction.atomic():
        if variant:
            stock_before = variant.stock
            stock_after = stock_before + quantity_change
            if stock_after < 0:
                raise ValueError(f"La variante {variant} no tiene stock suficiente.")
            variant.stock = stock_after
            variant.save(update_fields=["stock", "updated_at"])
        else:
            stock_before = product.stock
            stock_after = stock_before + quantity_change
            if stock_after < 0:
                raise ValueError(f"{product.nombre} no tiene stock suficiente.")
            product.stock = stock_after
            product.save(update_fields=["stock", "fecha_actualizacion"])

        return InventoryMovement.objects.create(
            product=product,
            variant=variant,
            order=order,
            movement_type=movement_type,
            quantity_change=quantity_change,
            stock_before=stock_before,
            stock_after=stock_after,
            note=note,
            metadata=metadata,
            created_by=created_by,
        )


class NewsletterSubscriber(models.Model):
    """
    Email captado por el popup de bienvenida. El cupón global se aplica
    al hacer checkout via 'allow_promotion_codes' de Stripe.
    """
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=40, default="popup", blank=True,
                              help_text="popup | footer | etc.")
    coupon_sent = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Suscriptor de newsletter"
        verbose_name_plural = "Suscriptores de newsletter"

    def __str__(self):
        return self.email
