from django.conf import settings


def meta_pixel(request):
    queued_events = request.session.pop("meta_pixel_events", [])
    inline_events = getattr(request, "_meta_pixel_events", [])
    return {
        "meta_pixel_id": getattr(settings, "META_PIXEL_ID", ""),
        "meta_pixel_events": queued_events + inline_events,
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
