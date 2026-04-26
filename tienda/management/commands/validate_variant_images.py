from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from tienda.models import ProductVariant
from tienda.utils.variant_image_assignment import find_best_image_for_variant


class Command(BaseCommand):
    help = "Valida variantes activas sin imagen compatible o con archivo faltante."

    def add_arguments(self, parser):
        parser.add_argument("--product-id", type=int, help="Limita la validación a un producto.")

    def handle(self, *args, **options):
        queryset = ProductVariant.objects.select_related("product").filter(activo=True)
        if options.get("product_id"):
            queryset = queryset.filter(product_id=options["product_id"])

        media_root = Path(settings.MEDIA_ROOT)
        missing_match = []
        missing_file = []

        for variant in queryset.order_by("product__nombre", "color", "talla"):
            image_name = variant.imagen.name or find_best_image_for_variant(variant)
            if not image_name:
                missing_match.append(str(variant))
                continue
            if not (media_root / image_name).exists():
                missing_file.append(f"{variant}: {image_name}")

        if missing_match:
            self.stdout.write(self.style.WARNING("Variantes sin imagen compatible:"))
            for item in missing_match:
                self.stdout.write(f"  - {item}")

        if missing_file:
            self.stdout.write(self.style.WARNING("Variantes con archivo faltante:"))
            for item in missing_file:
                self.stdout.write(f"  - {item}")

        if missing_match or missing_file:
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(f"OK. {queryset.count()} variantes activas tienen imagen válida."))
