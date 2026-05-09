from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tienda", "0026_order_pos_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="CashRegisterClosure",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fecha", models.DateField(unique=True)),
                ("efectivo_contado", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("tarjeta_contado", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("transferencia_contado", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("otros_contado", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("efectivo_sistema", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("tarjeta_sistema", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("transferencia_sistema", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("otros_sistema", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("gastos_efectivo", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("diferencia", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("nota", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "closed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Cierre de caja",
                "verbose_name_plural": "Cierres de caja",
                "ordering": ["-fecha"],
            },
        ),
    ]
