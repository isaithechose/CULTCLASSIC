from django.db import migrations

# Copia congelada de tienda.sat.CODIGO_SAT_POR_CUENTA al momento de esta migracion.
CODIGO_SAT_POR_CUENTA = {
    "1000": "101.01",
    "1010": "102.01",
    "1020": "106.01",
    "1030": "106.01",
    "1100": "115.01",
    "1200": "105.01",
    "2000": "201.01",
    "2100": "202.01",
    "3000": "301.01",
    "4000": "401.39",
    "4010": "402.05",
    "4020": "401.39",
    "5000": "501.01",
    "6000": "601.84",
    "6100": "602.72",
    "6200": "602.74",
}


def cargar(apps, schema_editor):
    from tienda.sat import leer_catalogo

    SatGroupingCode = apps.get_model("tienda", "SatGroupingCode")
    AccountingAccount = apps.get_model("tienda", "AccountingAccount")

    existentes = set(SatGroupingCode.objects.values_list("code", flat=True))
    SatGroupingCode.objects.bulk_create(
        SatGroupingCode(code=code, name=name, level=level)
        for code, name, level in leer_catalogo()
        if code not in existentes
    )
    # Solo cuentas sin codigo: no pisar lo que ya se asigno a mano.
    for code, sat in CODIGO_SAT_POR_CUENTA.items():
        AccountingAccount.objects.filter(code=code, sat_code__isnull=True).update(sat_code_id=sat)


def quitar(apps, schema_editor):
    AccountingAccount = apps.get_model("tienda", "AccountingAccount")
    SatGroupingCode = apps.get_model("tienda", "SatGroupingCode")
    AccountingAccount.objects.update(sat_code=None)
    SatGroupingCode.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0041_sat_codigo_agrupador"),
    ]

    operations = [
        migrations.RunPython(cargar, quitar),
    ]
