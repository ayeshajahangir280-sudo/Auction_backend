from django.db import migrations
from django.db.models import Max


def backfill_auction_purse(apps, schema_editor):
    Auction = apps.get_model("auctions", "Auction")
    Team = apps.get_model("auctions", "Team")

    for auction in Auction.objects.filter(purse_amount=0):
        team_purse = (
            Team.objects.filter(auction=auction, purse_amount__gt=0)
            .aggregate(value=Max("purse_amount"))
            .get("value")
        )
        if team_purse is not None:
            auction.purse_amount = team_purse
            auction.save(update_fields=["purse_amount"])


class Migration(migrations.Migration):
    dependencies = [
        ("auctions", "0006_alter_auction_bid_increment"),
    ]

    operations = [
        migrations.RunPython(backfill_auction_purse, migrations.RunPython.noop),
    ]
