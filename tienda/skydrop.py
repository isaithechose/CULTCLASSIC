from __future__ import annotations

from decimal import Decimal

import requests
from django.conf import settings


class SkydropError(Exception):
    pass


def is_skydrop_enabled() -> bool:
    return bool(
        getattr(settings, "SKYDROP_CLIENT_ID", "")
        and getattr(settings, "SKYDROP_CLIENT_SECRET", "")
    )


def _api_base_url() -> str:
    return getattr(settings, "SKYDROP_API_BASE_URL", "https://pro.skydropx.com").rstrip("/")


def _origin_payload() -> dict:
    return {
        "name": getattr(settings, "SKYDROP_ORIGIN_NAME", "Cult Classics"),
        "company": getattr(settings, "SKYDROP_ORIGIN_COMPANY", "Cult Classics"),
        "phone": getattr(settings, "SKYDROP_ORIGIN_PHONE", ""),
        "email": getattr(settings, "SKYDROP_ORIGIN_EMAIL", ""),
        "street1": getattr(settings, "SKYDROP_ORIGIN_STREET1", ""),
        "street2": getattr(settings, "SKYDROP_ORIGIN_STREET2", ""),
        "reference": getattr(settings, "SKYDROP_ORIGIN_REFERENCE", ""),
        "postal_code": getattr(settings, "SKYDROP_ORIGIN_POSTAL_CODE", ""),
        "area_level1": getattr(settings, "SKYDROP_ORIGIN_STATE", ""),
        "area_level2": getattr(settings, "SKYDROP_ORIGIN_CITY", ""),
        "area_level3": getattr(settings, "SKYDROP_ORIGIN_NEIGHBORHOOD", ""),
        "country_code": getattr(settings, "SKYDROP_ORIGIN_COUNTRY_CODE", "MX"),
    }


def _recipient_name(order) -> str:
    customer = order.customer
    if not customer:
        return "Cliente Cult Classics"
    full_name = f"{customer.first_name} {customer.last_name}".strip()
    return full_name or customer.username or customer.email or "Cliente Cult Classics"


def _recipient_email(order) -> str:
    customer = order.customer
    return getattr(customer, "email", "") or getattr(settings, "SKYDROP_DEFAULT_EMAIL", "")


def _recipient_phone(order) -> str:
    shipping_address = getattr(order, "shipping_address", None)
    if shipping_address and shipping_address.phone:
        return shipping_address.phone
    return getattr(settings, "SKYDROP_DEFAULT_PHONE", "")


def _destination_payload(order) -> dict:
    shipping_address = order.shipping_address
    return {
        "name": _recipient_name(order),
        "company": "Cliente Cult Classics",
        "phone": _recipient_phone(order),
        "email": _recipient_email(order),
        "street1": shipping_address.address_line1,
        "street2": shipping_address.address_line2 or "",
        "reference": shipping_address.address_line2 or "",
        "postal_code": shipping_address.postal_code,
        "area_level1": shipping_address.state,
        "area_level2": shipping_address.city,
        "area_level3": "",
        "country_code": "MX" if shipping_address.country.lower() in {"mexico", "méxico", "mx"} else shipping_address.country,
    }


def _parcel_payload(order) -> dict:
    total_quantity = sum(item.quantity for item in order.items.all()) or 1
    base_weight = getattr(settings, "SKYDROP_DEFAULT_PARCEL_WEIGHT", 0.8)
    return {
        "weight": round(float(base_weight) * total_quantity, 2),
        "length": getattr(settings, "SKYDROP_DEFAULT_PARCEL_LENGTH", 35),
        "width": getattr(settings, "SKYDROP_DEFAULT_PARCEL_WIDTH", 28),
        "height": getattr(settings, "SKYDROP_DEFAULT_PARCEL_HEIGHT", 6),
        "distance_unit": getattr(settings, "SKYDROP_DISTANCE_UNIT", "CM"),
        "mass_unit": getattr(settings, "SKYDROP_MASS_UNIT", "KG"),
    }


def _token() -> str:
    response = requests.post(
        f"{_api_base_url()}/api/v1/oauth/token",
        json={
            "grant_type": "client_credentials",
            "client_id": settings.SKYDROP_CLIENT_ID,
            "client_secret": settings.SKYDROP_CLIENT_SECRET,
        },
        timeout=20,
    )
    response.raise_for_status()
    access_token = response.json().get("access_token")
    if not access_token:
        raise SkydropError("Skydrop no devolvió access_token.")
    return access_token


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    token = _token()
    response = requests.request(
        method=method,
        url=f"{_api_base_url()}{path}",
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _extract_rates(payload: dict) -> list[dict]:
    candidates = []
    containers = []

    if isinstance(payload.get("included"), list):
        containers.extend(payload["included"])
    if isinstance(payload.get("data"), list):
        containers.extend(payload["data"])
    elif isinstance(payload.get("data"), dict):
        containers.append(payload["data"])

    for item in containers:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "rate":
            continue
        attributes = item.get("attributes", {})
        if attributes.get("success") is False:
            continue

        total = attributes.get("total") or attributes.get("amount") or attributes.get("cost") or "0"
        try:
            total_amount = Decimal(str(total))
        except Exception:
            total_amount = Decimal("0")

        candidates.append(
            {
                "id": item.get("id"),
                "amount": total_amount,
                "carrier": attributes.get("provider_display_name") or attributes.get("provider_name"),
                "service": attributes.get("provider_service_name") or attributes.get("provider_service_code"),
                "raw": item,
            }
        )

    return sorted(candidates, key=lambda rate: rate["amount"])


def quote_order(order) -> dict:
    if not is_skydrop_enabled():
        raise SkydropError("Faltan credenciales de Skydrop en settings.")
    if not hasattr(order, "shipping_address"):
        raise SkydropError("El pedido no tiene dirección de envío.")

    response = _request(
        "POST",
        "/api/v1/quotations",
        {
            "quotation": {
                "order_id": str(order.id),
                "address_from": _origin_payload(),
                "address_to": _destination_payload(order),
                "parcel": _parcel_payload(order),
            }
        },
    )
    rates = _extract_rates(response)
    if not rates:
        raise SkydropError("Skydrop no devolvió tarifas para este pedido.")

    best_rate = rates[0]
    return {
        "quotation_id": response.get("data", {}).get("id") if isinstance(response.get("data"), dict) else response.get("id"),
        "best_rate": best_rate,
        "rates": rates,
        "payload": response,
    }


def create_shipment(order, rate_id: str | None = None) -> dict:
    if not is_skydrop_enabled():
        raise SkydropError("Faltan credenciales de Skydrop en settings.")
    if not hasattr(order, "shipping_address"):
        raise SkydropError("El pedido no tiene dirección de envío.")

    if not rate_id:
        quotation = quote_order(order)
        rate_id = quotation["best_rate"]["id"]

    response = _request(
        "POST",
        "/api/v1/shipments",
        {
            "shipment": {
                "rate_id": rate_id,
                "printing_format": getattr(settings, "SKYDROP_PRINTING_FORMAT", "standard"),
                "address_from": _origin_payload(),
                "address_to": _destination_payload(order),
                "parcel": _parcel_payload(order),
            }
        },
    )
    data = response.get("data", {}) if isinstance(response.get("data"), dict) else response
    attributes = data.get("attributes", {}) if isinstance(data, dict) else {}
    relationships = data.get("relationships", {}) if isinstance(data, dict) else {}

    return {
        "shipment_id": data.get("id"),
        "tracking_number": attributes.get("master_tracking_number") or attributes.get("tracking_number"),
        "tracking_url": attributes.get("tracking_url"),
        "label_url": relationships.get("label_url") or attributes.get("label_url"),
        "carrier": attributes.get("provider_display_name") or attributes.get("carrier"),
        "service": attributes.get("provider_service_name") or attributes.get("service"),
        "payload": response,
    }


def sync_shipment(order) -> dict:
    if not order.skydrop_shipment_id:
        raise SkydropError("El pedido no tiene un shipment_id de Skydrop.")

    response = _request("GET", f"/api/v1/shipments/{order.skydrop_shipment_id}")
    data = response.get("data", {}) if isinstance(response.get("data"), dict) else response
    attributes = data.get("attributes", {}) if isinstance(data, dict) else {}
    return {
        "tracking_number": attributes.get("master_tracking_number") or attributes.get("tracking_number"),
        "tracking_url": attributes.get("tracking_url"),
        "status": attributes.get("status"),
        "carrier": attributes.get("provider_display_name") or attributes.get("carrier"),
        "service": attributes.get("provider_service_name") or attributes.get("service"),
        "payload": response,
    }
