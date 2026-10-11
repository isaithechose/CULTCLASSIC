"""Contabilidad electronica del SAT, version 1.3 (Anexo 24 RMF).

Genera los dos XML que se envian cada mes por el buzon tributario:

- Catalogo de cuentas (CT): cada cuenta con su codigo agrupador SAT.
- Balanza de comprobacion (BN normal / BC complementaria): saldo inicial,
  cargos, abonos y saldo final de cada cuenta en el mes.

El SAT recibe cada XML dentro de un .zip con el mismo nombre:
RFC + anio + mes + tipo, por ejemplo XAXX010101000202601BN.zip.
"""

import io
import re
import zipfile
from decimal import Decimal
from xml.etree import ElementTree as ET

from django.db.models import Q, Sum

from .models import AccountingAccount, JournalEntryLine

NS_CATALOGO = "http://www.sat.gob.mx/esquemas/ContabilidadE/1_3/CatalogoCuentas"
XSD_CATALOGO = "http://www.sat.gob.mx/esquemas/ContabilidadE/1_3/CatalogoCuentas/CatalogoCuentas_1_3.xsd"
NS_BALANZA = "http://www.sat.gob.mx/esquemas/ContabilidadE/1_3/BalanzaComprobacion"
XSD_BALANZA = "http://www.sat.gob.mx/esquemas/ContabilidadE/1_3/BalanzaComprobacion/BalanzaComprobacion_1_3.xsd"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"

# Mismo patron que el XSD del SAT (12 caracteres moral, 13 fisica).
RFC_RE = re.compile(r"^[A-ZÑ&]{3,4}[0-9]{2}[0-1][0-9][0-3][0-9][A-Z0-9]{2}[0-9A-Z]$")

# Grupos del codigo agrupador cuya naturaleza va contra la de su seccion.
_ACREEDORAS_EN_ACTIVO = {"108", "116", "171", "172", "183", "189"}  # estimaciones y acumuladas
_DEUDORAS_EN_INGRESOS = {"402"}  # devoluciones y descuentos sobre ventas
_ACREEDORAS_EN_COSTOS = {"503"}  # devoluciones y descuentos sobre compras
_ACREEDORAS_EN_RIF = {"702", "704"}  # productos financieros y otros productos


class ContabilidadElectronicaError(ValueError):
    pass


def naturaleza(codigo_sat):
    """'D' deudora o 'A' acreedora, segun el codigo agrupador."""
    grupo, _, sub = codigo_sat.partition(".")
    seccion = grupo[0]
    if seccion == "1":
        return "A" if grupo in _ACREEDORAS_EN_ACTIVO else "D"
    if seccion in "23":
        return "A"
    if seccion == "4":
        return "D" if grupo in _DEUDORAS_EN_INGRESOS else "A"
    if seccion == "5":
        return "A" if grupo in _ACREEDORAS_EN_COSTOS else "D"
    if seccion == "6":
        return "D"
    if seccion == "7":
        return "A" if grupo in _ACREEDORAS_EN_RIF else "D"
    # 8xx cuentas de orden: .01 la cuenta, .02 su contra cuenta
    return "A" if sub == "02" else "D"


def validar_rfc(rfc):
    rfc = (rfc or "").strip().upper()
    if not RFC_RE.match(rfc):
        raise ContabilidadElectronicaError(f"El RFC «{rfc or 'vacío'}» no tiene un formato válido.")
    return rfc


def cuentas_del_catalogo():
    """Cuentas activas y cualquier cuenta que tenga movimientos."""
    return list(
        AccountingAccount.objects.filter(Q(is_active=True) | Q(journal_lines__isnull=False))
        .distinct()
        .select_related("sat_code", "parent")
        .order_by("code")
    )


def cuentas_sin_codigo_sat(cuentas=None):
    cuentas = cuentas_del_catalogo() if cuentas is None else cuentas
    return [c for c in cuentas if c.sat_code_id is None]


def _nivel(cuenta):
    nivel, actual, vistos = 1, cuenta, {cuenta.pk}
    while actual.parent_id and actual.parent_id not in vistos:
        vistos.add(actual.parent_id)
        actual = actual.parent
        nivel += 1
    return nivel


def _revisar(cuentas):
    faltan = cuentas_sin_codigo_sat(cuentas)
    if faltan:
        lista = ", ".join(f"{c.code} {c.name}" for c in faltan)
        raise ContabilidadElectronicaError(f"Estas cuentas no tienen código agrupador SAT: {lista}.")
    if not cuentas:
        raise ContabilidadElectronicaError("No hay cuentas contables para reportar.")


def _xml(raiz):
    ET.indent(raiz)
    return ET.tostring(raiz, encoding="UTF-8", xml_declaration=True)


def catalogo_xml(*, rfc, anio, mes):
    rfc = validar_rfc(rfc)
    cuentas = cuentas_del_catalogo()
    _revisar(cuentas)

    ET.register_namespace("catalogocuentas", NS_CATALOGO)
    ET.register_namespace("xsi", NS_XSI)
    raiz = ET.Element(f"{{{NS_CATALOGO}}}Catalogo", {
        f"{{{NS_XSI}}}schemaLocation": f"{NS_CATALOGO} {XSD_CATALOGO}",
        "Version": "1.3",
        "RFC": rfc,
        "Mes": f"{mes:02d}",
        "Anio": str(anio),
    })
    for cuenta in cuentas:
        atributos = {
            "CodAgrup": cuenta.sat_code_id,
            "NumCta": cuenta.code,
            "Desc": cuenta.name,
        }
        if cuenta.parent_id:
            atributos["SubCtaDe"] = cuenta.parent.code
        atributos["Nivel"] = str(_nivel(cuenta))
        atributos["Natur"] = naturaleza(cuenta.sat_code_id)
        ET.SubElement(raiz, f"{{{NS_CATALOGO}}}Ctas", atributos)
    return _xml(raiz)


def _sumas(**filtro):
    filas = (
        JournalEntryLine.objects.filter(journal_entry__is_posted=True, **filtro)
        .values("account_id")
        .annotate(debe=Sum("debit"), haber=Sum("credit"))
    )
    return {f["account_id"]: (f["debe"] or Decimal("0"), f["haber"] or Decimal("0")) for f in filas}


def balanza(*, anio, mes):
    """Filas de la balanza del mes; cada cuenta padre acumula a sus subcuentas."""
    from datetime import date
    import calendar

    inicio = date(anio, mes, 1)
    fin = date(anio, mes, calendar.monthrange(anio, mes)[1])
    cuentas = cuentas_del_catalogo()
    previas = _sumas(journal_entry__date__lt=inicio)
    del_mes = _sumas(journal_entry__date__gte=inicio, journal_entry__date__lte=fin)

    hijos = {}
    for c in cuentas:
        if c.parent_id:
            hijos.setdefault(c.parent_id, []).append(c.pk)

    def acumulado(pk, sumas, vistos=()):
        debe, haber = sumas.get(pk, (Decimal("0"), Decimal("0")))
        for hijo in hijos.get(pk, []):
            if hijo not in vistos:
                d, h = acumulado(hijo, sumas, vistos + (pk,))
                debe, haber = debe + d, haber + h
        return debe, haber

    filas = []
    for c in cuentas:
        natur = naturaleza(c.sat_code_id) if c.sat_code_id else "D"
        d_prev, h_prev = acumulado(c.pk, previas)
        debe, haber = acumulado(c.pk, del_mes)
        if natur == "D":
            saldo_ini = d_prev - h_prev
            saldo_fin = saldo_ini + debe - haber
        else:
            saldo_ini = h_prev - d_prev
            saldo_fin = saldo_ini - debe + haber
        filas.append({
            "cuenta": c,
            "naturaleza": natur,
            "saldo_ini": saldo_ini,
            "debe": debe,
            "haber": haber,
            "saldo_fin": saldo_fin,
            "es_hoja": c.pk not in hijos,
        })
    return filas


def _importe(valor):
    return f"{Decimal(valor):.2f}"


def balanza_xml(*, rfc, anio, mes, tipo_envio="N", fecha_mod=None):
    rfc = validar_rfc(rfc)
    if tipo_envio not in ("N", "C"):
        raise ContabilidadElectronicaError("El tipo de envío debe ser N (normal) o C (complementaria).")
    if tipo_envio == "C" and not fecha_mod:
        raise ContabilidadElectronicaError("La balanza complementaria necesita la fecha de modificación.")
    cuentas = cuentas_del_catalogo()
    _revisar(cuentas)

    ET.register_namespace("BCE", NS_BALANZA)
    ET.register_namespace("xsi", NS_XSI)
    atributos = {
        f"{{{NS_XSI}}}schemaLocation": f"{NS_BALANZA} {XSD_BALANZA}",
        "Version": "1.3",
        "RFC": rfc,
        "Mes": f"{mes:02d}",
        "Anio": str(anio),
        "TipoEnvio": tipo_envio,
    }
    if tipo_envio == "C":
        atributos["FechaModBal"] = fecha_mod.isoformat()
    raiz = ET.Element(f"{{{NS_BALANZA}}}Balanza", atributos)
    for fila in balanza(anio=anio, mes=mes):
        ET.SubElement(raiz, f"{{{NS_BALANZA}}}Ctas", {
            "NumCta": fila["cuenta"].code,
            "SaldoIni": _importe(fila["saldo_ini"]),
            "Debe": _importe(fila["debe"]),
            "Haber": _importe(fila["haber"]),
            "SaldoFin": _importe(fila["saldo_fin"]),
        })
    return _xml(raiz)


def nombre_archivo(*, rfc, anio, mes, tipo):
    """tipo: CT catalogo, BN balanza normal, BC balanza complementaria."""
    return f"{rfc.strip().upper()}{anio}{mes:02d}{tipo}"


def empaquetar(nombre, contenido):
    """El buzon tributario recibe el XML comprimido en un .zip del mismo nombre."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{nombre}.xml", contenido)
    return buffer.getvalue()
