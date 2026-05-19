"""
Signals:
1. Cuando cambia el stock de un Producto local → empuja a publicación ML enlazada.
2. Cuando se llena el tracking_number en un MercadoLibreOrder → push a Mercado Envíos.
"""
import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from tienda.models import Producto, ProductVariant

from . import api
from .models import MercadoLibreCredential, MercadoLibreListing, MercadoLibreOrder

logger = logging.getLogger(__name__)


_tracking_before = {}


@receiver(pre_save, sender=MercadoLibreOrder)
def capture_tracking_before(sender, instance, **kwargs):
    if not instance.pk:
        return
    _tracking_before[instance.pk] = (
        MercadoLibreOrder.objects.filter(pk=instance.pk)
        .values_list("tracking_number", flat=True).first()
    )


@receiver(post_save, sender=MercadoLibreOrder)
def push_tracking_on_save(sender, instance, created, **kwargs):
    if created:
        return
    previous = _tracking_before.pop(instance.pk, None)
    if not instance.tracking_number:
        return
    if previous == instance.tracking_number:
        return  # no cambió, no hacer nada
    if not instance.shipping_id:
        logger.warning("Order ML #%s tiene tracking pero no shipping_id, no se puede pushear", instance.ml_id)
        return
    cred = MercadoLibreCredential.objects.first()
    if not cred:
        return
    try:
        api.push_tracking_to_ml(
            cred, instance.shipping_id,
            instance.tracking_number, instance.tracking_carrier,
        )
        MercadoLibreOrder.objects.filter(pk=instance.pk).update(
            pushed_tracking_at=timezone.now()
        )
        logger.info("Tracking %s empujado a ML envío %s", instance.tracking_number, instance.shipping_id)
    except Exception:
        logger.exception("Fallo al empujar tracking a ML para envío %s", instance.shipping_id)


def _push_producto_stock(producto):
    """Empuja el stock del producto a sus publicaciones ML enlazadas.
    update_listing_stock detecta variations[] y mapea por color/talla."""
    listings = MercadoLibreListing.objects.filter(producto=producto)
    if not listings.exists():
        return
    cred = MercadoLibreCredential.objects.first()
    if not cred:
        return
    for listing in listings:
        try:
            api.update_listing_stock(cred, listing.ml_id, producto)
        except Exception:
            logger.exception("Fallo al empujar stock a ML para listing %s", listing.ml_id)


@receiver(post_save, sender=Producto)
def push_stock_to_ml_on_producto_save(sender, instance, **kwargs):
    update_fields = kwargs.get("update_fields") or set()
    if update_fields and "stock" not in update_fields:
        return
    _push_producto_stock(instance)


@receiver(post_save, sender=ProductVariant)
def push_stock_to_ml_on_variant_save(sender, instance, **kwargs):
    update_fields = kwargs.get("update_fields") or set()
    if update_fields and "stock" not in update_fields:
        return
    if not instance.product_id:
        return
    _push_producto_stock(instance.product)
