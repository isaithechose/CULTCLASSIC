from django.db import models, transaction
from django.contrib.auth.models import User
from django.conf import settings
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
    customer = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=[
        ('Pending', 'Pending'), ('Completed', 'Completed'), ('Canceled', 'Canceled')
    ], default='Pending')
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
        return self.subtotal_price + self.shipping_total

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
        super().save(*args, **kwargs)
        self.product.sync_stock_from_variants()

    def delete(self, *args, **kwargs):
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

    fecha = models.DateField()
    categoria = models.ForeignKey(ExpenseCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses")
    concepto = models.CharField(max_length=140)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    metodo_pago = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default="transfer")
    proveedor = models.CharField(max_length=120, blank=True, null=True)
    nota = models.TextField(blank=True, null=True)
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
