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


def _clean_value(value) -> str:
    return str(value or "").strip()


def _normalize_country_code(value: str) -> str:
    normalized = _clean_value(value).lower()
    if normalized in {"mexico", "méxico", "mx", "mx."}:
        return "MX"
    return _clean_value(value).upper()


def _origin_payload() -> dict:
    return {
        "name": _clean_value(getattr(settings, "SKYDROP_ORIGIN_NAME", "Cult Classics")),
        "company": _clean_value(getattr(settings, "SKYDROP_ORIGIN_COMPANY", "Cult Classics")),
        "phone": _clean_value(getattr(settings, "SKYDROP_ORIGIN_PHONE", "")),
        "email": _clean_value(getattr(settings, "SKYDROP_ORIGIN_EMAIL", "")),
        "street1": _clean_value(getattr(settings, "SKYDROP_ORIGIN_STREET1", "")),
        "street2": _clean_value(getattr(settings, "SKYDROP_ORIGIN_STREET2", "")),
        "reference": _clean_value(getattr(settings, "SKYDROP_ORIGIN_REFERENCE", "")),
        "postal_code": _clean_value(getattr(settings, "SKYDROP_ORIGIN_POSTAL_CODE", "")),
        "area_level1": _clean_value(getattr(settings, "SKYDROP_ORIGIN_STATE", "")),
        "area_level2": _clean_value(getattr(settings, "SKYDROP_ORIGIN_CITY", "")),
        "area_level3": _clean_value(getattr(settings, "SKYDROP_ORIGIN_NEIGHBORHOOD", "")),
        "country_code": _normalize_country_code(getattr(settings, "SKYDROP_ORIGIN_COUNTRY_CODE", "MX")),
    }


def _recipient_name(order) -> str:
    customer = order.customer
    if not customer:
        return "Cliente Cult Classics"
    full_name = f"{customer.first_name} {customer.last_name}".strip()
    return full_name or customer.username or customer.email or "Cliente Cult Classics"


def _recipient_email(order) -> str:
    customer = order.customer
    return _clean_value(getattr(customer, "email", "")) or _clean_value(getattr(settings, "SKYDROP_DEFAULT_EMAIL", ""))


def _recipient_phone(order) -> str:
    shipping_address = getattr(order, "shipping_address", None)
    if shipping_address and shipping_address.phone:
        return _clean_value(shipping_address.phone)
    return _clean_value(getattr(settings, "SKYDROP_DEFAULT_PHONE", ""))


def _validate_payload(data: dict, label: str) -> None:
    required_fields = ("name", "phone", "street1", "postal_code", "area_level1", "area_level2", "country_code")
    missing = [field for field in required_fields if not _clean_value(data.get(field))]
    if missing:
        raise SkydropError(f"Faltan campos obligatorios en {label}: {', '.join(missing)}.")


def _destination_payload(order) -> dict:
    shipping_address = order.shipping_address
    area_level3 = (
        _clean_value(shipping_address.address_line2 or "")
        or _clean_value(shipping_address.city)
        or _clean_value(shipping_address.state)
    )
    payload = {
        "name": _recipient_name(order),
        "company": "Cliente Cult Classics",
        "phone": _recipient_phone(order),
        "email": _recipient_email(order),
        "street1": _clean_value(shipping_address.address_line1),
        "street2": _clean_value(shipping_address.address_line2 or ""),
        "reference": _clean_value(shipping_address.address_line2 or ""),
        "postal_code": _clean_value(shipping_address.postal_code),
        "area_level1": _clean_value(shipping_address.state),
        "area_level2": _clean_value(shipping_address.city),
        "area_level3": area_level3,
        "country_code": _normalize_country_code(shipping_address.country),
    }
    _validate_payload(payload, "la dirección de destino")
    return payload


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


def _validate_origin() -> dict:
    payload = _origin_payload()
    _validate_payload(payload, "la dirección de origen")
    return payload


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
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:500]
        raise SkydropError(f"Skydrop rechazó la autenticación: {detail}") from exc
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
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:800]
        raise SkydropError(f"Skydrop devolvió un error en {path}: {detail}") from exc
    return response.json()


def _extract_rates(payload: dict) -> list[dict]:
    candidates = []
    containers = []

    if isinstance(payload.get("included"), list):
        containers.extend(payload["included"])
    if isinstance(payload.get("rates"), list):
        containers.extend(payload["rates"])
    if isinstance(payload.get("meta"), dict) and isinstance(payload["meta"].get("rates"), list):
        containers.extend(payload["meta"]["rates"])
    if isinstance(payload.get("data"), list):
        containers.extend(payload["data"])
    elif isinstance(payload.get("data"), dict):
        containers.append(payload["data"])
        attributes = payload["data"].get("attributes", {})
        if isinstance(attributes, dict):
            if isinstance(attributes.get("rates"), list):
                containers.extend(attributes["rates"])
            if isinstance(attributes.get("available_rates"), list):
                containers.extend(attributes["available_rates"])

    for item in containers:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        attributes = item.get("attributes", {}) if isinstance(item.get("attributes"), dict) else {}
        if item_type not in (None, "rate") and not attributes:
            continue
        if attributes.get("success") is False:
            continue

        raw_id = item.get("id") or attributes.get("id") or item.get("rate_id")
        total = (
            attributes.get("total")
            or attributes.get("amount")
            or attributes.get("cost")
            or item.get("total")
            or item.get("amount")
            or item.get("cost")
            or "0"
        )
        try:
            total_amount = Decimal(str(total))
        except Exception:
            total_amount = Decimal("0")

        carrier = (
            attributes.get("provider_display_name")
            or attributes.get("provider_name")
            or attributes.get("carrier")
            or item.get("provider_display_name")
            or item.get("provider_name")
            or item.get("carrier")
        )
        service = (
            attributes.get("provider_service_name")
            or attributes.get("provider_service_code")
            or attributes.get("service")
            or item.get("provider_service_name")
            or item.get("provider_service_code")
            or item.get("service")
        )
        if not raw_id or total_amount <= 0:
            continue

        candidates.append(
            {
                "id": raw_id,
                "amount": total_amount,
                "carrier": carrier,
                "service": service,
                "raw": item,
            }
        )

    return sorted(candidates, key=lambda rate: rate["amount"])


def quote_order(order) -> dict:
    if not is_skydrop_enabled():
        raise SkydropError("Faltan credenciales de Skydrop en settings.")
    if not hasattr(order, "shipping_address"):
        raise SkydropError("El pedido no tiene dirección de envío.")

    origin_payload = _validate_origin()
    destination_payload = _destination_payload(order)

    response = _request(
        "POST",
        "/api/v1/quotations",
        {
            "quotation": {
                "order_id": str(order.id),
                "address_from": origin_payload,
                "address_to": destination_payload,
                "parcel": _parcel_payload(order),
            }
        },
    )
    rates = _extract_rates(response)
    if not rates:
        summary = []
        data = response.get("data")
        if isinstance(data, dict):
            attributes = data.get("attributes", {})
            if isinstance(attributes, dict):
                for key in ("error", "errors", "message", "messages", "warnings"):
                    value = attributes.get(key)
                    if value:
                        summary.append(f"{key}: {value}")
        for key in ("error", "errors", "message", "messages"):
            value = response.get(key)
            if value:
                summary.append(f"{key}: {value}")
        detail = f" Detalle: {' | '.join(map(str, summary))}" if summary else ""
        raise SkydropError(f"Skydrop no devolvió tarifas para este pedido.{detail}")

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

    origin_payload = _validate_origin()
    destination_payload = _destination_payload(order)

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
                "address_from": origin_payload,
                "address_to": destination_payload,
                "parcel": _parcel_payload(order),
            }
        },
    )
    data = response.get("data", {}) if isinstance(response.get("data"), dict) else response
    attributes = data.get("attributes", {}) if isinstance(data, dict) else {}
    relationships = data.get("relationships", {}) if isinstance(data, dict) else {}
    label_url = (
        attributes.get("label_url")
        or attributes.get("labelDownloadUrl")
        or attributes.get("printable_label_url")
        or relationships.get("label_url")
    )

    return {
        "shipment_id": data.get("id"),
        "tracking_number": attributes.get("master_tracking_number") or attributes.get("tracking_number"),
        "tracking_url": attributes.get("tracking_url"),
        "label_url": label_url,
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
