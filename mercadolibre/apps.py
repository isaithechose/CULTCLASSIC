from django.apps import AppConfig


class MercadolibreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mercadolibre'
    verbose_name = "Mercado Libre"

    def ready(self):
        from . import signals  # noqa: F401
