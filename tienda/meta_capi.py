"""
Cliente Meta Conversions API (CAPI) server-side.

Envia los mismos eventos del pixel browser pero desde el servidor, con un
event_id compartido para que Meta deduplique correctamente.

Uso desde una view:
    from .meta_capi import track_capi_event
    event_id = track_capi_event(request, "Purchase", {
        "value": 599.00, "currency": "MXN", "content_ids": ["12"],
    })
    # event_id luego se pasa al pixel browser para dedupe.

Config en settings/.env:
    META_PIXEL_ID=2055638251973934
    META_PIXEL_ACCESS_TOKEN=EAA...
    META_PIXEL_TEST_EVENT_CODE=TEST38956   # opcional, solo para pruebas
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from urllib import request as urllib_request, error as urllib_error

from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"


def _sha256_norm(value):
    """Hash SHA-256 (lowercase, trimmed) que Meta exige para PII."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def build_user_data(request, user=None):
    """Construye el bloque user_data con info disponible del request + user."""
    data = {
        "client_user_agent": request.META.get("HTTP_USER_AGENT", "")[:512],
    }
    ip = _client_ip(request)
    if ip:
        data["client_ip_address"] = ip

    # _fbp y _fbc cookies (las setea el pixel browser y FB ads)
    fbp = request.COOKIES.get("_fbp")
    if fbp:
        data["fbp"] = fbp
    fbc = request.COOKIES.get("_fbc")
    if fbc:
        data["fbc"] = fbc

    # Usuario logueado: email + nombre hasheados
    if user is None:
        user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        em = _sha256_norm(getattr(user, "email", ""))
        if em:
            data["em"] = em
        fn = _sha256_norm(getattr(user, "first_name", ""))
        if fn:
            data["fn"] = fn
        ln = _sha256_norm(getattr(user, "last_name", ""))
        if ln:
            data["ln"] = ln
        external_id = _sha256_norm(getattr(user, "pk", ""))
        if external_id:
            data["external_id"] = external_id

    return data


def _post_async(url, payload, event_name):
    body = json.dumps(payload).encode("utf-8")

    def _do():
        try:
            req = urllib_request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=8) as resp:
                response_body = resp.read().decode("utf-8", errors="ignore")
            logger.info("CAPI %s OK: %s", event_name, response_body[:200])
        except urllib_error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.warning("CAPI %s HTTP %s: %s", event_name, e.code, err_body[:400])
        except Exception as e:
            logger.warning("CAPI %s error: %s", event_name, e)

    # Fire-and-forget. Daemon thread no bloquea la response al cliente.
    threading.Thread(target=_do, daemon=True, name=f"capi-{event_name}").start()


def send_capi_event(
    event_name,
    event_id,
    user_data,
    custom_data=None,
    event_source_url=None,
    test_event_code=None,
):
    """Envía un evento al endpoint Conversions API. Async (no bloquea)."""
    pixel_id = getattr(settings, "META_PIXEL_ID", "") or ""
    token = getattr(settings, "META_PIXEL_ACCESS_TOKEN", "") or ""
    if not pixel_id or not token:
        return False

    event = {
        "event_name": event_name,
        "event_time": int(time.time()),
        "event_id": event_id,
        "action_source": "website",
        "user_data": user_data or {},
    }
    if event_source_url:
        event["event_source_url"] = event_source_url
    if custom_data:
        event["custom_data"] = custom_data

    payload = {"data": [event]}
    if test_event_code:
        payload["test_event_code"] = test_event_code

    url = (
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/"
        f"{pixel_id}/events?access_token={token}"
    )
    _post_async(url, payload, event_name)
    return True


def track_capi_event(request, event_name, custom_data=None, event_id=None, user=None):
    """Helper de alto nivel. Devuelve el event_id para usar en el pixel browser."""
    if event_id is None:
        event_id = str(uuid.uuid4())

    user_data = build_user_data(request, user=user)
    event_source_url = request.build_absolute_uri() if hasattr(request, "build_absolute_uri") else None
    test_code = getattr(settings, "META_PIXEL_TEST_EVENT_CODE", "") or None

    send_capi_event(
        event_name=event_name,
        event_id=event_id,
        user_data=user_data,
        custom_data=custom_data,
        event_source_url=event_source_url,
        test_event_code=test_code,
    )
    return event_id
