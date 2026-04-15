from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0017_alter_producto_colores_disponibles_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="stripe_session_id",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="color",
            field=models.CharField(blank=True, max_length=40, null=True),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="diseño_espalda",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="diseño_pecho",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="talla",
            field=models.CharField(blank=True, max_length=10, null=True),
        ),
    ]
