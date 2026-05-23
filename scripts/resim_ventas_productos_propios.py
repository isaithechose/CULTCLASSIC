"""
Re-simula 1509 ventas sobre PRODUCTOS PROPIOS (Oversize, DropShoulder, etc.)
manteniendo el stock final igual al actual de la BD.

Acciones:
1. Borra Orders y sale-movements de sims anteriores (Shaka).
2. Resetea stock SHK- al acumulado de purchases (queda como log de materia prima).
3. Distribuye 1509 ventas entre variantes propias activas, ponderado por
   stock_actual (variantes con stock 0 reciben peso 1 minimo).
4. Por cada variante: 1 entrada manual_in + N ventas. Balance neto = 0.

Stock final de variantes propias = stock actual (sin cambio).

Uso:
    venv/bin/python manage.py shell -c "exec(open('scripts/resim_ventas_productos_propios.py').read())"
"""
import random
from collections import defaultdict
from datetime import datetime, timedelta, date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from tienda.models import ProductVariant, InventoryMovement, Order, OrderItem

# ── Config ────────────────────────────────────────────────────────────────
TOTAL_A_VENDER = 1509
CUTOFF = date(2026, 2, 28)
START_RANGE = date(2023, 7, 1)
PRICE_MIN, PRICE_MAX = 220.0, 280.0
MAX_ITEMS_PER_ORDER = 3
SOURCE_TAG_PROPIOS = "ventas-propios-v1"
SOURCE_TAG_VIEJOS = ["shaka-sim-feb2026", "shaka-inventory-v1"]
SEED = 42

random.seed(SEED)

# ── 1. LIMPIAR sims previas ─────────────────────────────────────────────
print("=== FASE 1: limpiar sims previas ===")
with transaction.atomic():
    sale_movs = InventoryMovement.objects.filter(
        metadata__source__in=SOURCE_TAG_VIEJOS + [SOURCE_TAG_PROPIOS],
        movement_type__in=["sale", "manual_in"],
    )
    n_movs = sale_movs.count()
    sale_movs.delete()
    print(f"  Movements borrados: {n_movs}")

    tags_para_borrar = SOURCE_TAG_VIEJOS + [SOURCE_TAG_PROPIOS]
    sim_orders = Order.objects.none()
    for tag in tags_para_borrar:
        sim_orders = sim_orders | Order.objects.filter(internal_note__icontains=tag)
    n_orders = sim_orders.count()
    sim_orders.delete()
    print(f"  Orders simuladas borradas: {n_orders}")

# ── 2. RESETEAR stock SHK- al acumulado de compras ──────────────────────
print("\n=== FASE 2: stock SHK- = compras acumuladas (sin ventas) ===")
shk_variants = ProductVariant.objects.filter(sku__startswith="SHK-")
with transaction.atomic():
    for v in shk_variants:
        total = InventoryMovement.objects.filter(
            variant=v, movement_type="purchase",
            metadata__source="shaka-inventory-v1",
        ).aggregate(s=Sum("quantity_change"))["s"] or 0
        v.stock = total
        v.save(update_fields=["stock", "updated_at"])
total_shk = shk_variants.aggregate(s=Sum("stock"))["s"] or 0
print(f"  Stock SHK- restablecido: {total_shk} pzs (log de materia prima)")

# ── 3. CALCULAR distribución de 1509 ventas en variantes propias ────────
print(f"\n=== FASE 3: distribuir {TOTAL_A_VENDER} ventas entre variantes propias ===")
propias = list(ProductVariant.objects.exclude(sku__startswith="SHK-").filter(activo=True))
print(f"  Variantes propias activas: {len(propias)}")

# Stock antes (lo que va a quedar al final, intacto)
stock_propias_antes = sum(v.stock for v in propias)
print(f"  Stock propias actual: {stock_propias_antes} pzs (no cambiara)")

# Pesos: max(stock, 1) — variantes con stock>0 pesan más
pesos = {v.pk: max(v.stock, 1) for v in propias}
total_peso = sum(pesos.values())

# Cantidades por variante (proporcional)
cant_por_variante = {}
asignado = 0
for v in propias:
    n = int(round(TOTAL_A_VENDER * pesos[v.pk] / total_peso))
    cant_por_variante[v.pk] = n
    asignado += n

# Ajustar para que sume exactamente TOTAL_A_VENDER
diff = TOTAL_A_VENDER - asignado
if diff != 0:
    # Repartir el diff agregando 1 (o -1) a las variantes con mayor peso
    sorted_pks = sorted(pesos, key=lambda k: -pesos[k])
    step = 1 if diff > 0 else -1
    for pk in sorted_pks:
        if diff == 0:
            break
        if cant_por_variante[pk] + step < 0:
            continue
        cant_por_variante[pk] += step
        diff -= step

print(f"  Total distribuido: {sum(cant_por_variante.values())}")
print(f"  Variantes que reciben >=1 venta: {sum(1 for n in cant_por_variante.values() if n > 0)}")

# ── 4. CREAR entradas + Orders + sale-movements ─────────────────────────
print(f"\n=== FASE 4: crear movimientos + Orders ===")

# Pool: por cada venta, (fecha_venta, variant_pk)
pool = []
variants_by_pk = {v.pk: v for v in propias}

# Por cada variante, su rango de fechas
for pk, n in cant_por_variante.items():
    if n == 0:
        continue
    # Fecha inicio aleatoria para esta variante (entre START_RANGE y 6 meses antes de CUTOFF)
    span = (CUTOFF - START_RANGE).days
    inicio = START_RANGE + timedelta(days=random.randint(0, max(1, span - 30)))
    # Genera n fechas entre inicio+1 y CUTOFF
    for _ in range(n):
        delta_days = random.randint(1, max(1, (CUTOFF - inicio).days))
        fecha_venta = inicio + timedelta(days=delta_days)
        pool.append((fecha_venta, pk, inicio))

pool.sort(key=lambda x: x[0])

# Primero crear las entradas (manual_in) — 1 por variante con la cantidad total
print("  Creando entradas manual_in...")
with transaction.atomic():
    for pk, n in cant_por_variante.items():
        if n == 0:
            continue
        v = variants_by_pk[pk]
        # Fecha de entrada: la fecha minima del pool para esta variante - 7 dias
        primeras_fechas = [t[0] for t in pool if t[1] == pk]
        if not primeras_fechas:
            continue
        fecha_entrada = min(primeras_fechas) - timedelta(days=7)
        stock_before = v.stock
        stock_after = stock_before + n
        mov = InventoryMovement.objects.create(
            product=v.product,
            variant=v,
            movement_type="manual_in",
            quantity_change=n,
            stock_before=stock_before,
            stock_after=stock_after,
            note=f"Entrada hist sim ({n} pzs)",
            metadata={"source": SOURCE_TAG_PROPIOS, "kind": "stock_seed"},
        )
        InventoryMovement.objects.filter(pk=mov.pk).update(
            created_at=timezone.make_aware(datetime.combine(fecha_entrada, datetime.min.time()))
        )
        v.stock = stock_after
        v.save(update_fields=["stock", "updated_at"])

print(f"  Entradas creadas: {sum(1 for n in cant_por_variante.values() if n > 0)}")

# Ahora las ventas agrupadas en Orders por fecha
print("  Creando Orders con ventas...")
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
            internal_note=f"[{SOURCE_TAG_PROPIOS}] Venta sim productos propios",
        )
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.make_aware(datetime.combine(venta_date, datetime.min.time()))
        )

        for pk in bucket:
            v = variants_by_pk[pk]
            precio = Decimal(round(random.uniform(PRICE_MIN, PRICE_MAX), 2))
            OrderItem.objects.create(
                order=order, product=v.product, quantity=1, price=precio,
                talla=v.talla, color=v.color,
            )
            stock_before = v.stock
            stock_after = stock_before - 1
            InventoryMovement.objects.create(
                product=v.product, variant=v, order=order,
                movement_type="sale", quantity_change=-1,
                stock_before=stock_before, stock_after=stock_after,
                note=f"Venta sim Order #{order.id}",
                metadata={"source": SOURCE_TAG_PROPIOS, "simulated_price_mxn": str(precio)},
            )
            v.stock = stock_after
            v.save(update_fields=["stock", "updated_at"])
            items_creados += 1
            ingreso_total += precio

        orders_creadas += 1
        i = j

# ── 5. Verificación ─────────────────────────────────────────────────────
print("\n=== VERIFICACIÓN ===")
stock_propias_despues = ProductVariant.objects.exclude(sku__startswith="SHK-").filter(activo=True).aggregate(s=Sum("stock"))["s"] or 0
print(f"  Stock propios antes:    {stock_propias_antes}")
print(f"  Stock propios después:  {stock_propias_despues}")
print(f"  Diferencia (debe ser 0): {stock_propias_despues - stock_propias_antes}")

stock_shk_despues = ProductVariant.objects.filter(sku__startswith="SHK-").aggregate(s=Sum("stock"))["s"] or 0
print(f"  Stock SHK-: {stock_shk_despues} (= compras Shaka acumuladas)")

# ── Resumen ─────────────────────────────────────────────────────────────
print("\n=== RESUMEN ===")
print(f"Orders creadas:     {orders_creadas}")
print(f"Items vendidos:     {items_creados}")
print(f"Ingreso simulado:   ${ingreso_total:,.2f} MXN")
print(f"Promedio por pieza: ${(ingreso_total/items_creados if items_creados else 0):,.2f} MXN")
