from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("auctions", "0003_auction_unit_optional"),
    ]

    operations = [
        migrations.CreateModel(
            name="TeamCategoryLimit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("maximum_players", models.PositiveIntegerField(default=0)),
                (
                    "category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="team_limits",
                        to="auctions.category",
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="category_limits",
                        to="auctions.team",
                    ),
                ),
            ],
            options={
                "ordering": ["category__name"],
            },
        ),
        migrations.AddConstraint(
            model_name="teamcategorylimit",
            constraint=models.UniqueConstraint(fields=("team", "category"), name="unique_team_category_limit"),
        ),
    ]
