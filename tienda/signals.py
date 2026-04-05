from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.mail import send_mail
from django.conf import settings
from .models import Order
# signals.py
@receiver(post_save, sender=Order)
def send_shipping_notification(sender, instance, created, **kwargs):
    # Solo nos interesa cuando el pedido ya existe (no en la creación) y su estado cambió a "Shipped"
    if not created and instance.status == 'Shipped' and instance.tracking_number:
        subject = f"Tu pedido #{instance.id} ha sido enviado"
        message = (
            f"Hola {instance.customer.username},\n\n"
            f"Tu pedido #{instance.id} ha sido enviado.\n"
            f"Número de seguimiento: {instance.tracking_number}\n\n"
            "Gracias por tu compra."
        )
        from_email = settings.DEFAULT_FROM_EMAIL
        recipient_list = [instance.customer.email]
        send_mail(subject, message, from_email, recipient_list)
def notify_shipping_update(sender, instance, created, **kwargs):
    if created:
        subject = f"Actualización en el envío de tu pedido #{instance.order.id}"
        message = instance.status_message
        recipient_list = [instance.order.customer.email]
        send_mail(subject, message, 'tu_correo@tudominio.com', recipient_list)
# signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Order

@receiver(post_save, sender=Order)
def notify_shipping_update(sender, instance, created, **kwargs):
    if not created:
        # Suponiendo que envías notificaciones cuando el estado de envío cambia
        send_mail(
            'Actualización de Envío',
            f'Su pedido #{instance.id} ha cambiado a: {instance.shipping_status}.',
            'no-reply@cultcalle.com',
            [instance.customer.email],
        )