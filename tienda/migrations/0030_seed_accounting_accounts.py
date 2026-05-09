from django.db import migrations


DEFAULT_ACCOUNTS = [
    ("1000", "Caja", "asset"),
    ("1010", "Bancos", "asset"),
    ("1020", "Tarjetas por cobrar", "asset"),
    ("1100", "Inventario", "asset"),
    ("1200", "Clientes", "asset"),
    ("2000", "Proveedores", "liability"),
    ("2100", "Tarjetas de credito por pagar", "liability"),
    ("3000", "Capital", "equity"),
    ("4000", "Ventas", "income"),
    ("4010", "Descuentos sobre ventas", "income"),
    ("5000", "Costo de ventas", "cost"),
    ("6000", "Gastos generales", "expense"),
]


def seed_accounts(apps, schema_editor):
    AccountingAccount = apps.get_model("tienda", "AccountingAccount")
    for code, name, account_type in DEFAULT_ACCOUNTS:
        AccountingAccount.objects.get_or_create(
            code=code,
            defaults={"name": name, "account_type": account_type, "is_active": True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0029_accountingaccount_journalentry_journalentryline"),
    ]

    operations = [
        migrations.RunPython(seed_accounts, migrations.RunPython.noop),
    ]
