from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0018_checkout_hardening"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="shipping_quote_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_quote_currency",
            field=models.CharField(blank=True, default="MXN", max_length=10, null=True),
        ),
    ]
