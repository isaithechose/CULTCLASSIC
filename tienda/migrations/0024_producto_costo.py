from django.db import migrations, models


def copy_variant_cost_to_product(apps, schema_editor):
    Producto = apps.get_model("tienda", "Producto")
    ProductVariant = apps.get_model("tienda", "ProductVariant")

    for product in Producto.objects.all():
        variant = (
            ProductVariant.objects.filter(product=product, costo__isnull=False)
            .order_by("id")
            .first()
        )
        product.costo = variant.costo if variant else product.precio
        product.save(update_fields=["costo"])


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0023_productvariant_imagen"),
    ]

    operations = [
        migrations.AddField(
            model_name="producto",
            name="costo",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.RunPython(copy_variant_cost_to_product, migrations.RunPython.noop),
    ]
