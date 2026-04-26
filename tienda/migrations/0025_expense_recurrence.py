from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0024_producto_costo"),
    ]

    operations = [
        migrations.AddField(
            model_name="expense",
            name="recurrencia",
            field=models.CharField(
                choices=[
                    ("none", "No recurrente"),
                    ("weekly", "Semanal"),
                    ("monthly", "Mensual"),
                    ("yearly", "Anual"),
                ],
                default="none",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="expense",
            name="recurrencia_activa",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="expense",
            name="recurrencia_fin",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="expense",
            name="gasto_origen",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="gastos_generados",
                to="tienda.expense",
            ),
        ),
    ]
