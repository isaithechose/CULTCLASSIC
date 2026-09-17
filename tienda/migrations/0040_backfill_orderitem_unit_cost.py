from decimal import Decimal

from django.db import migrations


def backfill_unit_cost(apps, schema_editor):
    """Congela el costo de las ventas ya registradas.

    El costo histórico real no es recuperable (nunca se guardó), así que se usa
    el costo actual del producto: es exactamente lo que los reportes venían
    calculando hasta ahora, o sea que ninguna cifra cambia hoy. Lo que cambia es
    que de aquí en adelante deja de moverse cuando el costo del producto cambie.
    """
    OrderItem = apps.get_model("tienda", "OrderItem")
    pendientes = OrderItem.objects.filter(unit_cost__isnull=True).select_related("product")
    actualizados = []
    for item in pendientes:
        item.unit_cost = Decimal(str(item.product.costo or 0))
        actualizados.append(item)
    if actualizados:
        OrderItem.objects.bulk_update(actualizados, ["unit_cost"], batch_size=500)


def limpiar(apps, schema_editor):
    OrderItem = apps.get_model("tienda", "OrderItem")
    OrderItem.objects.update(unit_cost=None)


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0039_orderitem_unit_cost"),
    ]

    operations = [
        migrations.RunPython(backfill_unit_cost, limpiar),
    ]
