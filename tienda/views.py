import json
import logging
import base64
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.shortcuts import render, redirect, get_object_or_404
from .models import (
    Producto,
    OrderItem,
    ShippingAddress,
    Order,
    ProductVariant,
    find_variant_for_selection,
    available_stock_for_selection,
    record_inventory_movement,
)
from .forms import (
    SeleccionarTallaColorForm,
    ReseñaForm,
    UserProfileForm,
    ShippingAddressForm,
    CustomDesignUploadForm,
)
import stripe
from django.conf import settings
from django.urls import reverse
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
from django.contrib.auth.decorators import login_required
from django.utils.text import slugify

from .skydrop import SkydropError, quote_order, sync_shipment

logger = logging.getLogger(__name__)


def _track_meta_pixel_event(request, event_name, payload=None, persist=False):
    event = {
        "name": event_name,
        "payload": payload or {},
    }
    if persist:
        events = request.session.get("meta_pixel_events", [])
        events.append(event)
        request.session["meta_pixel_events"] = events
        return

    if not hasattr(request, "_meta_pixel_events"):
        request._meta_pixel_events = []
    request._meta_pixel_events.append(event)


def _custom_designs_dir():
    return Path(settings.MEDIA_ROOT) / "diseños_propios"


def _save_custom_design(user, design_name, uploaded_file=None, edited_image_data=None):
    designs_dir = _custom_designs_dir()
    designs_dir.mkdir(parents=True, exist_ok=True)

    base_name = slugify(design_name) or "diseno"
    owner_prefix = slugify(getattr(user, "username", "") or "cliente")
    filename_base = f"{owner_prefix}-{base_name}"

    if edited_image_data:
        header, _, encoded = edited_image_data.partition(",")
        if ";base64" not in header or not encoded:
            raise ValueError("La imagen editada no llegó en un formato válido.")
        decoded = base64.b64decode(encoded)
        final_name = f"{filename_base}.png"
        file_path = designs_dir / final_name
        counter = 1
        while file_path.exists():
            final_name = f"{filename_base}-{counter}.png"
            file_path = designs_dir / final_name
            counter += 1
        file_path.write_bytes(decoded)
        return final_name

    if uploaded_file:
        extension = Path(uploaded_file.name).suffix.lower() or ".png"
        final_name = f"{filename_base}{extension}"
        file_path = designs_dir / final_name
        counter = 1
        while file_path.exists():
            final_name = f"{filename_base}-{counter}{extension}"
            file_path = designs_dir / final_name
            counter += 1
        fs = FileSystemStorage(location=str(designs_dir))
        return fs.save(final_name, uploaded_file)

    raise ValueError("Necesitamos una imagen para guardar el diseño.")

@login_required
def subir_diseño_personalizado(request):
    if request.method != 'POST':
        return redirect(request.META.get('HTTP_REFERER', 'tienda:detalle_producto'))

    design_name = request.POST.get("name", "").strip() or request.POST.get("nombre", "").strip() or "diseno"
    edited_image = request.POST.get("edited_image", "").strip()
    imagen = request.FILES.get('imagen') or request.FILES.get("image")
    redirect_to = request.POST.get("redirect_to", "").strip()
    product_id = request.POST.get("product_id", "").strip()
    try:
        saved_name = _save_custom_design(
            request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
            design_name,
            uploaded_file=imagen,
            edited_image_data=edited_image,
        )
    except Exception as exc:
        messages.error(request, f"No pudimos guardar tu diseño: {exc}")
        return redirect(redirect_to or request.META.get('HTTP_REFERER', 'tienda:detalle_producto'))

    messages.success(request, "Tu diseño quedó guardado y listo para usar.")
    if product_id:
        return redirect(f"{reverse('tienda:detalle_producto', args=[product_id])}?diseño={saved_name}")
    if redirect_to:
        return redirect(redirect_to)
    return redirect(f"{reverse('tienda:design_creator')}?file={saved_name}")


@login_required
def design_creator(request):
    designs_dir = _custom_designs_dir()
    designs_dir.mkdir(parents=True, exist_ok=True)

    selected_file = request.GET.get("file", "").strip()
    selected_product_id = request.GET.get("product_id", "").strip()
    selected_design_name = ""
    selected_design_url = ""

    customizable_products = Producto.objects.filter(disponible=True, stock__gt=0).order_by("nombre")

    if selected_file:
        safe_name = Path(selected_file).name
        selected_path = designs_dir / safe_name
        if selected_path.exists():
            selected_design_name = Path(safe_name).stem
            selected_design_url = f"{settings.MEDIA_URL}diseños_propios/{safe_name}"

    if request.method == "POST":
        form = CustomDesignUploadForm(request.POST, request.FILES)
        selected_product_id = request.POST.get("product_id", "").strip()
        if form.is_valid():
            try:
                saved_name = _save_custom_design(
                    request.user,
                    form.cleaned_data["name"],
                    uploaded_file=form.cleaned_data.get("image"),
                    edited_image_data=form.cleaned_data.get("edited_image"),
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Tu diseño quedó guardado y listo para usar.")
                if selected_product_id and Producto.objects.filter(id=selected_product_id, disponible=True).exists():
                    return redirect(f"{reverse('tienda:detalle_producto', args=[selected_product_id])}?diseño={saved_name}")
                return redirect(f"{reverse('tienda:design_creator')}?file={saved_name}")
        else:
            messages.error(request, "Revisa el nombre o la imagen e inténtalo de nuevo.")
    else:
        form = CustomDesignUploadForm(initial={"name": selected_design_name})

    own_designs = sorted(
        [path.name for path in designs_dir.iterdir() if path.is_file()],
        key=lambda file_name: (designs_dir / file_name).stat().st_mtime,
        reverse=True,
    )[:18]

    context = {
        "form": form,
        "selected_design_name": selected_design_name,
        "selected_design_url": selected_design_url,
        "saved_designs": own_designs,
        "selected_product_id": selected_product_id,
        "customizable_products": customizable_products,
    }
    return render(request, "tienda/design_creator.html", context)

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

    selected_custom_design = ""
    if diseño_seleccionado and diseño_seleccionado in diseños_personalizados:
        selected_custom_design = diseño_seleccionado

    # Paginación de diseños anime
    page_anime = request.GET.get("page_anime", 1)
    anime_paginator = Paginator(diseños_anime, 10)
    anime_page = anime_paginator.get_page(page_anime)

    # Paginación para personalizados
    page_personalizado = request.GET.get("page_personalizado", 1)
    personalizado_paginator = Paginator(diseños_personalizados, 10)
    personalizado_page = personalizado_paginator.get_page(page_personalizado)

    # ...

    _track_meta_pixel_event(
        request,
        "ViewContent",
        {
            "content_ids": [str(producto.id)],
            "content_name": producto.nombre,
            "content_type": "product",
            "value": float(producto.precio),
            "currency": "MXN",
        },
    )

    return render(request, 'tienda/detalle_producto.html', {
        'producto': producto,
        'tallas_disponibles': tallas_disponibles,
        'colores_disponibles': colores_disponibles,
        'diseños_anime': anime_page,
        'diseños_personalizados': personalizado_page,
        'selected_custom_design': selected_custom_design,
        'reseña_form': ReseñaForm(),
    })
def _build_order_from_cart(order, carrito, reset_checkout_state=True):
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
    if reset_checkout_state:
        order.skydrop_quotation_id = None
        order.skydrop_rate_id = None
        order.skydrop_carrier = None
        order.skydrop_service = None
        order.skydrop_last_error = ""
        order.skydrop_last_payload = None
        order.shipping_quote_amount = None
        order.shipping_quote_currency = "MXN"
        order.stripe_session_id = None
        order.save(
            update_fields=[
                "skydrop_quotation_id",
                "skydrop_rate_id",
                "skydrop_carrier",
                "skydrop_service",
                "skydrop_last_error",
                "skydrop_last_payload",
                "shipping_quote_amount",
                "shipping_quote_currency",
                "stripe_session_id",
            ]
        )
    return order


def _cart_matches_order(order, carrito):
    def _normalize_amount(value):
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    expected_items = {}
    for key, item in carrito.items():
        expected_items[key] = {
            "cantidad": int(item["cantidad"]),
            "precio": _normalize_amount(item["precio"]),
        }

    current_items = {}
    for item in order.items.select_related("product").all():
        item_key = f"{item.product_id}-{item.talla or ''}-{item.color or ''}-{item.diseño_pecho or ''}-{item.diseño_espalda or ''}"
        current_items[item_key] = {
            "cantidad": int(item.quantity),
            "precio": _normalize_amount(item.price),
        }

    return current_items == expected_items


def _cart_stock_issues(carrito):
    quantities_by_variant = defaultdict(int)
    quantities_by_product = defaultdict(int)
    for key, item in carrito.items():
        parts = key.split("-", 4)
        if len(parts) != 5:
            continue
        product_id, talla, color, _, _ = parts
        product = get_object_or_404(Producto, id=int(product_id))
        variant = find_variant_for_selection(product, talla=talla, color=color)
        if variant:
            quantities_by_variant[variant.id] += int(item["cantidad"])
        else:
            quantities_by_product[int(product_id)] += int(item["cantidad"])

    issues = []
    for variant_id, requested_qty in quantities_by_variant.items():
        variant = get_object_or_404(ProductVariant, id=variant_id)
        if variant.stock < requested_qty:
            issues.append(
                (
                    f"{variant.product.nombre} ({variant.color} / {variant.talla})",
                    variant.stock,
                    requested_qty,
                )
            )

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
            if not _cart_matches_order(existing_order, carrito):
                _build_order_from_cart(existing_order, carrito, reset_checkout_state=True)
            return existing_order

    order = Order.objects.create(customer=request.user)
    _build_order_from_cart(order, carrito)
    request.session["order_id"] = order.id
    return order


def _customer_visible_orders(user):
    return Order.objects.filter(customer=user, status="Completed").order_by("-created_at")


def _has_skydrop_credentials():
    return bool(getattr(settings, "SKYDROP_CLIENT_ID", "")) and bool(getattr(settings, "SKYDROP_CLIENT_SECRET", ""))


def _fallback_shipping_amount():
    return Decimal(str(getattr(settings, "FALLBACK_SHIPPING_FLAT_RATE", "199.00"))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _apply_manual_shipping_quote(order, reason=""):
    amount = _fallback_shipping_amount()
    order.skydrop_quotation_id = None
    order.skydrop_rate_id = None
    order.skydrop_carrier = "Tarifa fija"
    order.skydrop_service = "Envío estándar"
    order.shipping_quote_amount = amount
    order.shipping_quote_currency = "MXN"
    order.stripe_session_id = None
    order.skydrop_last_error = reason
    order.save(
        update_fields=[
            "skydrop_quotation_id",
            "skydrop_rate_id",
            "skydrop_carrier",
            "skydrop_service",
            "shipping_quote_amount",
            "shipping_quote_currency",
            "stripe_session_id",
            "skydrop_last_error",
        ]
    )
    return amount


def _apply_skydrop_quote(order):
    result = quote_order(order)
    best_rate = result["best_rate"]
    amount = Decimal(str(best_rate["amount"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    order.skydrop_quotation_id = result["quotation_id"]
    order.skydrop_rate_id = best_rate["id"]
    order.skydrop_carrier = best_rate["carrier"]
    order.skydrop_service = best_rate["service"]
    order.shipping_quote_amount = amount
    order.shipping_quote_currency = best_rate.get("currency") or "MXN"
    order.skydrop_last_payload = result["payload"]
    order.skydrop_last_error = ""
    order.stripe_session_id = None
    order.save(
        update_fields=[
            "skydrop_quotation_id",
            "skydrop_rate_id",
            "skydrop_carrier",
            "skydrop_service",
            "shipping_quote_amount",
            "shipping_quote_currency",
            "skydrop_last_payload",
            "skydrop_last_error",
            "stripe_session_id",
        ]
    )
    return amount


@login_required
def profile_view(request):
    all_orders = Order.objects.filter(customer=request.user).order_by("-created_at")
    visible_orders = _customer_visible_orders(request.user)
    recent_orders = visible_orders[:3]
    completed_orders = visible_orders.count()
    pending_orders = all_orders.filter(status="Pending").count()

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
        "orders_count": all_orders.count(),
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
    _track_meta_pixel_event(
        request,
        "InitiateCheckout",
        {
            "content_type": "product",
            "num_items": sum(item.quantity for item in order.items.all()),
            "value": float(order.subtotal_price),
            "currency": "MXN",
        },
        persist=True,
    )
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
            if _has_skydrop_credentials():
                try:
                    amount = _apply_skydrop_quote(order)
                    shipping_note = (
                        f"Envío cotizado antes del pago. "
                        f"{order.skydrop_carrier or 'Skydrop'} / {order.skydrop_service or 'servicio disponible'} - "
                        f"${amount:.2f} {order.shipping_quote_currency or 'MXN'}."
                    )
                    user_message = (
                        f"Dirección guardada. Tu envío quedó cotizado en ${amount:.2f} MXN. Revisa el total y continúa al pago."
                    )
                except SkydropError as exc:
                    amount = _apply_manual_shipping_quote(order, str(exc))
                    shipping_note = (
                        f"Skydrop no devolvió tarifa en tiempo real. "
                        f"Aplicamos envío estándar temporal por ${amount:.2f} MXN para no frenar tu compra."
                    )
                    user_message = (
                        f"Dirección guardada. Skydrop no pudo cotizar en tiempo real, así que aplicamos un envío estándar temporal de ${amount:.2f} MXN."
                    )
                    messages.warning(
                        request,
                        f"{user_message} Después podrás ajustar la guía desde administración si hace falta."
                    )
                else:
                    messages.success(request, user_message)
            else:
                amount = _apply_manual_shipping_quote(
                    order,
                    "Skydrop no está configurado. Se aplicó una tarifa fija temporal."
                )
                shipping_note = (
                    f"Skydrop no está configurado. Aplicamos envío estándar temporal por ${amount:.2f} MXN."
                )
                messages.info(
                    request,
                    f"Dirección guardada. Aplicamos un envío estándar temporal de ${amount:.2f} MXN para continuar con el pago."
                )

            order.shipping_updates.create(
                status_message=shipping_note
            )
            _track_meta_pixel_event(
                request,
                "AddPaymentInfo",
                {
                    "content_type": "product",
                    "num_items": sum(item.quantity for item in order.items.all()),
                    "value": float(order.total_price),
                    "currency": order.shipping_quote_currency or "MXN",
                },
                persist=True,
            )
            request.session['order_id'] = order.id
            return redirect('tienda:shipping_details')
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

    can_continue_to_payment = hasattr(order, "shipping_address") and bool(order.shipping_quote_amount)
    context = {
        'form': form,
        'order': order,
        'has_skydrop_credentials': _has_skydrop_credentials(),
        'can_continue_to_payment': can_continue_to_payment,
    }
    return render(request, 'tienda/shipping_details.html', context)

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

        variant = find_variant_for_selection(producto, talla=talla, color=color)
        available_stock = available_stock_for_selection(producto, talla=talla, color=color)
        if producto.uses_variant_inventory() and not variant:
            messages.error(request, "Esa combinación de talla y color todavía no está configurada en inventario.")
            return redirect('tienda:detalle_producto', producto_id=producto.id)
        if available_stock <= 0:
            messages.error(request, "Esa combinación ya no tiene stock disponible.")
            return redirect('tienda:detalle_producto', producto_id=producto.id)

        carrito = request.session.get('carrito', {})
        item_key = f"{producto_id}-{talla}-{color}-{diseño_pecho}-{diseño_espalda}"

        # Suma precio base + extra por cada diseño
        PRECIO_DISENO = 200
        precio_pecho = PRECIO_DISENO if diseño_pecho else 0
        precio_espalda = PRECIO_DISENO if diseño_espalda else 0
        precio_total = float(producto.precio) + precio_pecho + precio_espalda

        if item_key in carrito:
            if carrito[item_key]['cantidad'] + 1 > available_stock:
                messages.error(
                    request,
                    f"Solo quedan {available_stock} piezas disponibles para {producto.nombre} en {color} / {talla}."
                )
                return redirect('tienda:detalle_producto', producto_id=producto.id)
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
        _track_meta_pixel_event(
            request,
            "AddToCart",
            {
                "content_ids": [str(producto.id)],
                "content_name": producto.nombre,
                "content_type": "product",
                "value": float(precio_total),
                "currency": "MXN",
            },
            persist=True,
        )
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
    orders_list = _customer_visible_orders(request.user)
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
    if _has_skydrop_credentials() and not order.shipping_quote_amount:
        messages.error(request, "Primero necesitamos cotizar tu envío antes de enviarte a Stripe.")
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

    if order.shipping_quote_amount:
        shipping_amount_cents = int(order.shipping_quote_amount * 100)
        line_items.append({
            'price_data': {
                'currency': (order.shipping_quote_currency or 'mxn').lower(),
                'product_data': {
                    'name': f"Envío {order.skydrop_carrier or 'Skydrop'}",
                },
                'unit_amount': shipping_amount_cents,
            },
            'quantity': 1,
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
            variant = find_variant_for_selection(item.product, talla=item.talla, color=item.color)
            available_stock = variant.stock if variant else item.product.stock
            item_label = item.product.nombre if not variant else f"{item.product.nombre} ({item.color} / {item.talla})"
            if available_stock < item.quantity:
                stock_issues.append((item_label, available_stock, item.quantity))

        if stock_issues:
            for nombre, stock, requested_qty in stock_issues:
                messages.error(
                    request,
                    f"{nombre} ya no tiene stock suficiente para finalizar. Disponible: {stock}, solicitado: {requested_qty}."
                )
            return redirect("tienda:carrito")

        for item in order.items.select_related("product"):
            variant = find_variant_for_selection(item.product, talla=item.talla, color=item.color)
            record_inventory_movement(
                product=item.product,
                variant=variant,
                order=order,
                movement_type="sale",
                quantity_change=-int(item.quantity),
                note=f"Descuento automático al completar el pedido #{order.id}.",
                created_by=request.user,
                metadata={
                    "talla": item.talla,
                    "color": item.color,
                    "precio": str(item.price),
                },
            )

        order.status = "Completed"
        order.save(update_fields=["status"])

        subject = f"Pedido #{order.id} - Confirmación de Envío"
        message = (
            f"Hola {order.customer.username},\n\n"
            f"Tu pedido #{order.id} ha sido pagado y ya tenemos tu dirección de envío.\n"
            f"Subtotal de productos: ${order.subtotal_price:.2f} MXN.\n"
            f"Envío cotizado: ${order.shipping_total:.2f} MXN.\n"
            f"Total final: ${order.total_price:.2f} MXN.\n\n"
            "¡Gracias por comprar en Cult Calle!"
        )
        from_email = settings.DEFAULT_FROM_EMAIL
        recipient_list = [order.customer.email]
        send_mail(subject, message, from_email, recipient_list)

    request.session['carrito'] = {}
    request.session["last_completed_order_id"] = order.id
    _track_meta_pixel_event(
        request,
        "Purchase",
        {
            "content_ids": [str(item.product_id) for item in order.items.all()],
            "content_type": "product",
            "num_items": sum(item.quantity for item in order.items.all()),
            "value": float(order.total_price),
            "currency": order.shipping_quote_currency or "MXN",
        },
        persist=True,
    )
    return redirect('tienda:payment_success_done')


def payment_cancel(request):
    messages.warning(request, "El pago fue cancelado. Tu pedido sigue pendiente para que puedas retomarlo.")
    if request.session.get("order_id"):
        return redirect("tienda:shipping_details")
    return render(request, 'tienda/payment_cancel.html')


@login_required
def payment_success_done(request):
    order_id = request.session.get("last_completed_order_id")
    if not order_id:
        messages.warning(request, "No encontramos una compra reciente para mostrarte.")
        return redirect("tienda:my_orders")

    order = get_object_or_404(Order, id=order_id, customer=request.user)
    return render(
        request,
        "tienda/payment_success.html",
        {
            "order": order,
        },
    )
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
    orders = _customer_visible_orders(request.user)
    return render(request, 'tienda/tracking.html', {'orders': orders})


def _map_skydrop_status(raw_status):
    normalized = (raw_status or "").lower()
    if "deliver" in normalized:
        return "Delivered"
    if any(token in normalized for token in ["transit", "ship", "pickup", "label"]):
        return "Shipped"
    return "Processing"


def _webhook_secret_is_valid(request):
    expected = getattr(settings, "SKYDROP_WEBHOOK_SECRET", "")
    if not expected:
        return True

    candidates = [
        request.headers.get("X-Webhook-Secret", ""),
        request.headers.get("X-Skydrop-Secret", ""),
    ]
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        candidates.append(authorization.removeprefix("Bearer ").strip())

    return any(candidate == expected for candidate in candidates if candidate)


def _register_shipping_update(order, message):
    last_update = order.shipping_updates.order_by("-updated_at").first()
    if not last_update or last_update.status_message != message:
        order.shipping_updates.create(status_message=message)


def _apply_skydrop_sync(order, payload, source_label="Skydrop"):
    tracking_number = payload.get("tracking_number") or order.tracking_number
    tracking_url = payload.get("tracking_url") or order.skydrop_tracking_url
    carrier = payload.get("carrier") or order.skydrop_carrier
    service = payload.get("service") or order.skydrop_service
    raw_status = payload.get("status")
    shipment_id = payload.get("shipment_id") or order.skydrop_shipment_id

    previous_status = order.shipping_status
    previous_tracking = order.tracking_number

    order.tracking_number = tracking_number
    order.skydrop_tracking_url = tracking_url
    order.skydrop_carrier = carrier
    order.skydrop_service = service
    order.skydrop_shipment_id = shipment_id
    if raw_status:
        order.shipping_status = _map_skydrop_status(raw_status)
    order.skydrop_last_payload = payload.get("payload") or payload
    order.skydrop_last_error = ""
    order.save()

    changes = []
    if raw_status and order.shipping_status != previous_status:
        changes.append(f"estado: {order.shipping_status}")
    if tracking_number and tracking_number != previous_tracking:
        changes.append(f"tracking: {tracking_number}")
    if carrier or service:
        changes.append(f"servicio: {carrier or 'Skydrop'}{f' / {service}' if service else ''}")

    if changes:
        message = f"{source_label} actualizó " + ", ".join(changes) + "."
    else:
        message = f"{source_label} sincronizó el envío sin cambios nuevos visibles."
    _register_shipping_update(order, message)
    return order


@csrf_exempt
def skydrop_webhook(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)
    if not _webhook_secret_is_valid(request):
        return JsonResponse({"ok": False, "error": "invalid_secret"}, status=403)

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
    if order is None and data.get("relationships"):
        relation_data = data.get("relationships", {})
        quotation_id = relation_data.get("quotation", {}).get("data", {}).get("id") if isinstance(relation_data.get("quotation"), dict) else None
        if quotation_id:
            order = Order.objects.filter(skydrop_quotation_id=quotation_id).first()
    if order is None:
        return JsonResponse({"ok": True, "ignored": True})

    sync_payload = {
        "payload": payload,
        "shipment_id": shipment_id,
        "tracking_number": tracking_number,
        "tracking_url": attributes.get("tracking_url") or payload.get("tracking_url"),
        "status": raw_status,
        "carrier": attributes.get("provider_display_name") or payload.get("carrier"),
        "service": attributes.get("provider_service_name") or payload.get("service"),
    }
    _apply_skydrop_sync(order, sync_payload, source_label="Webhook Skydrop")
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

    _apply_skydrop_sync(order, payload, source_label="Sincronización manual")
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
