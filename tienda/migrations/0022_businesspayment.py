from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tienda", "0021_expensecategory_expense"),
    ]

    operations = [
        migrations.CreateModel(
            name="BusinessPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fecha_programada", models.DateField()),
                ("concepto", models.CharField(max_length=140)),
                ("monto", models.DecimalField(decimal_places=2, max_digits=10)),
                ("categoria", models.CharField(choices=[("rent", "Renta"), ("payroll", "Nomina"), ("supplier", "Proveedor"), ("tax", "Impuestos"), ("services", "Servicios"), ("marketing", "Marketing"), ("logistics", "Logistica"), ("other", "Otro")], default="other", max_length=20)),
                ("estado", models.CharField(choices=[("pending", "Pendiente"), ("paid", "Pagado"), ("canceled", "Cancelado")], default="pending", max_length=20)),
                ("fecha_pagado", models.DateField(blank=True, null=True)),
                ("metodo_pago", models.CharField(choices=[("cash", "Efectivo"), ("card", "Tarjeta"), ("transfer", "Transferencia"), ("other", "Otro")], default="transfer", max_length=20)),
                ("proveedor", models.CharField(blank=True, max_length=120, null=True)),
                ("nota", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Pago programado",
                "verbose_name_plural": "Pagos programados",
                "ordering": ["fecha_programada", "estado", "id"],
            },
        ),
    ]
