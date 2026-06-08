"""
Marca la orden W60581 (la mas reciente de Shaka, 17 may 2026) como EN TRANSITO:
- Mantiene el Expense (ya esta pagado)
- Mantiene los purchase-movements (historico de la compra)
- Crea adjustment-movements negativos por la misma cantidad para que
  el stock disponible no incluya esas piezas
- Tag metadata.source = 'shaka-en-transito' para poder revertir cuando llegue

Idempotente: si ya hay ajustes con esta tag para W60581, no hace nada.

Uso:
    venv/bin/python manage.py shell -c "exec(open('scripts/marcar_w60581_en_transito.py').read())"
"""
from django.db import transaction
from django.db.models import Sum

from tienda.models import InventoryMovement, ProductVariant

ORDEN_SHAKA = "W60581"
SOURCE_TAG = "shaka-en-transito"

# 1. Verificar idempotencia
existentes = InventoryMovement.objects.filter(
    metadata__source=SOURCE_TAG, metadata__shaka_order=ORDEN_SHAKA,
).count()
if existentes:
    print(f"⚠ Ya hay {existentes} ajustes con tag {SOURCE_TAG} para {ORDEN_SHAKA}. Saliendo.")
    raise SystemExit(0)

# 2. Buscar los purchase-movements de W60581
purchases = InventoryMovement.objects.filter(
    movement_type="purchase",
    metadata__source="shaka-inventory-v1",
    metadata__shaka_order=ORDEN_SHAKA,
).select_related("variant", "product")

print(f"Purchase-movements encontrados para {ORDEN_SHAKA}: {purchases.count()}")
total_qty = 0

with transaction.atomic():
    for mov in purchases:
        v = mov.variant
        qty = mov.quantity_change
        stock_before = v.stock
        stock_after = stock_before - qty

        InventoryMovement.objects.create(
            product=mov.product,
            variant=v,
            movement_type="adjustment",
            quantity_change=-qty,
            stock_before=stock_before,
            stock_after=stock_after,
            note=f"En transito - pendiente recibir {ORDEN_SHAKA}",
            metadata={
                "source": SOURCE_TAG,
                "shaka_order": ORDEN_SHAKA,
                "linked_purchase_id": mov.id,
                "reason": "Mercancia pagada pero aun no recibida fisicamente",
            },
        )
        v.stock = stock_after
        v.save(update_fields=["stock", "updated_at"])
        total_qty += qty
        print(f"  - {qty} {v.product.nombre[:50]} / {v.color} / {v.talla}  (stock {stock_before} → {stock_after})")

print(f"\nTotal piezas marcadas en transito: {total_qty}")

# 3. Resumen
stock_shk = ProductVariant.objects.filter(sku__startswith="SHK-").aggregate(s=Sum("stock"))["s"] or 0
print(f"Stock SHK- disponible ahora: {stock_shk} pzs")
print()
print(f"Cuando llegue {ORDEN_SHAKA}, para revertir corre:")
print(f"  InventoryMovement.objects.filter(metadata__source='{SOURCE_TAG}', metadata__shaka_order='{ORDEN_SHAKA}').delete()")
print("  Y recalcula stock con resim_shakawear_until_feb.py o manualmente.")
