from django.db import models
from django.contrib.auth.models import User

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
    stripe_session_id = models.CharField(max_length=255, blank=True, null=True)

    @property
    def total_price(self):
        return sum(item.price * item.quantity for item in self.items.all())

    def __str__(self):
        return f"Orden {self.id} de {self.customer}"

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
