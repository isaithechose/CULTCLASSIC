"""Inspecciona qué dejaron los scripts Shaka. Solo lectura."""

from collections import Counter
from tienda.models import (
    Producto, ProductVariant, InventoryMovement, Order, OrderItem,
    Expense, ExpenseCategory, Categoria,
)
from django.db.models import Sum, Count

print("=" * 70)
print("PRODUCTOS")
print("=" * 70)
print(f"Total productos: {Producto.objects.count()}")
print(f"Productos con variantes SHK-: {Producto.objects.filter(variants__sku__startswith='SHK-').distinct().count()}")
print(f"Productos categoria 'Mercancia Shaka Wear': {Producto.objects.filter(categoria__nombre='Mercancia Shaka Wear').count()}")
print()
print("Top 15 productos por stock:")
for p in Producto.objects.order_by("-stock")[:15]:
    has_shk = p.variants.filter(sku__startswith="SHK-").exists()
    has_other = p.variants.exclude(sku__startswith="SHK-").exists()
    marker = ("SHK" if has_shk else "") + ("/PROP" if has_other else "")
    print(f"  stock={p.stock:>6} precio=${p.precio:>7} costo=${p.costo or 0:>6} [{marker:8}] {p.nombre[:50]}")

print()
print("=" * 70)
print("VARIANTES")
print("=" * 70)
total_v = ProductVariant.objects.count()
shk_v = ProductVariant.objects.filter(sku__startswith="SHK-").count()
print(f"Total variantes: {total_v}")
print(f"  SHK-:  {shk_v}")
print(f"  Otras: {total_v - shk_v}")

print()
print("=" * 70)
print("MOVIMIENTOS DE INVENTARIO POR SOURCE")
print("=" * 70)
sources = Counter()
total_movs = InventoryMovement.objects.count()
print(f"Total movimientos: {total_movs}")
for m in InventoryMovement.objects.exclude(metadata__isnull=True).values_list("metadata", flat=True):
    sources[m.get("source", "<sin source>") if isinstance(m, dict) else "<sin source>"] += 1
movs_sin_meta = InventoryMovement.objects.filter(metadata__isnull=True).count()
sources["<metadata=null>"] = movs_sin_meta
for src, n in sources.most_common():
    print(f"  {n:>6}  {src}")

print()
print("=" * 70)
print("ORDERS")
print("=" * 70)
print(f"Total Orders: {Order.objects.count()}")
for tag in ["shaka-inventory-v1", "shaka-sim-feb2026", "ventas-propios-v1"]:
    n = Order.objects.filter(internal_note__icontains=tag).count()
    print(f"  con tag '{tag}': {n}")
real = Order.objects.exclude(internal_note__icontains="shaka-").exclude(internal_note__icontains="ventas-propios").count()
print(f"  sin tags de simulacion: {real}")

print()
print("=" * 70)
print("EXPENSES (gastos)")
print("=" * 70)
for prov in ["Shaka Wear", "Shaka Wear (USD)"]:
    n = Expense.objects.filter(proveedor=prov).count()
    s = Expense.objects.filter(proveedor=prov).aggregate(s=Sum("monto"))["s"] or 0
    print(f"  {prov}: {n} exp / ${s} total")
print(f"  Total Expenses: {Expense.objects.count()}")
print(f"  Categoria 'Inventario' existe: {ExpenseCategory.objects.filter(nombre='Inventario').exists()}")
print(f"  Categoria 'Mercancia Shaka Wear' existe (Productos): {Categoria.objects.filter(nombre='Mercancia Shaka Wear').exists()}")
