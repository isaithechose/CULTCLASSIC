"""
REVERSO TOTAL de todos los scripts Shaka.

Borra (en orden seguro de hijos a padres):
1. OrderItems de Orders simuladas (cascade desde Order, pero por claridad)
2. Orders con tag 'ventas-propios-v1' / 'shaka-inventory-v1' / 'shaka-sim-feb2026'
3. InventoryMovement con source shaka-* y ventas-propios-v1
4. ProductVariants con sku 'SHK-*' (variantes que CREE — no las propias)
5. Producto con categoria 'Mercancia Shaka Wear' (los que CREE)
6. Categoria 'Mercancia Shaka Wear' (la que CREE)
7. Expenses con proveedor 'Shaka Wear' / 'Shaka Wear (USD)'
8. ExpenseCategory 'Inventario' (si no tiene otros gastos)
9. Imagenes descargadas en /media/productos/variantes/shaka/

NO toca:
- Productos propios (Oversize, DropShoulder, Pantalon Ballon, etc.)
- Variantes propias
- Movimientos historicos (los que NO tienen tag mio)
- Orders reales (sin tag de simulacion)
- Otros Expenses y Categorias

Uso:
    venv/bin/python manage.py shell -c "exec(open('scripts/revertir_todo_shaka.py').read())"
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from tienda.models import (
    Producto, ProductVariant, InventoryMovement, Order, OrderItem,
    Expense, ExpenseCategory, Categoria,
)

TAGS_MIS_MOVS = ["shaka-inventory-v1", "shaka-sim-feb2026", "ventas-propios-v1", "shaka-en-transito"]
TAGS_MIS_ORDERS = ["ventas-propios-v1", "shaka-inventory-v1", "shaka-sim-feb2026"]

print("=" * 70)
print("REVIRTIENDO TODO LO DE SHAKA")
print("=" * 70)

with transaction.atomic():
    # 1. Orders simuladas (cascade borra OrderItems)
    orders_q = Order.objects.none()
    for tag in TAGS_MIS_ORDERS:
        orders_q = orders_q | Order.objects.filter(internal_note__icontains=tag)
    n_items = OrderItem.objects.filter(order__in=orders_q).count()
    n_orders = orders_q.count()
    orders_q.delete()
    print(f"1. Orders simuladas borradas: {n_orders} (con {n_items} items)")

    # 2. Movimientos con mis tags
    movs_q = InventoryMovement.objects.filter(metadata__source__in=TAGS_MIS_MOVS)
    n_movs = movs_q.count()
    movs_q.delete()
    print(f"2. InventoryMovements con tag mio borrados: {n_movs}")

    # 3. Variantes SHK- (las que cree)
    shk_v = ProductVariant.objects.filter(sku__startswith="SHK-")
    n_shk_v = shk_v.count()
    shk_v.delete()
    print(f"3. ProductVariants SHK- borradas: {n_shk_v}")

    # 4. Productos categoria 'Mercancia Shaka Wear'
    cat_shk = Categoria.objects.filter(nombre="Mercancia Shaka Wear").first()
    if cat_shk:
        prods_shk = Producto.objects.filter(categoria=cat_shk)
        n_prods = prods_shk.count()
        prods_shk.delete()
        print(f"4. Productos 'Mercancia Shaka Wear' borrados: {n_prods}")
        # 5. Categoria
        cat_shk.delete()
        print(f"5. Categoria 'Mercancia Shaka Wear' borrada")
    else:
        print("4-5. Categoria 'Mercancia Shaka Wear' no existia, saltando")

    # 6. Expenses Shaka
    exp_shk = Expense.objects.filter(Q(proveedor="Shaka Wear") | Q(proveedor="Shaka Wear (USD)"))
    n_exp = exp_shk.count()
    exp_shk.delete()
    print(f"6. Expenses Shaka borrados: {n_exp}")

    # 7. ExpenseCategory 'Inventario' (solo si quedo vacia)
    cat_inv = ExpenseCategory.objects.filter(nombre="Inventario").first()
    if cat_inv:
        if cat_inv.expenses.exists():
            print(f"7. Categoria 'Inventario' tiene {cat_inv.expenses.count()} gastos ajenos, NO se borra")
        else:
            cat_inv.delete()
            print(f"7. Categoria 'Inventario' borrada (estaba vacia)")
    else:
        print("7. ExpenseCategory 'Inventario' no existia")

# 8. Borrar carpeta de imagenes Shaka
img_dir = Path(settings.MEDIA_ROOT) / "productos" / "variantes" / "shaka"
if img_dir.exists():
    n_files = sum(1 for _ in img_dir.iterdir())
    shutil.rmtree(img_dir)
    print(f"8. Imagenes Shaka borradas: {n_files} archivos en {img_dir}")
else:
    print(f"8. Carpeta de imagenes Shaka no existia")

# ── Verificacion final ──────────────────────────────────────────────────
print()
print("=" * 70)
print("ESTADO DESPUES DE LIMPIAR")
print("=" * 70)
print(f"Productos totales: {Producto.objects.count()}")
print(f"Variantes totales: {ProductVariant.objects.count()}")
print(f"  SHK- (debe ser 0): {ProductVariant.objects.filter(sku__startswith='SHK-').count()}")
print(f"InventoryMovements totales: {InventoryMovement.objects.count()}")
for tag in TAGS_MIS_MOVS:
    n = InventoryMovement.objects.filter(metadata__source=tag).count()
    print(f"  tag '{tag}' (debe ser 0): {n}")
print(f"Orders totales: {Order.objects.count()}")
print(f"  con tag de simulacion (debe ser 0): " + str(sum(
    Order.objects.filter(internal_note__icontains=t).count() for t in TAGS_MIS_ORDERS
)))
print(f"Expenses totales: {Expense.objects.count()}")
print(f"  proveedor Shaka (debe ser 0): {Expense.objects.filter(proveedor__icontains='Shaka').count()}")

print()
print("LISTO. Tu sistema deberia estar como antes de los scripts Shaka.")
