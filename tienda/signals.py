from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import (
    CreditCardStatement,
    Expense,
    InventoryMovement,
    Order,
    OrderItem,
    ProductVariant,
    ShippingUpdate,
)

_shipping_status_before = {}


@receiver(pre_save, sender=Order)
def capture_shipping_status(sender, instance, **kwargs):
    if not instance.pk:
        return
    _shipping_status_before[instance.pk] = (
        Order.objects.filter(pk=instance.pk)
        .values_list("shipping_status", flat=True)
        .first()
    )


@receiver(post_save, sender=Order)
def send_shipping_notification(sender, instance, created, update_fields=None, **kwargs):
    if created:
        return

    previous_status = _shipping_status_before.pop(instance.pk, None)
    if update_fields is not None and 'shipping_status' not in update_fields:
        return
    if previous_status == instance.shipping_status:
        return
    if instance.shipping_status != "Shipped" or not instance.tracking_number:
        return
    if not instance.customer or not instance.customer.email:
        return

    from django.template.loader import render_to_string
    subject = f"Tu pedido #{instance.id} ya va en camino — Cult Clasiccs"
    html_message = render_to_string('tienda/email/shipping_notification.html', {'order': instance})
    plain_message = (
        f"Hola {instance.customer.get_full_name() or instance.customer.username},\n\n"
        f"Tu pedido #{instance.id} fue enviado.\n"
        f"Número de seguimiento: {instance.tracking_number}\n\n"
        "Gracias por tu compra en Cult Clasiccs."
    )
    send_mail(subject, plain_message, settings.DEFAULT_FROM_EMAIL,
              [instance.customer.email], html_message=html_message)


@receiver(post_save, sender=ShippingUpdate)
def notify_shipping_update(sender, instance, created, **kwargs):
    if not created:
        return
    if not instance.order.customer or not instance.order.customer.email:
        return

    subject = f"Actualización en el envío de tu pedido #{instance.order.id}"
    message = instance.status_message
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [instance.order.customer.email])


# ── Invalidación del resumen del admin ──────────────────────────────────────
# _admin_overview_context() se cachea 30 s porque lo pide cada página del admin.
# Cuando cambia algo que ese resumen muestra, se tira el cache para que los
# contadores (pedidos pendientes, stock bajo, ventas del día) salgan al momento.

_OVERVIEW_MODELS = (
    Order,
    OrderItem,
    Expense,
    InventoryMovement,
    ProductVariant,
    CreditCardStatement,
)


def _clear_admin_overview_cache(**kwargs):
    from .admin import ADMIN_OVERVIEW_CACHE_KEY

    cache.delete(ADMIN_OVERVIEW_CACHE_KEY)


for _model in _OVERVIEW_MODELS:
    post_save.connect(
        _clear_admin_overview_cache,
        sender=_model,
        dispatch_uid=f"clear_admin_overview_save_{_model.__name__}",
    )
    post_delete.connect(
        _clear_admin_overview_cache,
        sender=_model,
        dispatch_uid=f"clear_admin_overview_delete_{_model.__name__}",
    )
