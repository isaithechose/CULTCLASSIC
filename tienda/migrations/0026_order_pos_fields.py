from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tienda", "0025_expense_recurrence"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="sales_channel",
            field=models.CharField(
                choices=[
                    ("online", "Tienda online"),
                    ("pos", "Punto de venta"),
                    ("manual", "Manual"),
                ],
                default="online",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("cash", "Efectivo"),
                    ("card", "Tarjeta"),
                    ("transfer", "Transferencia"),
                    ("stripe", "Stripe"),
                    ("other", "Otro"),
                ],
                default="stripe",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="discount_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="order",
            name="internal_note",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="cashier",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pos_orders",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
