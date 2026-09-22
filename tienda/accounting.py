"""Pólizas automáticas que no dependen del admin.

Aquí viven los asientos que deben generarse solos cuando pasa algo en el
negocio (una venta de Mercado Libre, un pago de tarjeta), para que puedan
dispararse desde cualquier parte —el sync, un comando, una acción del admin—
sin importar tienda.admin.

Todas las funciones son idempotentes: si la póliza de ese hecho ya existe, no
la duplican.
"""

import logging
from decimal import Decimal

from django.db import transaction

from .models import AccountingAccount, JournalEntry, JournalEntryLine

logger = logging.getLogger(__name__)

# Cuentas que usan estos asientos. Si alguna no existe en el catálogo, se crea
# la primera vez con su nombre y naturaleza.
CUENTAS = {
    "1000": ("Caja", "asset"),
    "1010": ("Bancos", "asset"),
    "1030": ("Mercado Libre por cobrar", "asset"),
    "1100": ("Inventario", "asset"),
    "2100": ("Tarjetas de credito por pagar", "liability"),
    "4000": ("Ventas", "income"),
    "5000": ("Costo de ventas", "cost"),
    "6100": ("Fletes y envios", "expense"),
    "6200": ("Comisiones de marketplace", "expense"),
}


def _money(value):
    return Decimal(str(value or 0))


def cuenta(code):
    nombre, tipo = CUENTAS.get(code, (code, "asset"))
    objeto, creada = AccountingAccount.objects.get_or_create(
        code=code, defaults={"name": nombre, "account_type": tipo}
    )
    if creada:
        logger.info("Cuenta contable %s (%s) creada automaticamente", code, nombre)
    return objeto


def crear_poliza(*, date_value, concept, reference, lines, entry_type="diary",
                 source="manual", created_by=None, order=None):
    """Crea una póliza cuadrada. Devuelve None si no hay importes."""
    debe = sum(_money(l.get("debit")) for l in lines)
    haber = sum(_money(l.get("credit")) for l in lines)
    if debe != haber:
        raise ValueError(f"La poliza no cuadra: debe {debe}, haber {haber}")
    if debe <= 0:
        return None

    with transaction.atomic():
        entry = JournalEntry.objects.create(
            date=date_value, entry_type=entry_type, source=source,
            concept=concept[:180], reference=reference[:80],
            order=order, is_posted=True, created_by=created_by,
        )
        JournalEntryLine.objects.bulk_create([
            JournalEntryLine(
                journal_entry=entry,
                account=l["account"],
                debit=_money(l.get("debit")),
                credit=_money(l.get("credit")),
                description=(l.get("description") or "")[:180],
            )
            for l in lines
            if _money(l.get("debit")) or _money(l.get("credit"))
        ])
    return entry


# ── Mercado Libre ───────────────────────────────────────────────────────────

ML_REFERENCIA = "ML-{ml_id}"
ML_REFERENCIA_CANCELA = "ML-CANC-{ml_id}"
ML_ESTADOS_VALIDOS = ("paid", "confirmed", "shipped", "delivered")


def _costo_de_venta_ml(ml_order):
    """Costo de los productos vendidos, desde la publicación enlazada."""
    from mercadolibre.models import MercadoLibreListing

    costo = Decimal("0.00")
    for item in ml_order.items.all():
        listing = (
            MercadoLibreListing.objects
            .filter(ml_id=item.item_id, producto__isnull=False)
            .select_related("producto")
            .first()
        )
        if listing and listing.producto:
            costo += _money(listing.producto.costo) * Decimal(str(item.quantity or 0))
    return costo


def post_ml_order_entry(ml_order, created_by=None):
    """Registra la venta de Mercado Libre en los libros.

    ML deposita el neto y se queda con comisión y envío, así que el asiento
    separa las tres cosas: lo que vas a cobrar, lo que te cobraron y la venta
    completa. El costo de lo vendido sale del producto enlazado a la
    publicación.
    """
    if ml_order.status not in ML_ESTADOS_VALIDOS:
        return None
    referencia = ML_REFERENCIA.format(ml_id=ml_order.ml_id)
    if JournalEntry.objects.filter(reference=referencia).exists():
        return None

    total = _money(ml_order.total_amount)
    comision = _money(ml_order.marketplace_fee)
    envio = _money(ml_order.shipping_cost)
    neto = _money(ml_order.net_received_amount) or (total - comision - envio)
    if total <= 0:
        return None

    lines = [
        {"account": cuenta("1030"), "debit": neto, "description": "Por depositar de Mercado Libre"},
        {"account": cuenta("6200"), "debit": comision, "description": "Comision de Mercado Libre"},
        {"account": cuenta("6100"), "debit": envio, "description": "Envio cubierto por el vendedor"},
        {"account": cuenta("4000"), "credit": total, "description": f"Venta ML #{ml_order.ml_id}"},
    ]
    costo = _costo_de_venta_ml(ml_order)
    if costo > 0:
        lines.append({"account": cuenta("5000"), "debit": costo, "description": "Costo de lo vendido"})
        lines.append({"account": cuenta("1100"), "credit": costo, "description": "Salida de inventario"})

    return crear_poliza(
        date_value=ml_order.date_created.date(),
        entry_type="income", source="manual",
        concept=f"Venta Mercado Libre #{ml_order.ml_id} - {ml_order.buyer_nickname or 'comprador'}",
        reference=referencia, lines=lines, created_by=created_by,
    )


def reverse_ml_order_entry(ml_order, created_by=None):
    """Cancela contablemente una venta de ML que se cayó después de registrarse."""
    referencia = ML_REFERENCIA.format(ml_id=ml_order.ml_id)
    original = JournalEntry.objects.filter(reference=referencia).first()
    if original is None:
        return None
    referencia_cancela = ML_REFERENCIA_CANCELA.format(ml_id=ml_order.ml_id)
    if JournalEntry.objects.filter(reference=referencia_cancela).exists():
        return None

    lines = [
        {"account": l.account, "debit": l.credit, "credit": l.debit, "description": f"Cancela: {l.description}"}
        for l in original.lines.all()
    ]
    from django.utils import timezone

    return crear_poliza(
        date_value=timezone.localdate(),
        entry_type="diary", source="manual",
        concept=f"Cancelacion de venta ML #{ml_order.ml_id}",
        reference=referencia_cancela, lines=lines, created_by=created_by,
    )


def sincronizar_poliza_ml(ml_order, created_by=None):
    """Postea o cancela según el estado actual del pedido. Segura de repetir."""
    try:
        if ml_order.status in ML_ESTADOS_VALIDOS:
            return post_ml_order_entry(ml_order, created_by=created_by)
        return reverse_ml_order_entry(ml_order, created_by=created_by)
    except Exception:
        # La contabilidad nunca debe tumbar la sincronizacion de pedidos.
        logger.exception("No se pudo registrar la poliza del pedido ML %s", ml_order.ml_id)
        return None


# ── Pagos a la tarjeta de crédito ───────────────────────────────────────────

ORIGEN_PAGO = {"cash": "1000", "transfer": "1010"}


def post_card_payment(*, fecha, monto, origen="cash", nota="", created_by=None, referencia=None):
    """Baja la deuda de la tarjeta contra el dinero con que la pagaste."""
    monto = _money(monto)
    if monto <= 0:
        return None
    # La referencia evita que un refresh del formulario duplique el pago, pero
    # incluye la nota para que dos abonos distintos del mismo dia y monto si
    # puedan registrarse.
    referencia = referencia or f"PAGO-TDC-{fecha}-{monto}-{(nota or '')[:20]}"
    if JournalEntry.objects.filter(reference=referencia).exists():
        return None
    origen_code = ORIGEN_PAGO.get(origen, "1000")
    return crear_poliza(
        date_value=fecha,
        entry_type="expense", source="credit_card",
        concept=f"Pago a tarjeta de credito{' - ' + nota if nota else ''}",
        reference=referencia,
        lines=[
            {"account": cuenta("2100"), "debit": monto, "description": "Abono a la tarjeta"},
            {"account": cuenta(origen_code), "credit": monto, "description": nota or "Salida de dinero"},
        ],
        created_by=created_by,
    )
