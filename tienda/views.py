from django.shortcuts import render, redirect, get_object_or_404
from .models import Producto, OrderItem
from .forms import SeleccionarTallaColorForm, ReseñaForm, UserProfileForm
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
    # Ahora request.user es un usuario autenticado
    carrito = request.session.get('carrito', {})
    user = request.user  # Siempre autenticado
    total = sum(item['precio'] * item['cantidad'] for item in carrito.values())

    # Crea la orden con el usuario autenticado
    order = Order.objects.create(customer=user)
    request.session['order_id'] = order.id

    for key, item in carrito.items():
        product_id = key.split("-")[0]
        OrderItem.objects.create(
            order=order,
            product_id=product_id,
            quantity=item['cantidad'],
            price=item['precio']
        )

    return redirect('tienda:stripe_checkout')


@login_required
def shipping_details(request):
    order_id = request.session.get('order_id')
    if order_id:
        order = get_object_or_404(Order, id=order_id, customer=request.user)
    else:
        # Intenta recuperar la última orden pendiente del usuario
        order = Order.objects.filter(customer=request.user, status='Pending').order_by('-created_at').first()
        if not order:
            return redirect('tienda:tienda')
        # Guarda el order_id en la sesión
        request.session['order_id'] = order.id

    if request.method == 'POST':
        form = ShippingAddressForm(request.POST)
        if form.is_valid():
            shipping_address = form.save(commit=False)
            shipping_address.order = order
            shipping_address.save()
            # Una vez guardada la dirección, puedes eliminar el order_id si ya no es necesario
            del request.session['order_id']
            return redirect('tienda:order_detail', order_id=order.id)
    else:
        form = ShippingAddressForm()

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

        producto.stock -= 1
        producto.save()
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
        cantidad = carrito[key]['cantidad']
        producto = get_object_or_404(Producto, id=producto_id)
        # Restaura el stock del producto al eliminarlo del carrito
        producto.stock += cantidad
        producto.save()
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



def my_orders(request):
    orders_list = Order.objects.filter(customer=request.user).order_by('-created_at')
    paginator = Paginator(orders_list, 10)  # 10 pedidos por página
    page_number = request.GET.get('page')
    orders = paginator.get_page(page_number)
    return render(request, 'tienda/my_orders.html', {'orders': orders})

def order_detail(request, order_id):
    order = get_object_or_404(Order, id=order_id, customer=request.user)
    return render(request, 'tienda/order_detail.html', {'order': order})

def lista_productos(request):
    productos = Producto.objects.all()
    return render(request, 'tienda/lista_productos.html', {'productos': productos})

def filtrar_productos(request, categoria_id):
    productos = Producto.objects.filter(categoria__id=categoria_id)
    return render(request, 'tienda/filtrar_productos.html', {'productos': productos})
def stripe_checkout(request):
    # Configura la API key de Stripe con la clave secreta desde settings
    stripe.api_key = settings.STRIPE_SECRET_KEY

    # Obtén el carrito desde la sesión
    carrito = request.session.get('carrito', {})
    if not carrito:
        return redirect('tienda:carrito')  # Si el carrito está vacío, redirige

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
    session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=line_items,
        mode='payment',
        # Genera URLs absolutas para éxito y cancelación
        success_url=request.build_absolute_uri(reverse('tienda:payment_success')),
        cancel_url=request.build_absolute_uri(reverse('tienda:payment_cancel')),
        locale='es',  # Opcional: para mostrar el checkout en español
    )

    # Redirige al usuario a la URL de Stripe para procesar el pago
    return redirect(session.url, code=303)




def payment_success(request):
    order_id = request.session.get('order_id')
    if order_id:
        order = get_object_or_404(Order, id=order_id)
        order.status = 'Completed'  # O el estado que consideres adecuado
        order.save()
        # No eliminar el order_id aquí para que shipping_details pueda acceder a la orden
        # del request.session['order_id']

        # Enviar correo de confirmación, etc.
        subject = f"Pedido #{order.id} - Confirmación de Envío"
        message = f"Hola {order.customer.username},\n\nTu pedido #{order.id} ha sido procesado. Por favor, ingresa tu dirección de envío para continuar con el proceso.\n\n¡Gracias por comprar en Cult Calle!"
        from_email = settings.DEFAULT_FROM_EMAIL
        recipient_list = [order.customer.email]
        send_mail(subject, message, from_email, recipient_list)

    request.session['carrito'] = {}
    return redirect('tienda:shipping_details')


def payment_cancel(request):
    # Vista de cancelación del pago
    return render(request, 'tienda/payment_cancel.html')
from .forms import ShippingAddressForm


def order_tracking(request, order_id):
    order = get_object_or_404(Order, id=order_id, customer=request.user)
    updates = order.shipping_updates.all().order_by('-updated_at')
    return render(request, 'tienda/order_tracking.html', {
        'order': order,
        'updates': updates,
    })
def tracking_view(request):
    orders = Order.objects.filter(customer=request.user)
    return render(request, 'tienda/tracking.html', {'orders': orders})

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
