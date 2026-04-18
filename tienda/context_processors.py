from django.conf import settings


def meta_pixel(request):
    queued_events = request.session.pop("meta_pixel_events", [])
    inline_events = getattr(request, "_meta_pixel_events", [])
    return {
        "meta_pixel_id": getattr(settings, "META_PIXEL_ID", ""),
        "meta_pixel_events": queued_events + inline_events,
    }
