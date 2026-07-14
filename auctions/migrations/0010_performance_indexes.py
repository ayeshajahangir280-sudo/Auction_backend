from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auctions", "0009_auctionsettings_enable_owner_bidding"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="auction",
            index=models.Index(
                fields=["status", "-updated_at", "-created_at"],
                name="auction_status_updated_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="sponsor",
            index=models.Index(
                fields=["auction", "status", "sort_order"],
                name="sponsor_auc_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="category",
            index=models.Index(
                fields=["auction", "status"],
                name="category_auc_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="team",
            index=models.Index(
                fields=["auction", "status"],
                name="team_auction_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="team",
            index=models.Index(
                fields=["auction", "name"],
                name="team_auction_name_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="player",
            index=models.Index(
                fields=["auction", "status", "queue_order", "id"],
                name="player_auc_status_queue_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="player",
            index=models.Index(
                fields=["auction", "sold_team", "status"],
                name="player_auc_sold_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="bid",
            index=models.Index(
                fields=["auction", "player", "bid_status", "-bid_amount", "-created_at"],
                name="bid_auc_player_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="bid",
            index=models.Index(
                fields=["auction", "bid_status", "-created_at"],
                name="bid_auc_status_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="soldplayer",
            index=models.Index(
                fields=["auction", "team", "-sold_time"],
                name="sold_auc_team_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="soldplayer",
            index=models.Index(
                fields=["auction", "-sold_price"],
                name="sold_auc_price_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="auctionlog",
            index=models.Index(
                fields=["auction", "-created_at"],
                name="log_auc_created_idx",
            ),
        ),
    ]
