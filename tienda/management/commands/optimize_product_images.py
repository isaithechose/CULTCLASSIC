from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageOps

from tienda.utils.variant_image_assignment import SUPPORTED_IMAGE_EXTENSIONS


class Command(BaseCommand):
    help = "Optimiza imágenes de producto y genera thumbnails webp en media/productos."

    def add_arguments(self, parser):
        parser.add_argument("--pattern", default="*", help="Patrón de archivos dentro de media/productos.")
        parser.add_argument("--max-size", type=int, default=1400, help="Lado máximo para imágenes principales.")
        parser.add_argument("--thumb-size", type=int, default=360, help="Lado máximo para thumbnails.")
        parser.add_argument("--quality", type=int, default=82, help="Calidad webp/jpeg.")
        parser.add_argument("--dry-run", action="store_true", help="Solo muestra qué haría.")

    def handle(self, *args, **options):
        products_dir = Path(settings.MEDIA_ROOT) / "productos"
        if not products_dir.exists():
            raise CommandError(f"No existe {products_dir}")

        max_size = options["max_size"]
        thumb_size = options["thumb_size"]
        quality = options["quality"]
        dry_run = options["dry_run"]
        pattern = options["pattern"]
        optimized = 0
        thumbnails = 0
        skipped = 0

        for image_path in sorted(products_dir.glob(pattern)):
            if not image_path.is_file() or image_path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
                continue
            if image_path.stem.endswith("_thumb"):
                skipped += 1
                continue

            thumb_path = image_path.with_name(f"{image_path.stem}_thumb.webp")
            before_size = image_path.stat().st_size

            with Image.open(image_path) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")

                optimized_image = image.copy()
                optimized_image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

                thumb_image = image.copy()
                thumb_image.thumbnail((thumb_size, thumb_size), Image.Resampling.LANCZOS)

                if dry_run:
                    self.stdout.write(f"DRY {image_path.name} -> principal <= {max_size}px, thumb {thumb_path.name}")
                    continue

                save_kwargs = {"quality": quality, "optimize": True}
                if image_path.suffix.lower() == ".webp":
                    optimized_image.save(image_path, "WEBP", method=6, **save_kwargs)
                elif image_path.suffix.lower() in {".jpg", ".jpeg"}:
                    optimized_image.save(image_path, "JPEG", progressive=True, **save_kwargs)
                else:
                    optimized_image.save(image_path, optimize=True)

                thumb_image.save(thumb_path, "WEBP", quality=quality, method=6, optimize=True)

            after_size = image_path.stat().st_size
            optimized += 1
            thumbnails += 1
            self.stdout.write(
                f"{image_path.name}: {before_size // 1024}KB -> {after_size // 1024}KB; thumb {thumb_path.name}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Listo. Optimizadas: {optimized}. Thumbnails: {thumbnails}. Omitidas: {skipped}."
            )
        )
