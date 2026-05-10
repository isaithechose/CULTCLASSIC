from django.db import migrations


DEFAULT_MONEY_ACCOUNTS = [
    ("Caja efectivo", "cash", "1000"),
    ("Bancos", "bank", "1010"),
    ("Tarjetas por cobrar", "processor", "1020"),
]


def seed_money_accounts(apps, schema_editor):
    MoneyAccount = apps.get_model("tienda", "MoneyAccount")
    AccountingAccount = apps.get_model("tienda", "AccountingAccount")
    for name, kind, account_code in DEFAULT_MONEY_ACCOUNTS:
        accounting_account = AccountingAccount.objects.filter(code=account_code).first()
        MoneyAccount.objects.get_or_create(
            name=name,
            defaults={
                "kind": kind,
                "accounting_account": accounting_account,
                "opening_balance": 0,
                "is_active": True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0033_moneyaccount_bankmovement"),
    ]

    operations = [
        migrations.RunPython(seed_money_accounts, migrations.RunPython.noop),
    ]
