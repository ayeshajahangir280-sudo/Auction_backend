from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auctions", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="player",
            name="role",
            field=models.CharField(
                blank=True,
                choices=[
                    ("Batter", "Batter"),
                    ("Bowler", "Bowler"),
                    ("All-rounder", "All-rounder"),
                    ("Wicket-keeper", "Wicket-keeper"),
                ],
                default="",
                max_length=32,
            ),
        ),
    ]
