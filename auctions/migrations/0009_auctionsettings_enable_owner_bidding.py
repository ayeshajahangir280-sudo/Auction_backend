from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auctions", "0008_auction_live_revision"),
    ]

    operations = [
        migrations.AddField(
            model_name="auctionsettings",
            name="enable_owner_bidding",
            field=models.BooleanField(default=False),
        ),
    ]
