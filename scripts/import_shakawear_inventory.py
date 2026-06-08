"""
Carga el inventario fisico de las compras de Shaka Wear como entradas
(InventoryMovement tipo 'purchase') y simula la venta de todo lo comprado
con Orders + OrderItems + movimientos 'sale', precio promedio $250 MXN.

Resultado neto: el stock final de cada variante regresa al stock inicial
(generalmente 0 si la variante se crea aqui). El historial queda completo.

Idempotente: usa metadata['source'] = 'shaka-inventory-v1' para marcar
todo lo creado por este script. Si lo corres 2 veces, salta lo existente.

Uso (desde la raiz del proyecto):
    venv/bin/python manage.py shell -c "exec(open('scripts/import_shakawear_inventory.py').read())"
"""
import json
import random
import re
from datetime import datetime, timedelta, date
from decimal import Decimal
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from tienda.models import (
    Producto, ProductVariant, InventoryMovement,
    Order, OrderItem, Categoria,
)

# ── Config ────────────────────────────────────────────────────────────────
PRICE_MIN, PRICE_MAX = Decimal("220"), Decimal("280")  # promedio ≈ $250
SOURCE_TAG = "shaka-inventory-v1"
MAX_ITEMS_PER_ORDER = 3
SEED = 42  # determinista entre ejecuciones

random.seed(SEED)

CANDIDATES = [
    Path.cwd() / "scripts" / "shakawear_orders.json",
    Path.cwd() / "shakawear_orders.json",
    Path("/var/www/CULTCLASSIC/scripts/shakawear_orders.json"),
]
JSON_PATH = next((p for p in CANDIDATES if p.exists()), None)
if not JSON_PATH:
    raise FileNotFoundError(f"No encontre shakawear_orders.json")
print(f"Leyendo {JSON_PATH}")

with JSON_PATH.open(encoding="utf-8") as f:
    ordenes = json.load(f)

# ── Idempotencia: si ya hay movimientos con esta tag, abortar ────────────
existing = InventoryMovement.objects.filter(metadata__source=SOURCE_TAG).count()
if existing:
    print(f"⚠ Ya hay {existing} movimientos con source={SOURCE_TAG}.")
    print("  Para re-ejecutar, primero borra los previos con:")
    print(f"    InventoryMovement.objects.filter(metadata__source='{SOURCE_TAG}').delete()")
    print(f"    Order.objects.filter(internal_note__contains='{SOURCE_TAG}').delete()")
    print(f"    ProductVariant.objects.filter(stock=0, sku__startswith='SHK-').delete()")
    print("Saliendo sin hacer nada.")
    raise SystemExit(0)

# ── Categoria default ─────────────────────────────────────────────────────
categoria, _ = Categoria.objects.get_or_create(nombre="Mercancia Shaka Wear")

# ── Parser de producto "Nombre - Talla / Color" ──────────────────────────
PROD_RE = re.compile(r"^(?P<nombre>.+?) - (?P<talla>\S+) / (?P<color>.+)$")

def parse_product(s):
    m = PROD_RE.match(s.strip())
    if not m:
        return None
    return m.group("nombre").strip(), m.group("talla").strip(), m.group("color").strip()

def money(s):
    return Decimal(str(s).replace("$", "").replace(",", "").strip())

# ── Fase 1: crear productos/variantes y registrar COMPRAS ───────────────
print("\n=== FASE 1: cargar compras (entradas) ===")
productos_cache = {}   # nombre -> Producto
variantes_cache = {}   # sku -> ProductVariant
purchases_by_variant = {}  # sku -> {"qty_total": N, "first_date": date, "variant": ...}

with transaction.atomic():
    for orden in ordenes:
        fecha_orden = datetime.strptime(orden["fecha"], "%B %d, %Y").date()
        for item in orden["items"]:
            parsed = parse_product(item["product"])
            if not parsed:
                print(f"  ⚠ No parsed: {item['product']!r}")
                continue
            nombre, talla, color = parsed
            sku = item["sku"]
            qty = int(item["qty"])
            costo_unit = money(item["price"])

            # Producto base (por nombre)
            if nombre not in productos_cache:
                p, was_created = Producto.objects.get_or_create(
                    nombre=nombre,
                    defaults={
                        "descripcion": f"Mercancia importada de Shaka Wear. Linea: {nombre}",
                        "costo": costo_unit,
                        "precio": Decimal("250.00"),
                        "stock": 0,
                        "categoria": categoria,
                        "tallas_disponibles": "",
                        "colores_disponibles": "",
                    },
                )
                productos_cache[nombre] = p
                if was_created:
                    print(f"  + Producto: {nombre}")
            producto = productos_cache[nombre]

            # Variante (sku unico de Shaka, prefijamos SHK- para distinguir)
            shaka_sku = f"SHK-{sku}"
            if shaka_sku not in variantes_cache:
                variante, was_created_v = ProductVariant.objects.get_or_create(
                    product=producto,
                    talla=talla,
                    color=color,
                    defaults={"sku": shaka_sku, "costo": costo_unit, "stock": 0},
                )
                variantes_cache[shaka_sku] = variante
                if was_created_v:
                    print(f"    + Variante: {nombre} / {color} / {talla} (sku {shaka_sku})")

            variante = variantes_cache[shaka_sku]

            # Movimiento de COMPRA
            stock_before = variante.stock
            stock_after = stock_before + qty
            InventoryMovement.objects.create(
                product=producto,
                variant=variante,
                movement_type="purchase",
                quantity_change=qty,
                stock_before=stock_before,
                stock_after=stock_after,
                note=f"Compra Shaka Wear {orden['orden']}",
                metadata={
                    "source": SOURCE_TAG,
                    "shaka_order": orden["orden"],
                    "shaka_date": orden["fecha"],
                    "unit_cost_usd": str(costo_unit),
                },
            )
            variante.stock = stock_after
            variante.save(update_fields=["stock", "updated_at"])

            agg = purchases_by_variant.setdefault(shaka_sku, {
                "variant": variante, "qty_total": 0, "first_date": fecha_orden,
            })
            agg["qty_total"] += qty
            if fecha_orden < agg["first_date"]:
                agg["first_date"] = fecha_orden

print(f"\n  Variantes nuevas: {sum(1 for v in variantes_cache.values())}")
print(f"  Total comprado:   {sum(a['qty_total'] for a in purchases_by_variant.values())} pzs")

# ── Fase 2: simular VENTAS distribuidas hasta agotar lo comprado ────────
print("\n=== FASE 2: simular ventas (todo lo comprado) ===")
hoy = date.today()

# Construir un "pool" de ventas: lista de (fecha, variant, qty=1)
# para cada pieza vendida, fecha random entre primera_compra y hoy.
pool = []
for shaka_sku, agg in purchases_by_variant.items():
    v = agg["variant"]
    days_span = max(1, (hoy - agg["first_date"]).days)
    for _ in range(agg["qty_total"]):
        offset = random.randint(0, days_span)
        venta_date = agg["first_date"] + timedelta(days=offset)
        pool.append((venta_date, v))

# Ordenar por fecha
pool.sort(key=lambda x: x[0])

# Agrupar en Orders de 1..MAX_ITEMS_PER_ORDER piezas (mismo dia)
orders_creadas = 0
items_creados = 0

with transaction.atomic():
    i = 0
    while i < len(pool):
        venta_date = pool[i][0]
        # Reune todos los items del mismo dia (hasta MAX_ITEMS_PER_ORDER por orden)
        bucket = []
        j = i
        while j < len(pool) and pool[j][0] == venta_date and len(bucket) < MAX_ITEMS_PER_ORDER:
            bucket.append(pool[j][1])
            j += 1
        # Crear Order
        order = Order.objects.create(
            customer=None,
            status="Completed",
            sales_channel="pos",
            payment_method="cash",
            internal_note=f"[{SOURCE_TAG}] Venta simulada",
        )
        # Forzar created_at a la fecha simulada
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.make_aware(datetime.combine(venta_date, datetime.min.time()))
        )

        for variant in bucket:
            precio = Decimal(round(random.uniform(float(PRICE_MIN), float(PRICE_MAX)), 2))
            OrderItem.objects.create(
                order=order,
                product=variant.product,
                quantity=1,
                price=precio,
                talla=variant.talla,
                color=variant.color,
            )
            stock_before = variant.stock
            stock_after = stock_before - 1
            InventoryMovement.objects.create(
                product=variant.product,
                variant=variant,
                order=order,
                movement_type="sale",
                quantity_change=-1,
                stock_before=stock_before,
                stock_after=stock_after,
                note=f"Venta simulada Order #{order.id}",
                metadata={"source": SOURCE_TAG, "simulated_price_mxn": str(precio)},
            )
            variant.stock = stock_after
            variant.save(update_fields=["stock", "updated_at"])
            items_creados += 1

        orders_creadas += 1
        i = j

print(f"\n  Orders creadas: {orders_creadas}")
print(f"  Items vendidos: {items_creados}")

# ── Resumen final ─────────────────────────────────────────────────────────
print("\n=== RESUMEN ===")
total_compras = sum(a["qty_total"] for a in purchases_by_variant.values())
stock_final_total = sum(a["variant"].stock for a in purchases_by_variant.values())
print(f"Piezas compradas: {total_compras}")
print(f"Piezas vendidas:  {items_creados}")
print(f"Stock final:      {stock_final_total} (debe ser 0)")

from django.db.models import Sum
ingresos = OrderItem.objects.filter(order__internal_note__contains=SOURCE_TAG).aggregate(
    s=Sum("price")
)["s"] or Decimal("0")
print(f"Ingreso simulado total: ${ingresos:,.2f} MXN")
print(f"Promedio por pieza:     ${(ingresos/items_creados if items_creados else 0):,.2f} MXN")
