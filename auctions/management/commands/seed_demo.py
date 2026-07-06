from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from auctions.models import Auction, AuctionSettings, Bid, Category, Player, RoleProfile, Sponsor, Team

User = get_user_model()


class Command(BaseCommand):
    help = "Seed a demo cricket auction with Super Admin, Auction Manager, Team Owners, masters, and bids."

    @transaction.atomic
    def handle(self, *args, **options):
        super_admin, _ = User.objects.get_or_create(username="admin", defaults={"email": "admin@example.com"})
        super_admin.is_staff = True
        super_admin.is_superuser = True
        super_admin.set_password("admin123")
        super_admin.save()
        RoleProfile.objects.update_or_create(
            user=super_admin,
            defaults={"role": RoleProfile.Role.SUPER_ADMIN, "assigned_auction": None, "team": None},
        )

        manager, _ = User.objects.get_or_create(username="manager", defaults={"email": "manager@example.com"})
        manager.set_password("manager123")
        manager.save()

        auction, _ = Auction.objects.update_or_create(
            auction_id="AUC-DEMO2026",
            defaults={
                "name": "Premier T20 League 2026",
                "manager": manager,
                "auction_type": Auction.AuctionType.IN_PERSON,
                "number_of_teams": 4,
                "allotted_to_user": "Rohan Sharma",
                "payment_status": Auction.PaymentStatus.PAID,
                "status": Auction.Status.LIVE,
                "logo_url": "",
                "unit": "",
                "purse_amount": Decimal("12000000"),
                "bid_increment": Decimal("100000"),
                "timer_duration": 30,
                "minimum_players_per_team": 14,
                "maximum_players_per_team": 20,
            },
        )
        AuctionSettings.objects.get_or_create(auction=auction)
        RoleProfile.objects.update_or_create(
            user=manager,
            defaults={"role": RoleProfile.Role.AUCTION_MANAGER, "assigned_auction": auction, "team": None},
        )

        Sponsor.objects.update_or_create(
            auction=auction,
            name="Stride Events",
            defaults={"logo_url": "", "status": "active", "sort_order": 1},
        )

        categories = {}
        for index, item in enumerate(
            [
                ("Platinum", 2, 4, "2000000", "#7C3AED"),
                ("Diamond", 3, 6, "1500000", "#2563EB"),
                ("Gold", 4, 8, "800000", "#F59E0B"),
                ("Emerging", 1, 4, "200000", "#22C55E"),
            ],
            start=1,
        ):
            name, minimum, maximum, base, color = item
            category, _ = Category.objects.update_or_create(
                auction=auction,
                name=name,
                defaults={
                    "category_id": f"CAT-DEMO{index}",
                    "minimum_players": minimum,
                    "maximum_players": maximum,
                    "base_value": Decimal(base),
                    "color": color,
                    "status": Category.Status.ACTIVE,
                },
            )
            categories[name] = category

        teams = {}
        team_specs = [
            ("TEAM-BOM", "Bombay Bulls", "BOM", "Arjun Kapoor", "owner_bom", "owner123", "12000000"),
            ("TEAM-DEL", "Delhi Dynamos", "DEL", "Neha Verma", "owner_del", "owner123", "12000000"),
            ("TEAM-BLR", "Bengaluru Blazers", "BLR", "Suresh Rao", "owner_blr", "owner123", "12000000"),
            ("TEAM-CHE", "Chennai Chargers", "CHE", "Kavya Subramaniam", "owner_che", "owner123", "12000000"),
        ]
        for code, name, short, owner_name, username, password, purse in team_specs:
            owner, _ = User.objects.get_or_create(username=username)
            owner.first_name = owner_name
            owner.set_password(password)
            owner.save()
            team, _ = Team.objects.update_or_create(
                auction=auction,
                short_name=short,
                defaults={
                    "team_id": code,
                    "name": name,
                    "owner_name": owner_name,
                    "owner_username": username,
                    "owner_user": owner,
                    "purse_amount": Decimal(purse),
                    "remaining_purse": Decimal(purse),
                    "maximum_players": 20,
                    "status": Team.Status.ACTIVE,
                },
            )
            RoleProfile.objects.update_or_create(
                user=owner,
                defaults={"role": RoleProfile.Role.TEAM_OWNER, "assigned_auction": None, "team": team},
            )
            teams[short] = team

        player_specs = [
            ("PLY-001", "Aarav", "Malhotra", "Platinum", "All-rounder", "India", 28, "2000000", Player.Status.IN_AUCTION, 1),
            ("PLY-002", "Isabella", "Grant", "Diamond", "Batter", "Australia", 26, "1500000", Player.Status.AVAILABLE, 2),
            ("PLY-003", "Jonty", "Peters", "Gold", "Bowler", "South Africa", 24, "800000", Player.Status.AVAILABLE, 3),
            ("PLY-004", "Rohit", "Yadav", "Diamond", "Wicket-keeper", "India", 27, "1500000", Player.Status.AVAILABLE, 4),
            ("PLY-005", "Kabir", "Anand", "Platinum", "All-rounder", "India", 30, "2000000", Player.Status.AVAILABLE, 5),
            ("PLY-006", "Tanmay", "Bhatia", "Emerging", "Batter", "India", 22, "200000", Player.Status.AVAILABLE, 6),
        ]
        current_player = None
        for code, first, last, category, role, country, age, base, player_status, order in player_specs:
            player, _ = Player.objects.update_or_create(
                auction=auction,
                player_id=code,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "category": categories[category],
                    "role": role,
                    "country": country,
                    "age": age,
                    "base_price": Decimal(base),
                    "status": player_status,
                    "queue_order": order,
                },
            )
            if player_status == Player.Status.IN_AUCTION:
                current_player = player

        auction.current_player = current_player
        auction.save(update_fields=["current_player"])

        if current_player:
            Bid.objects.update_or_create(
                auction=auction,
                player=current_player,
                team=teams["CHE"],
                bid_amount=Decimal("2000000"),
                bid_type=Bid.BidType.MANUAL,
                defaults={"bid_status": Bid.Status.APPROVED, "approved_by_admin": manager},
            )
            Bid.objects.update_or_create(
                auction=auction,
                player=current_player,
                team=teams["BOM"],
                bid_amount=Decimal("2200000"),
                bid_type=Bid.BidType.TEAM_OWNER,
                defaults={"bid_status": Bid.Status.PENDING},
            )

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write("Super Admin: admin / admin123")
        self.stdout.write("Auction Manager: manager / manager123")
        self.stdout.write("Team Owner: owner_bom / owner123")
