"""Codigo agrupador de cuentas del SAT (Anexo 24 de la RMF 2026).

El catalogo viene de tienda/data/sat_codigo_agrupador.csv, extraido del PDF
oficial publicado en el DOF el 13 de enero de 2026.
"""
import csv
from pathlib import Path

CATALOGO_CSV = Path(__file__).resolve().parent / "data" / "sat_codigo_agrupador.csv"

# Cuenta propia -> codigo agrupador. Las ventas van a zona fronteriza norte
# (IVA 8%) porque el negocio aplica el estimulo fronterizo.
CODIGO_SAT_POR_CUENTA = {
    "1000": "101.01",  # Caja -> Caja y efectivo
    "1010": "102.01",  # Bancos -> Bancos nacionales
    "1020": "106.01",  # Tarjetas por cobrar -> Cuentas por cobrar a corto plazo nacional
    "1030": "106.01",  # Mercado Libre por cobrar -> Cuentas por cobrar a corto plazo nacional
    "1100": "115.01",  # Inventario
    "1200": "105.01",  # Clientes -> Clientes nacionales
    "2000": "201.01",  # Proveedores -> Proveedores nacionales
    "2100": "202.01",  # Tarjetas de credito por pagar -> Documentos por pagar bancario nacional
    "3000": "301.01",  # Capital -> Capital fijo
    "4000": "401.39",  # Ventas -> Ventas gravadas en zona fronteriza norte
    "4010": "402.05",  # Descuentos -> Devoluciones/descuentos en zona fronteriza norte
    "4020": "401.39",  # Envio cobrado al cliente: parte de la venta
    "5000": "501.01",  # Costo de ventas -> Costo de venta
    "6000": "601.84",  # Gastos generales -> Otros gastos generales
    "6100": "602.72",  # Fletes y envios -> Gastos de venta: fletes y acarreos
    "6200": "602.74",  # Comisiones de marketplace -> Gastos de venta: comisiones sobre ventas
}


def leer_catalogo():
    """Filas (codigo, nombre, nivel) del codigo agrupador."""
    with open(CATALOGO_CSV, encoding="utf-8", newline="") as f:
        return [(r["codigo"], r["nombre"], int(r["nivel"])) for r in csv.DictReader(f)]
