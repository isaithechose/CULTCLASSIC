from django.db import migrations
from django.utils.text import slugify


def populate_slugs(apps, schema_editor):
    Producto = apps.get_model("tienda", "Producto")
    seen = set()
    for p in Producto.objects.all().order_by("pk"):
        base = slugify(p.nombre) or f"producto-{p.pk}"
        base = base[:135]
        candidate = base
        i = 2
        while candidate in seen or Producto.objects.exclude(pk=p.pk).filter(slug=candidate).exists():
            suffix = f"-{i}"
            candidate = base[:135 - len(suffix)] + suffix
            i += 1
        p.slug = candidate
        seen.add(candidate)
        p.save(update_fields=["slug"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0041_add_producto_slug"),
    ]

    operations = [
        migrations.RunPython(populate_slugs, noop_reverse),
    ]
