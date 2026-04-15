from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0015_producto_slug_imagen"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="skydrop_carrier",
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="skydrop_label_url",
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="skydrop_last_error",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="skydrop_last_payload",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="skydrop_quotation_id",
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="skydrop_rate_id",
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="skydrop_service",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="skydrop_shipment_id",
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="skydrop_tracking_url",
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="shippingaddress",
            name="phone",
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
    ]
