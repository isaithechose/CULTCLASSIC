from datetime import timedelta
from django.db import models
from django.utils import timezone


class MercadoLibreCredential(models.Model):
    """Token OAuth de un vendedor de Mercado Libre conectado a la app."""
    user_id = models.BigIntegerField(unique=True)
    nickname = models.CharField(max_length=120, blank=True)
    site_id = models.CharField(max_length=10, default="MLM", help_text="MLM=México, MLA=Argentina, etc.")
    access_token = models.TextField()
    refresh_token = models.TextField()
    expires_at = models.DateTimeField()
    connected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Credencial"
        verbose_name_plural = "Credenciales"

    def __str__(self):
        return self.nickname or str(self.user_id)

    def is_expired(self, buffer_minutes=5):
        return timezone.now() >= self.expires_at - timedelta(minutes=buffer_minutes)


class MercadoLibreOrder(models.Model):
    """Pedido sincronizado desde Mercado Libre."""
    ml_id = models.BigIntegerField(unique=True)
    status = models.CharField(max_length=40)
    date_created = models.DateTimeField()
    date_closed = models.DateTimeField(null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency_id = models.CharField(max_length=10, default="MXN")
    buyer_nickname = models.CharField(max_length=120, blank=True)
    buyer_id = models.BigIntegerField(null=True, blank=True)
    shipping_status = models.CharField(max_length=40, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_created"]
        verbose_name = "Pedido ML"
        verbose_name_plural = "Pedidos"

    def __str__(self):
        return f"ML #{self.ml_id} — {self.buyer_nickname or self.status}"


class MercadoLibreOrderItem(models.Model):
    order = models.ForeignKey(MercadoLibreOrder, related_name="items", on_delete=models.CASCADE)
    item_id = models.CharField(max_length=40)
    title = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Producto del pedido"
        verbose_name_plural = "Productos del pedido"

    def __str__(self):
        return f"{self.quantity}× {self.title[:60]}"


class MercadoLibreListing(models.Model):
    """Publicación (producto) en Mercado Libre."""
    ml_id = models.CharField(max_length=40, unique=True)
    producto = models.ForeignKey(
        "tienda.Producto", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="ml_listings",
        help_text="Producto local enlazado para sync de inventario bidireccional",
    )
    title = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency_id = models.CharField(max_length=10, default="MXN")
    available_quantity = models.PositiveIntegerField(default=0)
    sold_quantity = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20)
    permalink = models.URLField(blank=True)
    thumbnail = models.URLField(blank=True)
    listing_type_id = models.CharField(max_length=40, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(auto_now=True)
    last_pushed_stock = models.PositiveIntegerField(null=True, blank=True,
        help_text="Último stock que enviamos a ML; evita loops de sync")

    class Meta:
        ordering = ["-synced_at"]
        verbose_name = "Publicación"
        verbose_name_plural = "Publicaciones"

    def __str__(self):
        return self.title[:80]
