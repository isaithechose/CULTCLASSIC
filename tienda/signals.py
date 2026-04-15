from django.conf import settings
from django.core.mail import send_mail
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Order, ShippingUpdate


@receiver(post_save, sender=Order)
def send_shipping_notification(sender, instance, created, **kwargs):
    if created or instance.shipping_status != "Shipped" or not instance.tracking_number:
        return
    if not instance.customer or not instance.customer.email:
        return

    subject = f"Tu pedido #{instance.id} ha sido enviado"
    message = (
        f"Hola {instance.customer.username},\n\n"
        f"Tu pedido #{instance.id} ya fue enviado.\n"
        f"Número de seguimiento: {instance.tracking_number}\n\n"
        "Gracias por tu compra."
    )
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [instance.customer.email])


@receiver(post_save, sender=ShippingUpdate)
def notify_shipping_update(sender, instance, created, **kwargs):
    if not created:
        return
    if not instance.order.customer or not instance.order.customer.email:
        return

    subject = f"Actualización en el envío de tu pedido #{instance.order.id}"
    message = instance.status_message
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [instance.order.customer.email])
