from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0022_businesspayment"),
    ]

    operations = [
        migrations.AddField(
            model_name="productvariant",
            name="imagen",
            field=models.ImageField(blank=True, null=True, upload_to="productos/variantes/"),
        ),
    ]
