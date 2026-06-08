"""Reporta costo promedio ponderado por linea de producto, basado en
compras Shaka en MXN. Util para sugerir precios mayoreo."""

from tienda.models import InventoryMovement

costos = {}
for mov in InventoryMovement.objects.filter(
    movement_type="purchase",
    metadata__source="shaka-inventory-v1",
).select_related("product"):
    nombre = mov.product.nombre
    qty = mov.quantity_change
    mxn = float((mov.metadata or {}).get("unit_cost_mxn", 0) or 0)
    if not mxn:
        continue
    agg = costos.setdefault(nombre, {"qty": 0, "total": 0.0})
    agg["qty"] += qty
    agg["total"] += qty * mxn

print("PRODUCTO".ljust(55), "PZS".rjust(6), "COSTO".rjust(11))
print("-" * 78)
gt_pzs = 0
gt_total = 0.0
for nombre, d in sorted(costos.items(), key=lambda kv: -kv[1]["qty"]):
    prom = d["total"] / d["qty"]
    print(nombre[:55].ljust(55), str(d["qty"]).rjust(6), ("$" + format(prom, ",.2f")).rjust(11))
    gt_pzs += d["qty"]
    gt_total += d["total"]

print("-" * 78)
print("TOTAL".ljust(55), str(gt_pzs).rjust(6), ("$" + format(gt_total / gt_pzs, ",.2f")).rjust(11))
