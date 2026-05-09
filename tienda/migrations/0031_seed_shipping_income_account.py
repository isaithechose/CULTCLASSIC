from django.db import migrations


def seed_shipping_income_account(apps, schema_editor):
    AccountingAccount = apps.get_model("tienda", "AccountingAccount")
    AccountingAccount.objects.get_or_create(
        code="4020",
        defaults={
            "name": "Ingresos por envio",
            "account_type": "income",
            "is_active": True,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0030_seed_accounting_accounts"),
    ]

    operations = [
        migrations.RunPython(seed_shipping_income_account, migrations.RunPython.noop),
    ]
