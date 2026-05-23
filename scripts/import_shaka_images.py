"""
Descarga las imagenes de Shaka Wear para cada variante SHK- y las asigna.

Lee /tmp/shaka_products.json (resultado de curl al endpoint /products.json),
filtra solo los SKUs que tenemos, dedupliga URLs (varias tallas comparten foto),
descarga al MEDIA_ROOT/productos/variantes/ y asigna ProductVariant.imagen.

Uso (en VPS):
    cd /var/www/CULTCLASSIC
    curl -sS -H 'User-Agent: Mozilla/5.0' \\
        'https://wholesale.shakawear.com/products.json?limit=250' \\
        -o /tmp/shaka_products.json
    venv/bin/python manage.py shell -c "exec(open('scripts/import_shaka_images.py').read())"
"""
import json
import os
import urllib.request
import urllib.parse
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from tienda.models import ProductVariant

UA = "Mozilla/5.0 (compatible; cultclassics-img-import/1.0)"
PRODUCTS_JSON = Path("/tmp/shaka_products.json")
MEDIA_ROOT = Path(settings.MEDIA_ROOT)
OUT_DIR = MEDIA_ROOT / "productos" / "variantes" / "shaka"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── 1. Cargar productos Shaka y construir mapping SKU → URL imagen ───────
print("=== Cargando productos Shaka ===")
with PRODUCTS_JSON.open(encoding="utf-8") as f:
    payload = json.load(f)
products = payload.get("products", [])
print(f"  Productos en JSON: {len(products)}")

sku_to_img = {}
for p in products:
    imgs_by_id = {img["id"]: img["src"] for img in (p.get("images") or [])}
    product_image = (p.get("image") or {}).get("src") or (p["images"][0]["src"] if p.get("images") else None)
    for v in (p.get("variants") or []):
        sku = v.get("sku")
        if not sku:
            continue
        src = None
        if v.get("featured_image") and v["featured_image"].get("src"):
            src = v["featured_image"]["src"]
        elif v.get("image_id") and v["image_id"] in imgs_by_id:
            src = imgs_by_id[v["image_id"]]
        else:
            src = product_image
        if src:
            sku_to_img[sku] = src

print(f"  SKUs con imagen en Shaka: {len(sku_to_img)}")

# ── 2. Mis variantes ────────────────────────────────────────────────────
mis_variantes = list(ProductVariant.objects.filter(sku__startswith="SHK-"))
print(f"\n  Mis variantes SHK-: {len(mis_variantes)}")

# ── 3. Dedupe URLs (varias variantes pueden compartir la misma imagen) ──
url_to_localname = {}   # url → "MHO02XL.webp" (nombre del archivo local)
matched = 0
unmatched = []

for v in mis_variantes:
    raw_sku = v.sku.replace("SHK-", "", 1)
    src = sku_to_img.get(raw_sku)
    if not src:
        unmatched.append(raw_sku)
        continue
    matched += 1
    if src not in url_to_localname:
        # Construir filename basado en el path original (queda extension)
        parsed = urllib.parse.urlparse(src)
        original = os.path.basename(parsed.path)
        # prepend SKU para evitar colisiones
        name = f"{raw_sku}_{original}"[:180]
        url_to_localname[src] = name

print(f"  SKUs encontrados: {matched}")
print(f"  SKUs sin match:   {len(unmatched)}")
if unmatched:
    print(f"    Faltantes: {unmatched[:15]}{'...' if len(unmatched)>15 else ''}")
print(f"  URLs unicas a descargar: {len(url_to_localname)}")

# ── 4. Descargar imágenes unicas ────────────────────────────────────────
print(f"\n=== Descargando a {OUT_DIR} ===")
download_ok = 0
download_skip = 0
download_fail = []

for src, fname in url_to_localname.items():
    dest = OUT_DIR / fname
    if dest.exists() and dest.stat().st_size > 0:
        download_skip += 1
        continue
    try:
        req = urllib.request.Request(src, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as f:
            f.write(resp.read())
        download_ok += 1
    except Exception as e:
        download_fail.append((src, str(e)))

print(f"  Descargadas:    {download_ok}")
print(f"  Ya existian:    {download_skip}")
print(f"  Fallidas:       {len(download_fail)}")
for src, err in download_fail[:5]:
    print(f"    ! {src} → {err}")

# ── 5. Asignar ProductVariant.imagen ────────────────────────────────────
print("\n=== Asignando imagen a las variantes ===")
asignadas = 0
ya_tenian = 0
sin_archivo = 0

for v in mis_variantes:
    raw_sku = v.sku.replace("SHK-", "", 1)
    src = sku_to_img.get(raw_sku)
    if not src:
        continue
    fname = url_to_localname.get(src)
    if not fname:
        continue
    rel_path = f"productos/variantes/shaka/{fname}"
    dest = MEDIA_ROOT / rel_path
    if not dest.exists():
        sin_archivo += 1
        continue
    if str(v.imagen) == rel_path:
        ya_tenian += 1
        continue
    v.imagen = rel_path
    v.save(update_fields=["imagen", "updated_at"])
    asignadas += 1

print(f"  Asignadas: {asignadas}")
print(f"  Ya tenian: {ya_tenian}")
print(f"  Sin archivo: {sin_archivo}")

# ── 6. Tambien actualizo Producto.imagen si no tiene una (toma la 1ra variante) ─
print("\n=== Actualizando Producto.imagen (1ra variante con imagen) ===")
from tienda.models import Producto
productos_actualizados = 0
for p in Producto.objects.filter(variants__sku__startswith="SHK-").distinct():
    if p.imagen:
        continue
    v = p.variants.filter(sku__startswith="SHK-", imagen__isnull=False).exclude(imagen="").first()
    if v and v.imagen:
        p.imagen = v.imagen
        p.save(update_fields=["imagen", "fecha_actualizacion"])
        productos_actualizados += 1
print(f"  Productos con imagen asignada: {productos_actualizados}")

# ── Resumen ─────────────────────────────────────────────────────────────
print("\n=== RESUMEN ===")
print(f"Variantes SHK- con imagen: {ProductVariant.objects.filter(sku__startswith='SHK-').exclude(imagen='').exclude(imagen__isnull=True).count()}/{len(mis_variantes)}")
print(f"Total archivos descargados: {download_ok + download_skip}")
print(f"Carpeta: {OUT_DIR}")
