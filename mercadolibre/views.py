"""OAuth flow para conectar la cuenta de Mercado Libre."""
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils import timezone

from . import api
from .models import MercadoLibreCredential

logger = logging.getLogger(__name__)


@staff_member_required
def connect(request):
    """Redirige al usuario al diálogo de autorización de ML."""
    return redirect(api.get_auth_url(state=str(request.user.pk)))


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
