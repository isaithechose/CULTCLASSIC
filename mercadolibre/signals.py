"""
Signals: cuando cambia el stock de un Producto local, lo empujamos a la
publicación de ML enlazada (si existe). Evita loops comparando con
last_pushed_stock antes de llamar al API.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from tienda.models import Producto

from . import api
from .models import MercadoLibreCredential, MercadoLibreListing

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Producto)
def push_stock_to_ml_on_save(sender, instance, **kwargs):
    update_fields = kwargs.get("update_fields") or set()
    # Si el save vino con update_fields explícito y NO incluye 'stock', salir
    if update_fields and "stock" not in update_fields:
        return

    listings = MercadoLibreListing.objects.filter(producto=instance)
    if not listings.exists():
        return

    cred = MercadoLibreCredential.objects.first()
    if not cred:
        return

    for listing in listings:
        if listing.last_pushed_stock == instance.stock:
            continue  # ya está sincronizado, evita API call inútil
        try:
            api.update_listing_stock(cred, listing.ml_id, instance.stock)
            logger.info("Stock empujado a ML: %s → %s unidades", listing.ml_id, instance.stock)
        except Exception:
            logger.exception("Fallo al empujar stock a ML para %s", listing.ml_id)
