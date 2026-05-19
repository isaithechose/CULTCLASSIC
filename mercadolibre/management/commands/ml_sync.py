"""
Sincroniza pedidos y publicaciones de todas las credenciales conectadas.
Pensado para correr desde cron como backup en caso de que algún webhook falle.

Uso:
    python manage.py ml_sync             # sync de todo
    python manage.py ml_sync --orders    # solo pedidos
    python manage.py ml_sync --listings  # solo publicaciones
    python manage.py ml_sync --quiet     # sin output (útil en cron)
"""
import logging
import sys

from django.core.management.base import BaseCommand

from mercadolibre import api
from mercadolibre.models import MercadoLibreCredential

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sincroniza pedidos y publicaciones de todas las credenciales activas de Mercado Libre."

    def add_arguments(self, parser):
        parser.add_argument("--orders", action="store_true", help="Solo pedidos.")
        parser.add_argument("--listings", action="store_true", help="Solo publicaciones.")
        parser.add_argument("--quiet", action="store_true", help="Sin output a stdout.")

    def handle(self, *args, **options):
        do_orders = options["orders"] or not options["listings"]
        do_listings = options["listings"] or not options["orders"]
        quiet = options["quiet"]

        creds = list(MercadoLibreCredential.objects.all())
        if not creds:
            if not quiet:
                self.stdout.write(self.style.WARNING("No hay credenciales conectadas. Salgo."))
            return

        total_orders = 0
        total_listings = 0
        for cred in creds:
            try:
                if do_orders:
                    n = api.sync_orders(cred)
                    total_orders += n
                    if not quiet:
                        self.stdout.write(f"  {cred.nickname or cred.user_id}: {n} pedidos")
                if do_listings:
                    n = api.sync_listings(cred)
                    total_listings += n
                    if not quiet:
                        self.stdout.write(f"  {cred.nickname or cred.user_id}: {n} publicaciones")
            except Exception as exc:
                logger.exception("Sync ML falló para %s", cred.user_id)
                if not quiet:
                    self.stderr.write(self.style.ERROR(
                        f"  {cred.nickname or cred.user_id}: ERROR {exc}"
                    ))
                # No abortamos: seguimos con la siguiente credencial.

        if not quiet:
            self.stdout.write(self.style.SUCCESS(
                f"Sync OK · {total_orders} pedidos · {total_listings} publicaciones"
            ))
