from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auctions", "0010_performance_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="bid_increment",
            field=models.DecimalField(decimal_places=2, default=1, max_digits=14),
        ),
    ]
