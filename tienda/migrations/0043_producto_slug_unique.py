from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0042_populate_producto_slug"),
    ]

    operations = [
        migrations.AlterField(
            model_name="producto",
            name="slug",
            field=models.SlugField(blank=True, max_length=140, unique=True),
        ),
    ]
