"""
Cliente mínimo de la API de Mercado Libre.

Docs:
  https://developers.mercadolibre.com.mx/es_ar/autenticacion-y-autorizacion
  https://developers.mercadolibre.com.mx/es_ar/gestiona-ventas
"""
import logging
from datetime import timedelta
from decimal import Decimal

import requests
from django.conf import settings
from django.utils import timezone

from .models import (
    MercadoLibreCredential,
    MercadoLibreListing,
    MercadoLibreOrder,
    MercadoLibreOrderItem,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.mercadolibre.com"
AUTH_BASE = "https://auth.mercadolibre.com.mx"  # cambia el dominio según el país


# ── OAuth ──────────────────────────────────────────────────────────────────

def get_auth_url(state=""):
    return (
        f"{AUTH_BASE}/authorization"
        f"?response_type=code"
        f"&client_id={settings.ML_APP_ID}"
        f"&redirect_uri={settings.ML_REDIRECT_URI}"
        + (f"&state={state}" if state else "")
    )


def exchange_code_for_token(code):
    r = requests.post(
        f"{API_BASE}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": settings.ML_APP_ID,
            "client_secret": settings.ML_APP_SECRET,
            "code": code,
            "redirect_uri": settings.ML_REDIRECT_URI,
        },
        headers={"Accept": "application/json"},
        timeout=15,
    )
    if not r.ok:
        # Devuelve el cuerpo real para diagnóstico
        body = r.text[:500]
        logger.error("ML token exchange %s: %s", r.status_code, body)
        raise requests.HTTPError(f"{r.status_code} — {body}", response=r)
    return r.json()


def refresh_token(cred):
    r = requests.post(
        f"{API_BASE}/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": settings.ML_APP_ID,
            "client_secret": settings.ML_APP_SECRET,
            "refresh_token": cred.refresh_token,
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    cred.access_token = data["access_token"]
    cred.refresh_token = data["refresh_token"]
    cred.expires_at = timezone.now() + timedelta(seconds=data["expires_in"])
    cred.save(update_fields=["access_token", "refresh_token", "expires_at", "updated_at"])
    return cred


def _ensure_fresh(cred):
    if cred.is_expired():
        cred = refresh_token(cred)
    return cred


def _headers(cred):
    return {"Authorization": f"Bearer {cred.access_token}"}


# ── Endpoints ──────────────────────────────────────────────────────────────

def fetch_me(cred):
    cred = _ensure_fresh(cred)
    r = requests.get(f"{API_BASE}/users/me", headers=_headers(cred), timeout=10)
    r.raise_for_status()
    return r.json()


def sync_orders(cred, limit=50):
    """Trae los pedidos recientes del vendedor y los guarda en la BD.
    Para pedidos NUEVOS y pagados, descuenta stock local de productos enlazados."""
    cred = _ensure_fresh(cred)
    r = requests.get(
        f"{API_BASE}/orders/search",
        params={"seller": cred.user_id, "limit": limit, "sort": "date_desc"},
        headers=_headers(cred),
        timeout=15,
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    saved = 0
    for o in results:
        fee, ship, net = _extract_costs(o)
        order, _ = MercadoLibreOrder.objects.update_or_create(
            ml_id=o["id"],
            defaults={
                "status": o.get("status", ""),
                "date_created": o.get("date_created"),
                "date_closed": o.get("date_closed") or None,
                "total_amount": o.get("total_amount") or 0,
                "currency_id": o.get("currency_id", "MXN"),
                "buyer_nickname": (o.get("buyer") or {}).get("nickname", ""),
                "buyer_id": (o.get("buyer") or {}).get("id"),
                "shipping_status": (o.get("shipping") or {}).get("status", ""),
                "shipping_id": (o.get("shipping") or {}).get("id") or None,
                "marketplace_fee": fee,
                "shipping_cost": ship,
                "net_received_amount": net,
                "raw": o,
            },
        )
        order.items.all().delete()
        items_for_stock = []
        for it in o.get("order_items", []):
            item = it.get("item") or {}
            MercadoLibreOrderItem.objects.create(
                order=order,
                item_id=item.get("id", "") or "",
                title=item.get("title", "") or "",
                quantity=it.get("quantity", 1) or 1,
                unit_price=it.get("unit_price") or 0,
            )
            items_for_stock.append((item.get("id"), it.get("quantity", 1) or 1))
        _apply_stock_transition(order, items_for_stock, o.get("status", ""))
        saved += 1
    return saved


def sync_listings(cred, page_size=50):
    """Trae las publicaciones del vendedor."""
    cred = _ensure_fresh(cred)
    r = requests.get(
        f"{API_BASE}/users/{cred.user_id}/items/search",
        params={"limit": page_size},
        headers=_headers(cred),
        timeout=15,
    )
    r.raise_for_status()
    ids = r.json().get("results", [])
    saved = 0
    for i in range(0, len(ids), 20):
        batch = ids[i:i + 20]
        rr = requests.get(
            f"{API_BASE}/items",
            params={"ids": ",".join(batch)},
            headers=_headers(cred),
            timeout=15,
        )
        rr.raise_for_status()
        for entry in rr.json():
            if entry.get("code") != 200:
                continue
            b = entry["body"]
            MercadoLibreListing.objects.update_or_create(
                ml_id=b["id"],
                defaults={
                    "title": b.get("title", ""),
                    "price": b.get("price") or 0,
                    "currency_id": b.get("currency_id", "MXN"),
                    "available_quantity": b.get("available_quantity", 0),
                    "sold_quantity": b.get("sold_quantity", 0),
                    "status": b.get("status", ""),
                    "permalink": b.get("permalink", "") or "",
                    "thumbnail": b.get("thumbnail", "") or "",
                    "listing_type_id": b.get("listing_type_id", "") or "",
                    "raw": b,
                },
            )
            saved += 1
    return saved


def publish_product_to_ml(cred, producto, category_id=None, listing_type=None):
    """
    Publica un Producto local como item nuevo en Mercado Libre.
    Devuelve el MercadoLibreListing creado (con producto enlazado).
    """
    from django.conf import settings as _settings
    cred = _ensure_fresh(cred)

    category_id = category_id or getattr(_settings, "ML_DEFAULT_CATEGORY_ID", "MLM173159")
    listing_type = listing_type or getattr(_settings, "ML_DEFAULT_LISTING_TYPE", "gold_special")
    site_url = getattr(_settings, "SITE_URL", "https://cultclassics.shop").rstrip("/")

    # Imágenes — Producto.imagen (FileField) + cualquier ImageMedia relacionado
    pictures = []
    if getattr(producto, "imagen", None):
        try:
            url = producto.imagen.url
            if not url.startswith("http"):
                url = f"{site_url}{url}"
            pictures.append({"source": url})
        except Exception:
            pass

    payload = {
        "title": (producto.nombre or "Producto")[:60],
        "category_id": category_id,
        "price": float(producto.precio or 0),
        "currency_id": "MXN",
        "available_quantity": int(getattr(producto, "stock", 0) or 0),
        "buying_mode": "buy_it_now",
        "listing_type_id": listing_type,
        "condition": "new",
        "description": {"plain_text": (getattr(producto, "descripcion", "") or producto.nombre or "")[:50000]},
        "pictures": pictures or [{"source": f"{site_url}/static/images/logo.png"}],
    }

    r = requests.post(
        f"{API_BASE}/items",
        headers={**_headers(cred), "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if not r.ok:
        body = r.text[:500]
        logger.error("ML publish_product %s: %s", r.status_code, body)
        raise requests.HTTPError(f"{r.status_code} — {body}", response=r)
    data = r.json()

    listing, _ = MercadoLibreListing.objects.update_or_create(
        ml_id=data["id"],
        defaults={
            "producto": producto,
            "title": data.get("title", ""),
            "price": data.get("price", 0),
            "currency_id": data.get("currency_id", "MXN"),
            "available_quantity": data.get("available_quantity", 0),
            "sold_quantity": data.get("sold_quantity", 0),
            "status": data.get("status", ""),
            "permalink": data.get("permalink", "") or "",
            "thumbnail": data.get("thumbnail", "") or "",
            "listing_type_id": data.get("listing_type_id", "") or "",
            "last_pushed_stock": data.get("available_quantity", 0),
            "raw": data,
        },
    )
    return listing


def push_tracking_to_ml(cred, shipment_id, tracking_number, carrier=""):
    """
    Envía el número de guía a Mercado Envíos para que el comprador lo vea.
    Solo aplica a pedidos que NO van por Mercado Envíos full (es decir,
    cuando el seller hace su propio envío).
    """
    cred = _ensure_fresh(cred)
    payload = {"tracking_number": tracking_number}
    if carrier:
        payload["service_id"] = carrier  # ML expone una lista de carriers, esto es flexible
    r = requests.put(
        f"{API_BASE}/shipments/{shipment_id}",
        headers=_headers(cred),
        json=payload,
        timeout=15,
    )
    if not r.ok:
        body = r.text[:300]
        logger.error("ML push tracking %s: %s", r.status_code, body)
        raise requests.HTTPError(f"{r.status_code} — {body}", response=r)
    return r.json() if r.text else {"ok": True}


def _extract_combo_value(combos, *aliases):
    """Devuelve el value_name del primer attribute_combination cuyo id/name calce."""
    aliases = [a.lower() for a in aliases]
    for c in combos or []:
        cid = (c.get("id") or "").lower()
        cname = (c.get("name") or "").lower()
        if cid in aliases or any(a in cname for a in aliases):
            return c.get("value_name")
    return None


def update_listing_stock(cred, ml_id, qty_or_producto):
    """
    Actualiza stock de una publicación. Acepta:
      - int (flat): publica con available_quantity plano.
      - Producto: si la publicación tiene variations[], mapea cada variación
        ML a un ProductVariant local por color+talla y publica por variación.
    """
    cred = _ensure_fresh(cred)
    listing = MercadoLibreListing.objects.filter(ml_id=ml_id).first()
    raw = (listing.raw if listing else {}) or {}
    variations = raw.get("variations") or []

    # Si recibimos un Producto Y la publicación tiene variaciones, vamos por variación
    if variations and hasattr(qty_or_producto, "pk") and not isinstance(qty_or_producto, int):
        from tienda.models import ProductVariant
        producto = qty_or_producto
        payload_variations = []
        matched_total = 0
        for v in variations:
            combos = v.get("attribute_combinations", [])
            color_val = _extract_combo_value(combos, "color")
            size_val = _extract_combo_value(combos, "size", "tama", "talla")
            qs = ProductVariant.objects.filter(product=producto, activo=True)
            if color_val:
                qs = qs.filter(color__iexact=color_val)
            if size_val:
                qs = qs.filter(talla__iexact=size_val)
            local_v = qs.first()
            if local_v is None:
                continue
            payload_variations.append({
                "id": v["id"],
                "available_quantity": int(local_v.stock or 0),
            })
            matched_total += int(local_v.stock or 0)
        if not payload_variations:
            logger.warning("No hay match local para variaciones de %s — no se actualiza", ml_id)
            return None
        total_to_persist = matched_total
        # Estrategia: PUT individual por variación (no re-valida pictures globales)
        errors = []
        for pv in payload_variations:
            rv = requests.put(
                f"{API_BASE}/items/{ml_id}/variations/{pv['id']}",
                headers=_headers(cred),
                json={"available_quantity": pv["available_quantity"]},
                timeout=15,
            )
            if not rv.ok:
                errors.append(f"var {pv['id']}: {rv.status_code} {rv.text[:120]}")
        if errors:
            logger.error("ML update variations partial fails: %s", " | ".join(errors[:3]))
            raise requests.HTTPError(" | ".join(errors[:3]))
        if listing:
            listing.available_quantity = total_to_persist
            listing.last_pushed_stock = total_to_persist
            listing.save(update_fields=["available_quantity", "last_pushed_stock", "synced_at"])
        return {"ok": True, "variations_updated": len(payload_variations)}
    else:
        # Flat
        qty = int(qty_or_producto.stock) if hasattr(qty_or_producto, "stock") else int(qty_or_producto)
        total_to_persist = qty
        r = requests.put(
            f"{API_BASE}/items/{ml_id}",
            headers=_headers(cred),
            json={"available_quantity": qty},
            timeout=15,
        )

    if not r.ok:
        logger.error("ML update_listing_stock %s: %s", r.status_code, r.text[:300])
        raise requests.HTTPError(f"{r.status_code} — {r.text[:300]}", response=r)
    if listing:
        listing.available_quantity = total_to_persist
        listing.last_pushed_stock = total_to_persist
        listing.save(update_fields=["available_quantity", "last_pushed_stock", "synced_at"])
    return r.json() if r.text else {"ok": True}


def sync_single_order(cred, order_id):
    """Trae UN pedido específico de ML por ID y lo guarda."""
    cred = _ensure_fresh(cred)
    r = requests.get(
        f"{API_BASE}/orders/{order_id}",
        headers=_headers(cred),
        timeout=15,
    )
    r.raise_for_status()
    o = r.json()
    was_new = not MercadoLibreOrder.objects.filter(ml_id=o["id"]).exists()
    fee, ship, net = _extract_costs(o)
    order, _ = MercadoLibreOrder.objects.update_or_create(
        ml_id=o["id"],
        defaults={
            "status": o.get("status", ""),
            "date_created": o.get("date_created"),
            "date_closed": o.get("date_closed") or None,
            "total_amount": o.get("total_amount") or 0,
            "currency_id": o.get("currency_id", "MXN"),
            "buyer_nickname": (o.get("buyer") or {}).get("nickname", ""),
            "buyer_id": (o.get("buyer") or {}).get("id"),
            "shipping_status": (o.get("shipping") or {}).get("status", ""),
            "shipping_id": (o.get("shipping") or {}).get("id") or None,
            "marketplace_fee": fee,
            "shipping_cost": ship,
            "net_received_amount": net,
            "raw": o,
        },
    )
    order.items.all().delete()
    items_for_stock = []
    for it in o.get("order_items", []):
        item = it.get("item") or {}
        MercadoLibreOrderItem.objects.create(
            order=order,
            item_id=item.get("id", "") or "",
            title=item.get("title", "") or "",
            quantity=it.get("quantity", 1) or 1,
            unit_price=it.get("unit_price") or 0,
        )
        items_for_stock.append((item.get("id"), it.get("quantity", 1) or 1))
    _apply_stock_transition(order, items_for_stock, o.get("status", ""))
    return order, was_new


def sync_single_listing(cred, ml_id):
    """Trae UNA publicación específica de ML por ID."""
    cred = _ensure_fresh(cred)
    r = requests.get(
        f"{API_BASE}/items/{ml_id}",
        headers=_headers(cred),
        timeout=15,
    )
    r.raise_for_status()
    b = r.json()
    listing, _ = MercadoLibreListing.objects.update_or_create(
        ml_id=b["id"],
        defaults={
            "title": b.get("title", ""),
            "price": b.get("price") or 0,
            "currency_id": b.get("currency_id", "MXN"),
            "available_quantity": b.get("available_quantity", 0),
            "sold_quantity": b.get("sold_quantity", 0),
            "status": b.get("status", ""),
            "permalink": b.get("permalink", "") or "",
            "thumbnail": b.get("thumbnail", "") or "",
            "listing_type_id": b.get("listing_type_id", "") or "",
            "raw": b,
        },
    )
    return listing


def _adjust_local_stock_for_ml_item(ml_item_id, quantity_delta, note):
    """
    Ajusta stock del Producto local enlazado.
    quantity_delta negativo = baja stock; positivo = sube stock (revert por cancelación).
    """
    if not ml_item_id or quantity_delta == 0:
        return
    listing = MercadoLibreListing.objects.filter(ml_id=ml_item_id, producto__isnull=False).first()
    if not listing or not listing.producto:
        return
    try:
        from tienda.models import record_inventory_movement
        record_inventory_movement(
            product=listing.producto,
            variant=None,
            order=None,
            movement_type="sale" if quantity_delta < 0 else "adjustment",
            quantity_change=int(quantity_delta),
            note=note,
        )
    except Exception as exc:
        logger.exception("Failed to adjust local stock for ML item %s: %s", ml_item_id, exc)


def _decrement_local_stock_for_ml_item(ml_item_id, quantity):
    """Atajo retrocompatible: descuenta `quantity` unidades."""
    _adjust_local_stock_for_ml_item(
        ml_item_id, -int(quantity),
        f"Venta en Mercado Libre (item {ml_item_id})",
    )


def _extract_costs(order_payload):
    """Extrae fees y costos de payments[] del payload de orden."""
    fee = Decimal("0.00")
    ship = Decimal("0.00")
    net = Decimal("0.00")
    for p in order_payload.get("payments", []) or []:
        fee += Decimal(str(p.get("marketplace_fee") or 0))
        ship += Decimal(str(p.get("shipping_cost") or 0))
        if p.get("net_received_amount") is not None:
            net += Decimal(str(p["net_received_amount"]))
        else:
            net += Decimal(str(p.get("transaction_amount") or 0)) - Decimal(str(p.get("marketplace_fee") or 0))
    if net == 0 and order_payload.get("total_amount"):
        net = Decimal(str(order_payload["total_amount"])) - fee
    return fee, ship, net


def _apply_stock_transition(order, items_data, new_status):
    """
    Decide si descontar o revertir stock según el cambio de estado.
    - Si pasa a (paid/confirmed) y aún no se decrementó → descuenta y marca True.
    - Si pasa a cancelled y antes se había decrementado → revierte y marca False.
    """
    VALID = ("paid", "confirmed")
    is_valid = new_status in VALID
    is_cancelled = new_status == "cancelled"
    if is_valid and not order.stock_decremented:
        for item_id, qty in items_data:
            _adjust_local_stock_for_ml_item(
                item_id, -int(qty),
                f"Venta en Mercado Libre (pedido {order.ml_id})",
            )
        order.stock_decremented = True
        order.save(update_fields=["stock_decremented"])
    elif is_cancelled and order.stock_decremented:
        for item_id, qty in items_data:
            _adjust_local_stock_for_ml_item(
                item_id, int(qty),
                f"Reversión por cancelación en ML (pedido {order.ml_id})",
            )
        order.stock_decremented = False
        order.save(update_fields=["stock_decremented"])
