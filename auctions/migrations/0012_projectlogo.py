from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("auctions", "0011_category_bid_increment"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectLogo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("logo_url", models.URLField()),
                ("status", models.CharField(default="active", max_length=16)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                (
                    "auction",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="project_logos",
                        to="auctions.auction",
                    ),
                ),
            ],
            options={
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="projectlogo",
            index=models.Index(
                fields=["auction", "status", "sort_order"],
                name="project_logo_auc_status_idx",
            ),
        ),
    ]
