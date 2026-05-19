"""
Cliente mínimo de la API de Mercado Libre.

Docs:
  https://developers.mercadolibre.com.mx/es_ar/autenticacion-y-autorizacion
  https://developers.mercadolibre.com.mx/es_ar/gestiona-ventas
"""
import logging
from datetime import timedelta

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
    """Trae los pedidos recientes del vendedor y los guarda en la BD."""
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
                "raw": o,
            },
        )
        order.items.all().delete()
        for it in o.get("order_items", []):
            item = it.get("item") or {}
            MercadoLibreOrderItem.objects.create(
                order=order,
                item_id=item.get("id", "") or "",
                title=item.get("title", "") or "",
                quantity=it.get("quantity", 1) or 1,
                unit_price=it.get("unit_price") or 0,
            )
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


def update_listing_stock(cred, ml_id, available_quantity):
    """Actualiza el stock de una publicación en ML."""
    cred = _ensure_fresh(cred)
    r = requests.put(
        f"{API_BASE}/items/{ml_id}",
        headers=_headers(cred),
        json={"available_quantity": int(available_quantity)},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()
