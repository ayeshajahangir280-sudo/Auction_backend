from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auctions", "0007_backfill_auction_purse_from_teams"),
    ]

    operations = [
        migrations.AddField(
            model_name="auction",
            name="live_revision",
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
