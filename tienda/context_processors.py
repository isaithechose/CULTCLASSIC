from django.conf import settings


def instagram_feed(request):
    """
    Inyecta el feed ID de Behold y el username de Instagram en todos los templates.
    El feed se renderiza con el widget de Behold (script en base.html).
    """
    return {
        "behold_feed_id": getattr(settings, "BEHOLD_FEED_ID", ""),
        "instagram_username": getattr(settings, "INSTAGRAM_USERNAME", "cultclasiccs"),
    }


def meta_pixel(request):
    queued_events = request.session.pop("meta_pixel_events", [])
    inline_events = getattr(request, "_meta_pixel_events", [])
    return {
        "meta_pixel_id": getattr(settings, "META_PIXEL_ID", ""),
        "meta_pixel_events": queued_events + inline_events,
        "facebook_domain_verification": getattr(settings, "FACEBOOK_DOMAIN_VERIFICATION", ""),
    }


def admin_nav_context(request):
    if not request.path.startswith("/admin/"):
        return {}
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_staff:
        return {}

    from tienda.admin import _admin_overview_context

    try:
        return _admin_overview_context()
    except Exception:
        return {}
