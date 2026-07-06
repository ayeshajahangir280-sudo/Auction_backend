from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auctions", "0002_player_role_optional"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auction",
            name="unit",
            field=models.CharField(blank=True, default="", max_length=24),
        ),
    ]
