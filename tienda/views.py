import json
import logging
from collections import defaultdict

from django.shortcuts import render, redirect, get_object_or_404
from .models import Producto, OrderItem, ShippingAddress
from .forms import (
    SeleccionarTallaColorForm,
    ReseñaForm,
    UserProfileForm,
    ShippingAddressForm,
)
import stripe
from django.conf import settings
from django.urls import reverse
from .models import Order
from django.core.mail import send_mail
from tienda.utils.importador_diseños import importar_diseños_desde_carpeta, importar_diseños_propios
import os
from django.core.paginator import Paginator
from .models import Producto, Categoria
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .skydrop import SkydropError, sync_shipment

logger = logging.getLogger(__name__)

def subir_diseño_personalizado(request):
    if request.method == 'POST' and request.FILES.get('imagen'):
        imagen = request.FILES['imagen']
        fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'diseños_propios'))
        filename = fs.save(imagen.name, imagen)
        messages.success(request, "¡Diseño subido correctamente!")
    else:
        messages.error(request, "Error al subir el diseño.")
    return redirect(request.META.get('HTTP_REFERER', 'tienda:detalle_producto'))

from urllib.parse import unquote as urlunquote

def detalle_producto(request, producto_id):
    producto = get_object_or_404(Producto, id=producto_id)
    tallas_disponibles = producto.tallas_disponibles.split(",") if producto.tallas_disponibles else []
    colores_disponibles = producto.colores_disponibles.split(",") if producto.colores_disponibles else []

    diseño_seleccionado = request.GET.get("diseño")

    diseños_anime = []
    diseños_personalizados = []

    try:
        ruta_anime = os.path.join(settings.MEDIA_ROOT, 'diseños_nuevos')
        ruta_personalizados = os.path.join(settings.MEDIA_ROOT, 'diseños_propios')

        diseños_anime = os.listdir(ruta_anime)
        diseños_personalizados = os.listdir(ruta_personalizados)
    except FileNotFoundError:
        pass

    # Paginación de diseños anime
    page_anime = request.GET.get("page_anime", 1)
    anime_paginator = Paginator(diseños_anime, 10)
    anime_page = anime_paginator.get_page(page_anime)

    # Paginación para personalizados
    page_personalizado = request.GET.get("page_personalizado", 1)
    personalizado_paginator = Paginator(diseños_personalizados, 10)
    personalizado_page = personalizado_paginator.get_page(page_personalizado)

    # ...

    return render(request, 'tienda/detalle_producto.html', {
        'producto': producto,
        'tallas_disponibles': tallas_disponibles,
        'colores_disponibles': colores_disponibles,
        'diseños_anime': anime_page,
        'diseños_personalizados': personalizado_page,
        'reseña_form': ReseñaForm(),
    })



from django.contrib.auth.decorators import login_required


def _build_order_from_cart(order, carrito):
    order.items.all().delete()
    for key, item in carrito.items():
        parts = key.split("-", 4)
        if len(parts) != 5:
            continue
        product_id, talla, color, diseño_pecho, diseño_espalda = parts
        OrderItem.objects.create(
            order=order,
            product_id=product_id,
            quantity=item["cantidad"],
            price=item["precio"],
            talla=talla,
            color=color,
            diseño_pecho=diseño_pecho or "",
            diseño_espalda=diseño_espalda or "",
        )
    return order


def _cart_stock_issues(carrito):
    quantities_by_product = defaultdict(int)
    for key, item in carrito.items():
        product_id = key.split("-", 1)[0]
        quantities_by_product[int(product_id)] += int(item["cantidad"])

    issues = []
    for product_id, requested_qty in quantities_by_product.items():
        producto = get_object_or_404(Producto, id=product_id)
        if producto.stock < requested_qty:
            issues.append((producto.nombre, producto.stock, requested_qty))
    return issues


def _get_checkout_order(request):
    carrito = request.session.get("carrito", {})
    if not carrito:
        return None

    stock_issues = _cart_stock_issues(carrito)
    if stock_issues:
        return None

    order_id = request.session.get("order_id")
    if order_id:
        existing_order = Order.objects.filter(
            id=order_id,
            customer=request.user,
            status="Pending",
        ).first()
        if existing_order:
            _build_order_from_cart(existing_order, carrito)
            return existing_order

    order = Order.objects.create(customer=request.user)
    _build_order_from_cart(order, carrito)
    request.session["order_id"] = order.id
    return order


@login_required
def profile_view(request):
    orders = Order.objects.filter(customer=request.user).order_by("-created_at")
    recent_orders = orders[:3]
    completed_orders = orders.filter(status="Completed").count()
    pending_orders = orders.filter(status="Pending").count()

    if request.method == "POST":
        form = UserProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Tu perfil se actualizo correctamente.")
            return redirect("tienda:profile")
        messages.error(request, "No pudimos guardar tus cambios. Revisa los campos e intenta de nuevo.")
    else:
        form = UserProfileForm(instance=request.user)

    context = {
        "form": form,
        "orders_count": orders.count(),
        "completed_orders": completed_orders,
        "pending_orders": pending_orders,
        "recent_orders": recent_orders,
    }
    return render(request, "tienda/profile.html", context)


@login_required
def checkout(request):
    carrito = request.session.get("carrito", {})
    stock_issues = _cart_stock_issues(carrito)
    if stock_issues:
        for nombre, stock, requested_qty in stock_issues:
            messages.error(
                request,
                f"{nombre} ya no tiene stock suficiente. Disponible: {stock}, solicitado: {requested_qty}."
            )
        return redirect("tienda:carrito")

    order = _get_checkout_order(request)
    if order is None:
        messages.error(request, "No pudimos preparar tu compra. Revisa tu carrito e inténtalo de nuevo.")
        return redirect("tienda:carrito")
    return redirect("tienda:shipping_details")


@login_required
def shipping_details(request):
    order_id = request.session.get('order_id')
    if order_id:
        order = get_object_or_404(Order, id=order_id, customer=request.user)
    else:
        messages.error(request, "Tu sesión de compra expiró. Vuelve al carrito para continuar.")
        return redirect('tienda:carrito')

    if request.method == 'POST':
        form = ShippingAddressForm(request.POST)
        if form.is_valid():
            ShippingAddress.objects.update_or_create(
                order=order,
                defaults=form.cleaned_data,
            )
            if getattr(settings, "SKYDROP_CLIENT_ID", "") and getattr(settings, "SKYDROP_CLIENT_SECRET", ""):
                messages.info(
                    request,
                    "Dirección guardada. El pago queda como último paso y después podrás cotizar o crear la guía de Skydrop."
                )
            else:
                messages.success(request, "Dirección guardada. Ahora continúa con el pago.")
            request.session['order_id'] = order.id
            return redirect('tienda:stripe_checkout')
    else:
        initial = {}
        if hasattr(order, "shipping_address"):
            shipping_address = order.shipping_address
            initial = {
                "phone": shipping_address.phone,
                "address_line1": shipping_address.address_line1,
                "address_line2": shipping_address.address_line2,
                "city": shipping_address.city,
                "state": shipping_address.state,
                "postal_code": shipping_address.postal_code,
                "country": shipping_address.country,
            }
        form = ShippingAddressForm(initial=initial)

    return render(request, 'tienda/shipping_details.html', {'form': form})

def tienda_view(request):
    productos = Producto.objects.filter(categoria__nombre__iexact="cortes")  # o "Cortes"
    carrito = request.session.get('carrito', {})
    carrito_items_count = sum(item['cantidad'] for item in carrito.values())

    return render(request, 'tienda/index.html', {
        'productos': productos,
        'carrito_items_count': carrito_items_count,
    })


import os

def extraer_precio_desde_nombre(nombre_archivo):
    import re
    nombre_archivo = nombre_archivo.split('/')[-1]
    coincidencia = re.search(r'_(\d+)\.png$', nombre_archivo)
    if coincidencia:
        return int(coincidencia.group(1))
    return 0

def agregar_al_carrito(request, producto_id):
    producto = get_object_or_404(Producto, id=producto_id)

    if producto.stock <= 0:
        return redirect('tienda:tienda')

    if request.method == 'POST':
        talla = request.POST.get('talla')
        color = request.POST.get('color')
        diseño_pecho = request.POST.get('diseño_pecho', '')
        diseño_espalda = request.POST.get('diseño_espalda', '')
        action = request.POST.get('action', 'add_to_cart')

        if not talla or not color:
            messages.error(request, "Selecciona una talla y un color antes de continuar.")
            return redirect('tienda:detalle_producto', producto_id=producto.id)

        carrito = request.session.get('carrito', {})
        item_key = f"{producto_id}-{talla}-{color}-{diseño_pecho}-{diseño_espalda}"

        # Suma precio base + extra por cada diseño
        PRECIO_DISENO = 200
        precio_pecho = PRECIO_DISENO if diseño_pecho else 0
        precio_espalda = PRECIO_DISENO if diseño_espalda else 0
        precio_total = float(producto.precio) + precio_pecho + precio_espalda

        if item_key in carrito:
            carrito[item_key]['cantidad'] += 1
        else:
            carrito[item_key] = {
                'nombre': producto.nombre,
                'precio': precio_total,
                'cantidad': 1,
                'talla': talla,
                'color': color,
                'diseño_pecho': diseño_pecho,
                'diseño_espalda': diseño_espalda,
            }

        request.session['carrito'] = carrito
        if action == 'buy_now':
            if request.user.is_authenticated:
                return redirect('tienda:checkout')
            login_url = reverse('account_login')
            checkout_url = reverse('tienda:checkout')
            return redirect(f'{login_url}?next={checkout_url}')

        messages.success(request, f"{producto.nombre} se agrego a tu carrito.")
        return redirect('tienda:carrito')

    return redirect('tienda:detalle_producto', producto_id=producto.id)

def carrito_view(request):
    carrito = request.session.get('carrito', {})
    carrito_items = []
    total = 0

    for key, item in carrito.items():
        parts = key.split("-", 4)
        if len(parts) != 5:
            continue

        producto_id, talla, color, diseño_pecho, diseño_espalda = parts
        producto = get_object_or_404(Producto, id=producto_id)

        # ⚡ Usa el precio ya guardado en la sesión
        precio_unitario_total = item['precio']
        subtotal = precio_unitario_total * item['cantidad']

        carrito_items.append({
            'producto_id': producto_id,
            'nombre': producto.nombre,
            'imagen': producto.imagen.url if producto.imagen else None,
            'talla': talla,
            'color': color,
            'cantidad': item['cantidad'],
            'precio': precio_unitario_total,
            'subtotal': subtotal,
            'diseño_pecho': item.get('diseño_pecho', ''),
            'diseño_espalda': item.get('diseño_espalda', ''),
        })

        total += subtotal

    return render(request, 'tienda/carrito.html', {
        'carrito': carrito_items,
        'total': total,
    })

def eliminar_del_carrito(request, producto_id):
    carrito = request.session.get('carrito', {})

    # Encuentra las claves que correspondan al producto_id dado
    keys_to_remove = [key for key in carrito if key.startswith(f"{producto_id}-")]

    for key in keys_to_remove:
        del carrito[key]

    # Guarda el carrito actualizado
    request.session['carrito'] = carrito
    return redirect('tienda:carrito')

def archivo_view(request):
    productos = Producto.objects.filter(categoria__nombre="archivo")
    return render(request, 'tienda/archivo.html', {'productos': productos})

# Ejemplo de modelo para items del carrito (si se utiliza en otro contexto)
from django.db import models

class ItemCarrito(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE)
    cantidad = models.PositiveIntegerField(default=1)
    talla = models.CharField(max_length=5, blank=True, null=True)
    color = models.CharField(max_length=20, blank=True, null=True)

    def subtotal(self):
        return self.cantidad * self.producto.precio

def proceso_compra(request):
    # Obtiene los datos del carrito desde la sesión
    carrito = request.session.get('carrito', {})
    if not carrito:
        return redirect('tienda:carrito')  # Redirige si el carrito está vacío

    total = sum(item['precio'] * item['cantidad'] for item in carrito.values())

    # Aquí podrías integrar el formulario de pago y procesamiento real de la compra

    # Limpiar el carrito tras la compra
    request.session['carrito'] = {}

    return render(request, 'tienda/proceso_compra.html', {
        'total': total,
        'mensaje': '¡Gracias por tu compra! Tu pedido se está procesando.',
    })



@login_required
def my_orders(request):
    orders_list = Order.objects.filter(customer=request.user).order_by('-created_at')
    paginator = Paginator(orders_list, 10)  # 10 pedidos por página
    page_number = request.GET.get('page')
    orders = paginator.get_page(page_number)
    return render(request, 'tienda/my_orders.html', {'orders': orders})

@login_required
def order_detail(request, order_id):
    order = get_object_or_404(Order, id=order_id, customer=request.user)
    return render(request, 'tienda/order_detail.html', {'order': order})

def lista_productos(request):
    productos = Producto.objects.all()
    return render(request, 'tienda/lista_productos.html', {'productos': productos})

def filtrar_productos(request, categoria_id):
    productos = Producto.objects.filter(categoria__id=categoria_id)
    return render(request, 'tienda/filtrar_productos.html', {'productos': productos})
@login_required
def stripe_checkout(request):
    # Configura la API key de Stripe con la clave secreta desde settings
    stripe.api_key = settings.STRIPE_SECRET_KEY

    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        messages.error(
            request,
            "El checkout no está configurado todavía. Agrega las credenciales de Stripe en producción e inténtalo de nuevo."
        )
        return redirect("tienda:shipping_details")

    # Obtén el carrito desde la sesión
    carrito = request.session.get('carrito', {})
    if not carrito:
        return redirect('tienda:carrito')  # Si el carrito está vacío, redirige

    stock_issues = _cart_stock_issues(carrito)
    if stock_issues:
        for nombre, stock, requested_qty in stock_issues:
            messages.error(
                request,
                f"{nombre} ya no tiene stock suficiente. Disponible: {stock}, solicitado: {requested_qty}."
            )
        return redirect("tienda:carrito")

    order = _get_checkout_order(request)
    if order is None:
        messages.error(request, "No pudimos preparar tu pedido para el checkout.")
        return redirect("tienda:carrito")
    if not hasattr(order, "shipping_address"):
        messages.error(request, "Primero necesitamos tu dirección de envío.")
        return redirect("tienda:shipping_details")

    # Prepara los items para Stripe (Stripe requiere montos en centavos)
    line_items = []
    for key, item in carrito.items():
        # Convierte el precio a centavos (por ejemplo, 299.00 MXN -> 29900)
        price_in_cents = int(item['precio'] * 100)
        line_items.append({
            'price_data': {
                'currency': 'mxn',  # ← AQUÍ CAMBIASTE A PESOS MEXICANOS
                'product_data': {
                    'name': item['nombre'],
                },
                'unit_amount': price_in_cents,
            },
            'quantity': item['cantidad'],
        })

    # Crea la sesión de Checkout de Stripe
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=line_items,
            mode='payment',
            metadata={"order_id": str(order.id)},
            # Genera URLs absolutas para éxito y cancelación
            success_url=request.build_absolute_uri(reverse('tienda:payment_success')) + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=request.build_absolute_uri(reverse('tienda:payment_cancel')),
            locale='es',  # Opcional: para mostrar el checkout en español
        )
    except stripe.error.StripeError as exc:
        logger.exception("Stripe checkout session creation failed for order %s", order.id)
        user_message = getattr(exc, "user_message", None) or str(exc) or "No pudimos iniciar el pago con Stripe."
        messages.error(request, f"Stripe no pudo iniciar el pago: {user_message}")
        return redirect("tienda:shipping_details")
    except Exception:
        logger.exception("Unexpected error while creating Stripe checkout session for order %s", order.id)
        messages.error(request, "Ocurrió un problema inesperado al iniciar el checkout. Inténtalo de nuevo.")
        return redirect("tienda:shipping_details")

    order.stripe_session_id = session.id
    order.save(update_fields=["stripe_session_id"])

    # Redirige al usuario a la URL de Stripe para procesar el pago
    return redirect(session.url, code=303)

@login_required
def payment_success(request):
    session_id = request.GET.get("session_id")
    order_id = request.session.get("order_id")

    if not session_id or not order_id:
        messages.error(request, "No pudimos validar tu pago.")
        return redirect("tienda:carrito")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
    except stripe.error.StripeError as exc:
        logger.exception("Stripe checkout session retrieve failed for session %s", session_id)
        user_message = getattr(exc, "user_message", None) or str(exc) or "No pudimos validar tu pago con Stripe."
        messages.error(request, f"No pudimos validar el pago: {user_message}")
        return redirect("tienda:carrito")
    except Exception:
        logger.exception("Unexpected error while retrieving Stripe checkout session %s", session_id)
        messages.error(request, "Ocurrió un problema inesperado al validar tu pago.")
        return redirect("tienda:carrito")

    if checkout_session.payment_status != "paid":
        messages.error(request, "Tu pago todavía no aparece como completado.")
        return redirect("tienda:order_detail", order_id=order_id)

    order = get_object_or_404(Order, id=order_id, customer=request.user)
    if order.stripe_session_id and order.stripe_session_id != session_id:
        messages.error(request, "La sesión de pago no coincide con este pedido.")
        return redirect("tienda:carrito")

    if order.status != "Completed":
        stock_issues = []
        for item in order.items.select_related("product"):
            if item.product.stock < item.quantity:
                stock_issues.append((item.product.nombre, item.product.stock, item.quantity))

        if stock_issues:
            for nombre, stock, requested_qty in stock_issues:
                messages.error(
                    request,
                    f"{nombre} ya no tiene stock suficiente para finalizar. Disponible: {stock}, solicitado: {requested_qty}."
                )
            return redirect("tienda:carrito")

        for item in order.items.select_related("product"):
            item.product.stock -= item.quantity
            item.product.save(update_fields=["stock"])

        order.status = "Completed"
        order.save(update_fields=["status"])

        subject = f"Pedido #{order.id} - Confirmación de Envío"
        message = (
            f"Hola {order.customer.username},\n\n"
            f"Tu pedido #{order.id} ha sido procesado. Por favor, ingresa tu dirección de envío para continuar con el proceso.\n\n"
            "¡Gracias por comprar en Cult Calle!"
        )
        from_email = settings.DEFAULT_FROM_EMAIL
        recipient_list = [order.customer.email]
        send_mail(subject, message, from_email, recipient_list)

    request.session['carrito'] = {}
    return redirect('tienda:order_detail', order_id=order.id)


def payment_cancel(request):
    messages.warning(request, "El pago fue cancelado. Tu pedido sigue pendiente para que puedas retomarlo.")
    if request.session.get("order_id"):
        return redirect("tienda:shipping_details")
    return render(request, 'tienda/payment_cancel.html')
from .forms import ShippingAddressForm


@login_required
def order_tracking(request, order_id):
    order = get_object_or_404(Order, id=order_id, customer=request.user)
    updates = order.shipping_updates.all().order_by('-updated_at')
    return render(request, 'tienda/order_tracking.html', {
        'order': order,
        'updates': updates,
    })

@login_required
def tracking_view(request):
    orders = Order.objects.filter(customer=request.user)
    return render(request, 'tienda/tracking.html', {'orders': orders})


def _map_skydrop_status(raw_status):
    normalized = (raw_status or "").lower()
    if "deliver" in normalized:
        return "Delivered"
    if any(token in normalized for token in ["transit", "ship", "pickup", "label"]):
        return "Shipped"
    return "Processing"


@csrf_exempt
def skydrop_webhook(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    attributes = data.get("attributes", {}) if isinstance(data, dict) else {}
    shipment_id = data.get("id") or payload.get("shipment_id")
    tracking_number = (
        attributes.get("master_tracking_number")
        or attributes.get("tracking_number")
        or payload.get("tracking_number")
    )
    raw_status = attributes.get("status") or payload.get("status")

    order = None
    if shipment_id:
        order = Order.objects.filter(skydrop_shipment_id=shipment_id).first()
    if order is None and tracking_number:
        order = Order.objects.filter(tracking_number=tracking_number).first()
    if order is None:
        return JsonResponse({"ok": True, "ignored": True})

    order.skydrop_last_payload = payload
    if shipment_id:
        order.skydrop_shipment_id = shipment_id
    if tracking_number:
        order.tracking_number = tracking_number
    if raw_status:
        order.shipping_status = _map_skydrop_status(raw_status)
    order.save(update_fields=[
        "skydrop_last_payload",
        "skydrop_shipment_id",
        "tracking_number",
        "shipping_status",
    ])

    status_message = f"Skydrop actualizo el envío a: {raw_status or order.shipping_status}."
    if tracking_number:
        status_message += f" Tracking: {tracking_number}."
    order.shipping_updates.create(status_message=status_message)
    return JsonResponse({"ok": True})


@login_required
def sync_skydrop_order(request, order_id):
    order = get_object_or_404(Order, id=order_id, customer=request.user)
    try:
        payload = sync_shipment(order)
    except SkydropError as exc:
        messages.error(request, str(exc))
        return redirect("tienda:order_detail", order_id=order.id)
    except Exception:
        messages.error(request, "No pudimos sincronizar este envío con Skydrop.")
        return redirect("tienda:order_detail", order_id=order.id)

    order.tracking_number = payload.get("tracking_number") or order.tracking_number
    order.skydrop_tracking_url = payload.get("tracking_url") or order.skydrop_tracking_url
    order.skydrop_carrier = payload.get("carrier") or order.skydrop_carrier
    order.skydrop_service = payload.get("service") or order.skydrop_service
    if payload.get("status"):
        order.shipping_status = _map_skydrop_status(payload["status"])
    order.skydrop_last_payload = payload.get("payload")
    order.save()
    messages.success(request, "Actualizamos el tracking desde Skydrop.")
    return redirect("tienda:order_detail", order_id=order.id)

def catalogo_diseños(request):
    categoria_diseños = Categoria.objects.filter(nombre__icontains="dise").first()

    if not categoria_diseños:
        productos_diseños = Producto.objects.none()
    else:
        productos_diseños = Producto.objects.filter(categoria=categoria_diseños, disponible=True).order_by('-fecha_creacion')

    paginator = Paginator(productos_diseños, 15)  # 12 productos por página
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'tienda/catalogo_diseños.html', {
        'page_obj': page_obj
    })
def catalogo_diseños_propios(request):
    nuevos = importar_diseños_propios()
    productos = Producto.objects.filter(categoria__nombre__iexact="Diseños Propios")
    return render(request, 'tienda/catalogo_diseños_propios.html', {
        'productos': productos,
        'nuevos': nuevos,

    })
def subir_diseño_personalizado(request):
    if request.method == 'POST' and request.FILES.get('imagen'):
        imagen = request.FILES['imagen']
        fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'diseños_propios'))
        filename = fs.save(imagen.name, imagen)
        messages.success(request, "¡Diseño subido correctamente!")
    else:
        messages.error(request, "Error al subir el diseño.")
    return redirect(request.META.get('HTTP_REFERER', 'tienda:detalle_producto'))
