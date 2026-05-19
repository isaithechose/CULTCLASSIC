"""OAuth flow + sync + webhook para Mercado Libre."""
import json
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import api
from .models import MercadoLibreCredential

logger = logging.getLogger(__name__)


@staff_member_required
def connect(request):
    """Redirige al usuario al diálogo de autorización de ML."""
    return redirect(api.get_auth_url(state=str(request.user.pk)))


@staff_member_required
def sync_now(request):
    """Sincroniza pedidos y publicaciones de la primera credencial activa."""
    cred = MercadoLibreCredential.objects.first()
    if not cred:
        messages.error(request, "No hay cuenta de Mercado Libre conectada.")
        return redirect("admin:index")
    try:
        orders = api.sync_orders(cred)
        listings = api.sync_listings(cred)
        messages.success(
            request,
            f"Mercado Libre: {orders} pedidos y {listings} publicaciones sincronizados.",
        )
    except Exception as exc:
        logger.exception("ML sync failed")
        messages.error(request, f"Error al sincronizar con ML: {exc}")
    return redirect(request.META.get("HTTP_REFERER") or "admin:index")


@staff_member_required
def callback(request):
    """Callback de OAuth: intercambia el code por token y guarda credenciales."""
    code = request.GET.get("code")
    err = request.GET.get("error")
    if err:
        messages.error(request, f"Mercado Libre rechazó la conexión: {err}")
        return redirect("admin:index")
    if not code:
        return HttpResponseBadRequest("Falta el parámetro 'code'.")

    try:
        token = api.exchange_code_for_token(code)
    except Exception as exc:
        logger.exception("ML token exchange failed")
        messages.error(request, f"No pude intercambiar el código: {exc}")
        return redirect("admin:index")

    cred, _ = MercadoLibreCredential.objects.update_or_create(
        user_id=token["user_id"],
        defaults={
            "access_token": token["access_token"],
            "refresh_token": token["refresh_token"],
            "expires_at": timezone.now() + timedelta(seconds=token["expires_in"]),
        },
    )

    try:
        me = api.fetch_me(cred)
        cred.nickname = me.get("nickname", "") or ""
        cred.site_id = me.get("site_id", "MLM")
        cred.save(update_fields=["nickname", "site_id", "updated_at"])
    except Exception:
        logger.warning("No pude obtener /users/me después de conectar.")

    messages.success(
        request,
        f"Conectado a Mercado Libre como {cred.nickname or cred.user_id}."
    )
    return redirect("admin:mercadolibre_mercadolibrecredential_changelist")


@csrf_exempt
@require_POST
def webhook(request):
    """
    Endpoint para notificaciones en tiempo real de Mercado Libre.
    ML envía POST JSON con {topic, resource, user_id, ...}.
    Debe responder 2xx en <500ms; el procesamiento puede fallar silencioso.
    """
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

    topic = payload.get("topic", "")
    resource = (payload.get("resource") or "").strip("/")
    user_id = payload.get("user_id")
    logger.info("ML webhook topic=%s resource=%s user=%s", topic, resource, user_id)

    cred = MercadoLibreCredential.objects.filter(user_id=user_id).first()
    if not cred:
        # No es de un usuario que tengamos conectado — ignoramos pero confirmamos
        return JsonResponse({"ok": True, "note": "user not tracked"})

    try:
        parts = resource.split("/")
        last_id = parts[-1] if parts else ""

        if topic in ("orders_v2", "orders") and last_id:
            api.sync_single_order(cred, last_id)
        elif topic == "items" and last_id:
            api.sync_single_listing(cred, last_id)
        elif topic in ("stock-location", "stock_location"):
            # /user-products/{id}/stock — resync de la primera publicación encontrada
            # Por simplicidad, no procesamos detalle; se actualizará en próximo sync.
            pass
        else:
            logger.debug("ML webhook topic no manejado: %s", topic)
    except Exception:
        logger.exception("Webhook handler failed (topic=%s)", topic)
        # Devolvemos 200 igual para que ML no reintente eternamente.

    return JsonResponse({"ok": True})
