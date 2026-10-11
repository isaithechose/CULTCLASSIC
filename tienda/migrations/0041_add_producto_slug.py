from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tienda', '0040_backfill_orderitem_unit_cost'),
    ]

    operations = [
        migrations.AddField(
            model_name='producto',
            name='slug',
            field=models.SlugField(blank=True, max_length=140, default=''),
            preserve_default=False,
        ),
    ]
