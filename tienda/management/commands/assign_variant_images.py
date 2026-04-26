from django.core.management.base import BaseCommand

from tienda.models import ProductVariant
from tienda.utils.variant_image_assignment import find_best_image_for_variant


class Command(BaseCommand):
    help = "Asigna automáticamente la imagen correcta a variantes según producto/color."

    def add_arguments(self, parser):
        parser.add_argument("--product-id", type=int, help="Limita la asignación a un producto.")
        parser.add_argument("--dry-run", action="store_true", help="Solo muestra cambios.")

    def handle(self, *args, **options):
        queryset = ProductVariant.objects.select_related("product").filter(activo=True)
        if options.get("product_id"):
            queryset = queryset.filter(product_id=options["product_id"])

        updated = 0
        missing = 0
        unchanged = 0

        for variant in queryset.order_by("product__nombre", "color", "talla"):
            image_name = find_best_image_for_variant(variant)
            if not image_name:
                missing += 1
                self.stdout.write(self.style.WARNING(f"FALTA: {variant}"))
                continue

            if variant.imagen.name == image_name:
                unchanged += 1
                continue

            self.stdout.write(f"{variant}: {variant.imagen.name or '-'} -> {image_name}")
            updated += 1
            if not options["dry_run"]:
                variant.imagen.name = image_name
                variant.save(update_fields=["imagen", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Listo. Actualizadas: {updated}. Sin cambios: {unchanged}. Sin imagen: {missing}."
            )
        )
