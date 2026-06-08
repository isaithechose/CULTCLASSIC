"""
Convierte a MXN todo lo que esta en USD relacionado con Shaka Wear:
 - Expenses con proveedor 'Shaka Wear (USD)'  → 'Shaka Wear', monto en MXN
 - InventoryMovement.metadata: agrega unit_cost_mxn y fx_rate, fx_date
 - ProductVariant.costo: promedio ponderado de costo en MXN

Usa frankfurter.app (gratis, datos del BCE) para el tipo de cambio MXN/USD
del dia de cada compra. Si el dia es fin de semana/feriado, el endpoint
devuelve el ultimo dia habil disponible.

Idempotente: si encuentra que un Expense ya esta en MXN (proveedor != USD),
lo salta. Imprime un resumen al final.

Uso:
    venv/bin/python manage.py shell -c "exec(open('scripts/convert_shaka_usd_to_mxn.py').read())"
"""
import json
import urllib.request
from collections import defaultdict
from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum

from tienda.models import Expense, InventoryMovement, ProductVariant


# ── Cache de tipo de cambio por fecha ─────────────────────────────────────
_fx_cache = {}

UA = "Mozilla/5.0 (compatible; cultclassics-fx-import/1.0)"

def _http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get_fx_rate(d):
    """Devuelve MXN por 1 USD para la fecha d (date). Intenta frankfurter,
    cae a jsdelivr currency-api si falla."""
    key = d.isoformat()
    if key in _fx_cache:
        return _fx_cache[key]

    # 1) frankfurter (ECB)
    try:
        data = _http_get_json(f"https://api.frankfurter.app/{key}?from=USD&to=MXN")
        rate = Decimal(str(data["rates"]["MXN"]))
        _fx_cache[key] = rate
        return rate
    except Exception:
        pass

    # 2) jsdelivr currency-api (fawazahmed0) — sin auth, datos historicos
    try:
        data = _http_get_json(
            f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{key}/v1/currencies/usd.json"
        )
        rate = Decimal(str(data["usd"]["mxn"]))
        _fx_cache[key] = rate
        return rate
    except Exception as e2:
        pass

    # 3) fallback al endpoint .min.json
    try:
        data = _http_get_json(
            f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{key}/v1/currencies/usd.min.json"
        )
        rate = Decimal(str(data["usd"]["mxn"]))
        _fx_cache[key] = rate
        return rate
    except Exception as e3:
        raise RuntimeError(f"No pude obtener TC para {key} en ningun proveedor: {e3}")


def to_mxn(usd, rate):
    return (Decimal(str(usd)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ── 1) Expenses Shaka USD → MXN ────────────────────────────────────────────
print("=== Convirtiendo Expenses Shaka USD → MXN ===")
qs = Expense.objects.filter(proveedor="Shaka Wear (USD)").order_by("fecha")
print(f"Expenses por convertir: {qs.count()}")

convertidos = 0
total_usd = Decimal("0")
total_mxn = Decimal("0")

with transaction.atomic():
    for exp in qs:
        rate = get_fx_rate(exp.fecha)
        usd = exp.monto
        mxn = to_mxn(usd, rate)
        nota_extra = f"\n[FX] USD ${usd} × {rate:.4f} = MXN ${mxn} (frankfurter @ {exp.fecha})"
        exp.monto = mxn
        exp.proveedor = "Shaka Wear"
        exp.nota = (exp.nota or "") + nota_extra
        exp.save(update_fields=["monto", "proveedor", "nota"])
        convertidos += 1
        total_usd += usd
        total_mxn += mxn
        print(f"  {exp.fecha} ${usd} USD × {rate:.4f} = ${mxn} MXN")

print(f"\n  Convertidos: {convertidos}")
print(f"  Total USD: ${total_usd}")
print(f"  Total MXN: ${total_mxn}")
if total_usd:
    print(f"  TC promedio implicito: {total_mxn/total_usd:.4f}")

# ── 2) InventoryMovement: agregar unit_cost_mxn ───────────────────────────
print("\n=== Anotando MXN en metadata de InventoryMovement (purchases) ===")
movs = InventoryMovement.objects.filter(
    movement_type="purchase",
    metadata__source="shaka-inventory-v1",
)
print(f"Movimientos por anotar: {movs.count()}")

# Agrupar costos en MXN por variante para luego actualizar ProductVariant.costo
costos_por_variante = defaultdict(list)  # variant_id -> [(qty, costo_mxn_unit)]

with transaction.atomic():
    for mov in movs.iterator():
        meta = mov.metadata or {}
        if "unit_cost_mxn" in meta:
            continue
        fecha_str = meta.get("shaka_date", "")
        try:
            fecha = datetime.strptime(fecha_str, "%B %d, %Y").date()
        except (ValueError, TypeError):
            fecha = mov.created_at.date()

        usd_unit = Decimal(meta.get("unit_cost_usd", "0"))
        if not usd_unit:
            continue
        rate = get_fx_rate(fecha)
        mxn_unit = to_mxn(usd_unit, rate)

        meta["fx_rate"] = str(rate)
        meta["fx_date"] = fecha.isoformat()
        meta["fx_source"] = "frankfurter"
        meta["unit_cost_mxn"] = str(mxn_unit)
        mov.metadata = meta
        mov.save(update_fields=["metadata"])

        costos_por_variante[mov.variant_id].append((mov.quantity_change, mxn_unit))

print(f"  Anotados: {sum(len(v) for v in costos_por_variante.values())}")

# ── 3) ProductVariant.costo: promedio ponderado MXN ───────────────────────
print("\n=== Actualizando ProductVariant.costo (MXN, promedio ponderado) ===")

actualizados = 0
with transaction.atomic():
    for variant_id, entries in costos_por_variante.items():
        total_qty = sum(qty for qty, _ in entries)
        if not total_qty:
            continue
        weighted = sum(qty * costo for qty, costo in entries) / total_qty
        nuevo_costo = weighted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ProductVariant.objects.filter(pk=variant_id).update(costo=nuevo_costo)
        actualizados += 1

print(f"  Variantes actualizadas: {actualizados}")

# ── Resumen ───────────────────────────────────────────────────────────────
print("\n=== RESUMEN ===")
print(f"Fechas únicas consultadas a frankfurter: {len(_fx_cache)}")
print(f"Rates usados:")
for k in sorted(_fx_cache):
    print(f"  {k}: {_fx_cache[k]:.4f}")
print()
print(f"Expenses Shaka en MXN: {Expense.objects.filter(proveedor='Shaka Wear').count()}")
print(f"Suma Expenses MXN: ${Expense.objects.filter(proveedor='Shaka Wear').aggregate(s=Sum('monto'))['s']}")
