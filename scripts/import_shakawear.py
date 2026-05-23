"""
Carga las compras hechas en Shaka Wear (wholesale.shakawear.com) como Expense
en la BD. Idempotente: si lo corres dos veces, no duplica gastos (usa el
numero de orden W##### como llave dentro del concepto).

Uso (desde la raiz del proyecto):
    python manage.py shell -c "exec(open('scripts/import_shakawear.py').read())"

O en el VPS:
    cd /var/www/CULTCLASSIC
    venv/bin/python manage.py shell -c "exec(open('scripts/import_shakawear.py').read())"
"""
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from tienda.models import Expense, ExpenseCategory

# Buscar el JSON relativo al cwd (donde se corre manage.py)
CANDIDATES = [
    Path.cwd() / "scripts" / "shakawear_orders.json",
    Path.cwd() / "shakawear_orders.json",
    Path("/var/www/CULTCLASSIC/scripts/shakawear_orders.json"),
]
JSON_PATH = next((p for p in CANDIDATES if p.exists()), None)
if not JSON_PATH:
    raise FileNotFoundError(f"shakawear_orders.json no encontrado. Probe: {CANDIDATES}")
print(f"Leyendo {JSON_PATH}")

# ── Categoria ─────────────────────────────────────────────────────────────
categoria, created_cat = ExpenseCategory.objects.get_or_create(
    nombre="Inventario",
    defaults={"descripcion": "Compra de mercancia para reventa / produccion"},
)
print(f"Categoria 'Inventario' {'creada' if created_cat else 'ya existia'} (id={categoria.id})")

# ── Cargar ordenes ────────────────────────────────────────────────────────
with JSON_PATH.open(encoding="utf-8") as f:
    ordenes = json.load(f)

print(f"Procesando {len(ordenes)} ordenes...")

created, updated, skipped = 0, 0, 0
total_usd = Decimal("0")

for o in ordenes:
    orden_num = o["orden"]
    fecha = datetime.strptime(o["fecha"], "%B %d, %Y").date()
    total = Decimal(str(o["total"]))
    status = o.get("status", "Paid")
    nota_items = o.get("nota", "")

    concepto = f"Shaka Wear - {orden_num}"
    nota = (
        f"Pedido {orden_num} · USD ${total} · Status: {status}\n"
        f"Items: {nota_items}"
    )

    defaults = {
        "fecha": fecha,
        "categoria": categoria,
        "monto": total,
        "metodo_pago": "transfer",
        "proveedor": "Shaka Wear (USD)",
        "nota": nota,
    }

    obj, was_created = Expense.objects.update_or_create(
        concepto=concepto,
        proveedor="Shaka Wear (USD)",
        defaults=defaults,
    )

    if was_created:
        created += 1
        print(f"  + {orden_num} {fecha} ${total}")
    else:
        updated += 1
        print(f"  ~ {orden_num} {fecha} ${total} (actualizado)")

    total_usd += total

print()
print(f"Resumen: {created} creados, {updated} actualizados, {skipped} omitidos")
print(f"Total USD acumulado: ${total_usd}")
