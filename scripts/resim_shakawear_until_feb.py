"""
Re-simula las ventas de inventario Shaka SOLO hasta feb 28, 2026.

Acciones:
1. Borra Orders y sale-movements de la sim previa (source=shaka-inventory-v1).
2. Recalcula stock SHK- desde los purchase-movements (que se mantienen).
3. Simula ventas con fecha entre primera compra y CUTOFF, vendiendo todo lo
   comprado HASTA CUTOFF. Compras posteriores a CUTOFF quedan como stock.

Uso:
    venv/bin/python manage.py shell -c "exec(open('scripts/resim_shakawear_until_feb.py').read())"
"""
import random
from collections import defaultdict
from datetime import datetime, timedelta, date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from tienda.models import (
    ProductVariant, InventoryMovement, Order, OrderItem,
)

CUTOFF = date(2026, 2, 28)
PRICE_MIN, PRICE_MAX = 220.0, 280.0
MAX_ITEMS_PER_ORDER = 3
SOURCE_TAG = "shaka-inventory-v1"
SOURCE_TAG_V2 = "shaka-sim-feb2026"
SEED = 42

random.seed(SEED)

# ── 1. LIMPIAR sim previa ────────────────────────────────────────────────
print("=== FASE 1: limpiar sim previa ===")
with transaction.atomic():
    # Borrar sale-movements de la sim previa
    sale_movs = InventoryMovement.objects.filter(
        metadata__source__in=[SOURCE_TAG, SOURCE_TAG_V2],
        movement_type="sale",
    )
    n_sales = sale_movs.count()
    sale_movs.delete()
    print(f"  Sale-movements borrados: {n_sales}")

    # Borrar Orders simuladas (las que tienen el tag en internal_note)
    sim_orders = Order.objects.filter(internal_note__icontains=SOURCE_TAG)
    n_orders = sim_orders.count()
    # OrderItems se borran en cascade
    sim_orders.delete()
    print(f"  Orders simuladas borradas: {n_orders}")

# ── 2. RECALCULAR STOCK SHK- desde purchases ────────────────────────────
print("\n=== FASE 2: recalcular stock SHK- desde compras ===")
shk_variants = ProductVariant.objects.filter(sku__startswith="SHK-")
print(f"  Variantes SHK-: {shk_variants.count()}")

from django.db.models import Sum
with transaction.atomic():
    for v in shk_variants:
        total = InventoryMovement.objects.filter(
            variant=v, movement_type="purchase", metadata__source=SOURCE_TAG,
        ).aggregate(s=Sum("quantity_change"))["s"] or 0
        v.stock = total
        v.save(update_fields=["stock", "updated_at"])
total_stock_post = shk_variants.aggregate(s=Sum("stock"))["s"] or 0
print(f"  Stock SHK- restaurado al acumulado de compras: {total_stock_post} pzs")

# ── 3. CONSTRUIR pool de ventas (solo hasta CUTOFF) ──────────────────────
print(f"\n=== FASE 3: simular ventas hasta {CUTOFF} ===")

# Por variante, sumar lo comprado HASTA CUTOFF
purchases_until = defaultdict(lambda: {"qty": 0, "first_date": None})
for mov in InventoryMovement.objects.filter(
    variant__sku__startswith="SHK-",
    movement_type="purchase",
    metadata__source=SOURCE_TAG,
).select_related("variant"):
    # Fecha de la compra: viene en metadata['shaka_date']
    fecha_str = (mov.metadata or {}).get("shaka_date", "")
    try:
        fecha = datetime.strptime(fecha_str, "%B %d, %Y").date()
    except (ValueError, TypeError):
        fecha = mov.created_at.date()

    if fecha > CUTOFF:
        continue  # esa compra es post-cutoff; queda como stock

    agg = purchases_until[mov.variant_id]
    agg["qty"] += mov.quantity_change
    if agg["first_date"] is None or fecha < agg["first_date"]:
        agg["first_date"] = fecha
    agg["variant"] = mov.variant

print(f"  Variantes a vender: {len(purchases_until)}")
total_a_vender = sum(a["qty"] for a in purchases_until.values())
print(f"  Total a vender (hasta {CUTOFF}): {total_a_vender} pzs")

# Construir pool: lista de (fecha, variant) por cada pieza
pool = []
for agg in purchases_until.values():
    v = agg["variant"]
    fd = agg["first_date"]
    span_days = max(1, (CUTOFF - fd).days)
    for _ in range(agg["qty"]):
        offset = random.randint(0, span_days)
        venta_date = fd + timedelta(days=offset)
        pool.append((venta_date, v))
pool.sort(key=lambda x: x[0])

# ── 4. CREAR Orders + sale-movements ──────────────────────────────────────
print(f"\n=== FASE 4: crear Orders + movimientos sale ===")
orders_creadas = 0
items_creados = 0
ingreso_total = Decimal("0")

with transaction.atomic():
    i = 0
    while i < len(pool):
        venta_date = pool[i][0]
        bucket = []
        j = i
        while j < len(pool) and pool[j][0] == venta_date and len(bucket) < MAX_ITEMS_PER_ORDER:
            bucket.append(pool[j][1])
            j += 1

        order = Order.objects.create(
            customer=None,
            status="Completed",
            sales_channel="pos",
            payment_method="cash",
            internal_note=f"[{SOURCE_TAG_V2}] Venta simulada hist (hasta {CUTOFF})",
        )
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.make_aware(datetime.combine(venta_date, datetime.min.time()))
        )

        for variant in bucket:
            precio = Decimal(round(random.uniform(PRICE_MIN, PRICE_MAX), 2))
            OrderItem.objects.create(
                order=order, product=variant.product, quantity=1, price=precio,
                talla=variant.talla, color=variant.color,
            )
            stock_before = variant.stock
            stock_after = stock_before - 1
            InventoryMovement.objects.create(
                product=variant.product, variant=variant, order=order,
                movement_type="sale", quantity_change=-1,
                stock_before=stock_before, stock_after=stock_after,
                note=f"Venta sim Order #{order.id}",
                metadata={"source": SOURCE_TAG_V2, "simulated_price_mxn": str(precio)},
            )
            variant.stock = stock_after
            variant.save(update_fields=["stock", "updated_at"])
            items_creados += 1
            ingreso_total += precio

        orders_creadas += 1
        i = j

print(f"  Orders creadas: {orders_creadas}")
print(f"  Items vendidos: {items_creados}")

# ── 5. RESUMEN ────────────────────────────────────────────────────────────
print("\n=== RESUMEN FINAL ===")
total_comprado = InventoryMovement.objects.filter(
    variant__sku__startswith="SHK-", movement_type="purchase",
).aggregate(s=Sum("quantity_change"))["s"] or 0
total_vendido = items_creados
stock_shk_final = shk_variants.aggregate(s=Sum("stock"))["s"] or 0
stock_otros = ProductVariant.objects.exclude(sku__startswith="SHK-").aggregate(s=Sum("stock"))["s"] or 0

print(f"Compras totales Shaka:     {total_comprado} pzs")
print(f"Ventas simuladas (≤feb28): {total_vendido} pzs")
print(f"Stock SHK- final:          {stock_shk_final} pzs  (= compras post-{CUTOFF})")
print(f"Stock productos propios:   {stock_otros} pzs  (sin cambios)")
print(f"Stock total combinado:     {stock_shk_final + stock_otros} pzs")
print(f"Ingreso simulado:          ${ingreso_total:,.2f} MXN")
print(f"Promedio por pieza:        ${(ingreso_total/total_vendido if total_vendido else 0):,.2f} MXN")
