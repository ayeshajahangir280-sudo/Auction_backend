from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from config.settings import LOCAL_DEV_CORS_ALLOWED_ORIGIN_REGEXES

from .models import Auction, AuctionLog, AuctionSettings, Bid, Category, Player, RoleProfile, SoldPlayer, Sponsor, Team, TeamCategoryLimit, TeamOwner


User = get_user_model()


class AuctionWorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="workflow-admin", password="test-pass", is_superuser=True)
        self.manager = User.objects.create_user(username="workflow-manager", password="test-pass")
        self.auction = Auction.objects.create(
            name="Workflow Auction",
            manager=self.manager,
            unit="points",
            bid_increment=Decimal("10"),
        )
        self.other_auction = Auction.objects.create(name="Hidden Auction")
        self.auction_settings = AuctionSettings.objects.create(auction=self.auction)
        AuctionSettings.objects.create(auction=self.other_auction)
        RoleProfile.objects.create(
            user=self.manager,
            role=RoleProfile.Role.AUCTION_MANAGER,
            assigned_auction=self.auction,
        )
        self.category = Category.objects.create(
            auction=self.auction,
            name="Premium",
            maximum_players=2,
            base_value=Decimal("100"),
        )
        self.players = [
            Player.objects.create(
                auction=self.auction,
                category=self.category,
                first_name=f"Player {index}",
                base_price=self.category.base_value,
            )
            for index in range(1, 4)
        ]
        self.team = Team.objects.create(
            auction=self.auction,
            name="Test Team",
            short_name="TT",
            purse_amount=Decimal("1000"),
            remaining_purse=Decimal("1000"),
            maximum_players=2,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_category_management_api_creates_category_for_player_import_id(self):
        response = self.client.post(
            "/api/categories/",
            {
                "auction": self.auction.pk,
                "name": "Manual Category",
                "base_value": "250",
                "maximum_players": 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        category = Category.objects.get(pk=response.data["id"])
        self.assertTrue(category.category_id.startswith("CAT-"))
        self.assertEqual(category.name, "Manual Category")
        self.assertEqual(category.base_value, Decimal("250"))
        self.assertTrue(
            TeamCategoryLimit.objects.filter(
                team=self.team,
                category=category,
                maximum_players=1,
            ).exists()
        )

        upload = SimpleUploadedFile(
            "players.csv",
            f"Name,Category\nDelta Player,{category.category_id}\n".encode("utf-8"),
            content_type="text/csv",
        )
        import_response = self.client.post(
            "/api/players/import-excel/",
            {"auction": self.auction.pk, "file": upload},
            format="multipart",
        )

        self.assertEqual(import_response.status_code, 201)
        imported_player = Player.objects.get(first_name="Delta")
        self.assertEqual(imported_player.category, category)
        self.assertEqual(imported_player.base_price, category.base_value)

    def test_player_import_creates_normalized_categories_from_file(self):
        upload = SimpleUploadedFile(
            "players.csv",
            (
                "Name,Category,Base Price\n"
                "Alpha Player, Category A ,150\n"
                "Beta Player,category a,200\n"
                "Gamma Player,Category B,300\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(
            "/api/players/import-excel/",
            {"auction": self.auction.pk, "file": upload},
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["created_count"], 3)
        self.assertEqual(response.data["created_category_count"], 2)
        category_a = Category.objects.get(auction=self.auction, name="Category A")
        category_b = Category.objects.get(auction=self.auction, name="Category B")
        self.assertEqual(category_a.base_value, Decimal("150"))
        self.assertEqual(category_b.base_value, Decimal("300"))
        self.assertEqual(Player.objects.get(first_name="Alpha").category, category_a)
        self.assertEqual(Player.objects.get(first_name="Beta").category, category_a)
        self.assertEqual(Player.objects.get(first_name="Beta").base_price, Decimal("200"))

    def test_team_category_targets_cannot_exceed_total_squad_maximum(self):
        response = self.client.post(
            f"/api/teams/{self.team.pk}/category-limits/",
            {"limits": [{"category": self.category.pk, "maximum_players": 3}]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("squad maximum is 2", str(response.data))
        self.assertFalse(TeamCategoryLimit.objects.filter(team=self.team).exists())

    def test_category_and_squad_limits_block_further_bids(self):
        TeamCategoryLimit.objects.create(team=self.team, category=self.category, maximum_players=1)
        first_player = self.players[0]
        first_player.status = Player.Status.IN_AUCTION
        first_player.save(update_fields=["status"])
        self.auction.current_player = first_player
        self.auction.save(update_fields=["current_player"])

        sold_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/sell-player/",
            {"team_id": self.team.team_id, "sold_price": "150"},
            format="json",
        )
        self.assertEqual(sold_response.status_code, 200)
        self.assertIsNone(sold_response.data["current_player"])
        self.assertEqual(Decimal(sold_response.data["teams"][0]["required_points"]), Decimal("0"))

        second_player = self.players[1]
        set_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/set-current-player/",
            {"player_id": second_player.player_id},
            format="json",
        )
        self.assertEqual(set_response.status_code, 200)
        category_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/manual-bid/",
            {"team_id": self.team.team_id, "bid_amount": "100"},
            format="json",
        )
        self.assertEqual(category_response.status_code, 400)
        self.assertIn("maximum players for Premium", str(category_response.data))

        blocked_bid = Bid.objects.create(
            auction=self.auction,
            player=second_player,
            team=self.team,
            bid_amount=Decimal("100"),
            bid_type=Bid.BidType.MANUAL,
            bid_status=Bid.Status.PENDING,
        )
        approve_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/bids/{blocked_bid.pk}/approve/",
            {},
            format="json",
        )
        self.assertEqual(approve_response.status_code, 400)
        self.assertIn("maximum players for Premium", str(approve_response.data))
        blocked_bid.refresh_from_db()
        self.assertEqual(blocked_bid.bid_status, Bid.Status.PENDING)

        TeamCategoryLimit.objects.filter(team=self.team).delete()
        self.team.maximum_players = 1
        self.team.save(update_fields=["maximum_players"])
        squad_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/manual-bid/",
            {"team_id": self.team.team_id, "bid_amount": "100"},
            format="json",
        )
        self.assertEqual(squad_response.status_code, 400)
        self.assertIn("maximum squad of 1", str(squad_response.data))

    def test_move_remaining_players_changes_only_available_players_to_target_category(self):
        target_category = Category.objects.create(
            auction=self.auction,
            name="Reserve",
            base_value=Decimal("50"),
        )
        current_player = self.players[0]
        current_player.status = Player.Status.IN_AUCTION
        current_player.save(update_fields=["status"])
        sold_player = self.players[1]
        sold_player.status = Player.Status.SOLD
        sold_player.save(update_fields=["status"])
        remaining_player = self.players[2]
        self.auction.current_player = current_player
        self.auction.status = Auction.Status.LIVE
        self.auction.save(update_fields=["current_player", "status"])
        previous_revision = self.auction.live_revision

        response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/move-remaining-players/",
            {"from_category": self.category.pk, "to_category": target_category.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["moved_count"], 1)
        remaining_player.refresh_from_db()
        current_player.refresh_from_db()
        sold_player.refresh_from_db()
        self.auction.refresh_from_db()
        self.assertEqual(remaining_player.category, target_category)
        self.assertEqual(remaining_player.base_price, target_category.base_value)
        self.assertEqual(current_player.category, self.category)
        self.assertEqual(sold_player.category, self.category)
        self.assertEqual(self.auction.live_revision, previous_revision + 1)
        self.assertEqual(response.data["state"]["current_player"]["id"], current_player.pk)
        self.assertTrue(
            AuctionLog.objects.filter(
                auction=self.auction,
                action="players.category_moved",
                metadata__moved_count=1,
            ).exists()
        )

    def test_move_current_player_category_updates_live_player(self):
        target_category = Category.objects.create(
            auction=self.auction,
            name="Reserve",
            base_value=Decimal("50"),
        )
        current_player = self.players[0]
        current_player.status = Player.Status.IN_AUCTION
        current_player.save(update_fields=["status"])
        self.auction.current_player = current_player
        self.auction.status = Auction.Status.LIVE
        self.auction.save(update_fields=["current_player", "status"])
        previous_revision = self.auction.live_revision

        response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/move-current-player-category/",
            {"to_category": target_category.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        current_player.refresh_from_db()
        self.auction.refresh_from_db()
        self.assertEqual(response.data["from_category"], self.category.pk)
        self.assertEqual(response.data["to_category"], target_category.pk)
        self.assertEqual(current_player.category, target_category)
        self.assertEqual(current_player.base_price, target_category.base_value)
        self.assertEqual(self.auction.live_revision, previous_revision + 1)
        self.assertEqual(response.data["state"]["current_player"]["id"], current_player.pk)
        self.assertEqual(response.data["state"]["current_player"]["category"], target_category.pk)
        self.assertEqual(response.data["state"]["current_player"]["category_name"], "Reserve")
        self.assertEqual(response.data["state"]["current_player"]["base_price"], "50.00")
        self.assertTrue(
            AuctionLog.objects.filter(
                auction=self.auction,
                action="player.category_moved",
                metadata__player=current_player.pk,
                metadata__from_category=self.category.pk,
                metadata__to_category=target_category.pk,
            ).exists()
        )

    def test_mark_unsold_saves_player_and_waits_for_admin_next_action(self):
        current_player = self.players[0]
        current_player.status = Player.Status.IN_AUCTION
        current_player.save(update_fields=["status"])
        self.auction.current_player = current_player
        self.auction.status = Auction.Status.LIVE
        self.auction.save(update_fields=["current_player", "status"])
        pending_bid = Bid.objects.create(
            auction=self.auction,
            player=current_player,
            team=self.team,
            bid_amount=Decimal("100"),
            bid_type=Bid.BidType.MANUAL,
            bid_status=Bid.Status.PENDING,
        )

        response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/mark-unsold/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["current_player"])
        current_player.refresh_from_db()
        self.auction.refresh_from_db()
        pending_bid.refresh_from_db()
        self.assertEqual(current_player.status, Player.Status.UNSOLD)
        self.assertIsNone(self.auction.current_player)
        self.assertEqual(pending_bid.bid_status, Bid.Status.REJECTED)

        unsold_response = self.client.get(f"/api/players/?auction={self.auction.auction_id}&status=unsold")
        self.assertEqual(unsold_response.status_code, 200)
        self.assertEqual([player["id"] for player in unsold_response.data], [current_player.pk])

    def test_random_and_manual_selection_exclude_sold_and_unsold_players(self):
        sold_player, unsold_player, available_player = self.players
        sold_player.status = Player.Status.SOLD
        sold_player.sold_team = self.team
        sold_player.sold_price = Decimal("100")
        sold_player.save(update_fields=["status", "sold_team", "sold_price"])
        unsold_player.status = Player.Status.UNSOLD
        unsold_player.save(update_fields=["status"])

        sold_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/set-current-player/",
            {"player_id": sold_player.player_id},
            format="json",
        )
        self.assertEqual(sold_response.status_code, 400)
        self.assertIn("not sold or unsold", str(sold_response.data))

        unsold_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/set-current-player/",
            {"player_id": unsold_player.player_id},
            format="json",
        )
        self.assertEqual(unsold_response.status_code, 400)
        self.assertIn("not sold or unsold", str(unsold_response.data))

        random_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/next-player/",
            {},
            format="json",
        )
        self.assertEqual(random_response.status_code, 200)
        self.assertEqual(random_response.data["current_player"]["id"], available_player.pk)

    def test_manager_sees_only_assigned_project_and_can_start_it(self):
        self.client.force_authenticate(self.manager)

        list_response = self.client.get("/api/auctions/")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual([item["auction_id"] for item in list_response.data], [self.auction.auction_id])

        start_response = self.client.post(f"/api/auctions/{self.auction.auction_id}/start-auction/", {}, format="json")
        self.assertEqual(start_response.status_code, 200)
        self.assertEqual(start_response.data["auction"]["status"], Auction.Status.LIVE)
        self.assertIsNotNone(start_response.data["current_player"])

        hidden_response = self.client.get(f"/api/auctions/{self.other_auction.auction_id}/live-state/")
        self.assertEqual(hidden_response.status_code, 404)

    def test_live_actions_bump_revision_and_disable_cache(self):
        current_player = self.players[0]
        current_player.status = Player.Status.IN_AUCTION
        current_player.save(update_fields=["status"])
        self.auction.current_player = current_player
        self.auction.status = Auction.Status.LIVE
        self.auction.save(update_fields=["current_player", "status"])
        starting_revision = self.auction.live_revision

        live_response = self.client.get(f"/api/auctions/{self.auction.auction_id}/live-state/")
        self.assertEqual(live_response.status_code, 200)
        self.assertIn("no-store", live_response["Cache-Control"])

        bid_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/manual-bid/",
            {"team_id": self.team.team_id, "bid_amount": "110"},
            format="json",
        )

        self.assertEqual(bid_response.status_code, 201)
        self.assertIn("no-store", bid_response["Cache-Control"])
        self.auction.refresh_from_db()
        self.assertGreater(self.auction.live_revision, starting_revision)

    def test_manual_bid_accepts_comma_formatted_amount_without_server_error(self):
        current_player = self.players[0]
        current_player.status = Player.Status.IN_AUCTION
        current_player.save(update_fields=["status"])
        self.auction.current_player = current_player
        self.auction.status = Auction.Status.LIVE
        self.auction.save(update_fields=["current_player", "status"])

        response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/manual-bid/",
            {"team_id": self.team.team_id, "bid_amount": "1,000.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Decimal(response.data["bid_amount"]), Decimal("1000.00"))

    def test_each_team_first_bid_can_match_base_price(self):
        current_player = self.players[0]
        current_player.status = Player.Status.IN_AUCTION
        current_player.save(update_fields=["status"])
        self.auction.current_player = current_player
        self.auction.status = Auction.Status.LIVE
        self.auction.save(update_fields=["current_player", "status"])
        other_team = Team.objects.create(
            auction=self.auction,
            name="Other Team",
            short_name="OT",
            purse_amount=Decimal("1000"),
            remaining_purse=Decimal("1000"),
            maximum_players=2,
        )

        response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/manual-bid/",
            {"team_id": self.team.team_id, "bid_amount": "100"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Decimal(response.data["bid_amount"]), current_player.base_price)

        other_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/manual-bid/",
            {"team_id": other_team.team_id, "bid_amount": "100"},
            format="json",
        )

        self.assertEqual(other_response.status_code, 201)
        self.assertEqual(Decimal(other_response.data["bid_amount"]), current_player.base_price)

        next_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/manual-bid/",
            {"team_id": self.team.team_id, "bid_amount": "105"},
            format="json",
        )

        self.assertEqual(next_response.status_code, 400)
        self.assertIn("Bid must be at least 110", str(next_response.data))

    def test_lower_pending_bid_cannot_be_approved_when_higher_bid_exists(self):
        current_player = self.players[0]
        current_player.status = Player.Status.IN_AUCTION
        current_player.save(update_fields=["status"])
        self.auction.current_player = current_player
        self.auction.status = Auction.Status.LIVE
        self.auction.save(update_fields=["current_player", "status"])

        lower_bid_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/manual-bid/",
            {"team_id": self.team.team_id, "bid_amount": "110"},
            format="json",
        )
        higher_bid_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/manual-bid/",
            {"team_id": self.team.team_id, "bid_amount": "120"},
            format="json",
        )

        self.assertEqual(lower_bid_response.status_code, 201)
        self.assertEqual(higher_bid_response.status_code, 201)

        approve_lower_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/bids/{lower_bid_response.data['id']}/approve/",
            {},
            format="json",
        )

        self.assertEqual(approve_lower_response.status_code, 400)
        self.assertIn("Only the current highest bid can be approved", str(approve_lower_response.data))
        self.assertEqual(
            Bid.objects.get(pk=lower_bid_response.data["id"]).bid_status,
            Bid.Status.PENDING,
        )

    def test_manual_bid_rejects_inactive_team(self):
        current_player = self.players[0]
        current_player.status = Player.Status.IN_AUCTION
        current_player.save(update_fields=["status"])
        self.auction.current_player = current_player
        self.auction.status = Auction.Status.LIVE
        self.auction.save(update_fields=["current_player", "status"])
        self.team.status = Team.Status.SUSPENDED
        self.team.save(update_fields=["status"])

        response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/manual-bid/",
            {"team_id": self.team.team_id, "bid_amount": "110"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot place bids", str(response.data))
        self.assertFalse(Bid.objects.filter(auction=self.auction, player=current_player).exists())

    def test_complete_auction_marks_remaining_players_unsold(self):
        current_player = self.players[0]
        current_player.status = Player.Status.IN_AUCTION
        current_player.save(update_fields=["status"])
        self.players[1].status = Player.Status.SOLD
        self.players[1].sold_team = self.team
        self.players[1].sold_price = Decimal("150")
        self.players[1].save(update_fields=["status", "sold_team", "sold_price"])
        self.auction.current_player = current_player
        self.auction.status = Auction.Status.LIVE
        self.auction.save(update_fields=["current_player", "status"])

        response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/complete-auction/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.auction.refresh_from_db()
        self.assertEqual(self.auction.status, Auction.Status.COMPLETED)
        self.assertIsNone(self.auction.current_player)
        self.assertEqual(response.data["auction"]["status"], Auction.Status.COMPLETED)
        self.assertEqual(
            list(self.auction.players.order_by("pk").values_list("status", flat=True)),
            [Player.Status.UNSOLD, Player.Status.SOLD, Player.Status.UNSOLD],
        )

    @override_settings(DEBUG=True, CORS_ALLOWED_ORIGIN_REGEXES=LOCAL_DEV_CORS_ALLOWED_ORIGIN_REGEXES)
    def test_private_lan_frontend_origin_can_read_public_auction(self):
        origin = "http://192.168.1.10:8080"

        response = self.client.options(
            "/api/auctions/public-active/",
            HTTP_ORIGIN=origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), origin)

    def test_created_sponsor_is_returned_for_project_filter(self):
        response = self.client.post(
            "/api/sponsors/",
            {
                "auction": self.auction.pk,
                "name": "Broadcast Partner",
                "logo_url": "https://cdn.example.com/sponsor.png",
                "status": "active",
                "sort_order": 1,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)

        list_response = self.client.get(f"/api/sponsors/?auction={self.auction.auction_id}")

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0]["name"], "Broadcast Partner")
        self.assertEqual(list_response.data[0]["logo_url"], "https://cdn.example.com/sponsor.png")

    def test_public_active_includes_project_sponsors(self):
        self.auction.status = Auction.Status.LIVE
        self.auction.save(update_fields=["status"])
        Sponsor.objects.create(
            auction=self.auction,
            name="Title Sponsor",
            logo_url="https://cdn.example.com/title.png",
            status="active",
            sort_order=1,
        )
        public_client = APIClient()

        response = public_client.get("/api/auctions/public-active/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["auction"]["auction_id"], self.auction.auction_id)
        self.assertEqual(response.data["auction"]["sponsors"][0]["name"], "Title Sponsor")
        self.assertEqual(response.data["auction"]["sponsors"][0]["logo_url"], "https://cdn.example.com/title.png")
        self.assertEqual(response.data["auction"]["sponsors"][0]["status"], "active")

    def test_team_creation_does_not_create_owner_credentials_unless_requested(self):
        self.auction.purse_amount = Decimal("3005")
        self.auction.unit = "coins"
        self.auction.save(update_fields=["purse_amount", "unit"])

        response = self.client.post(
            "/api/teams/",
            {
                "auction": self.auction.pk,
                "name": "No Login Team",
                "short_name": "NLT",
                "purse_amount": "500",
                "maximum_players": 2,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        team = Team.objects.get(pk=response.data["id"])
        self.assertEqual(team.owner_username, "")
        self.assertIsNone(team.owner_user)
        self.assertEqual(team.purse_amount, Decimal("3005"))
        self.assertEqual(team.remaining_purse, Decimal("3005"))
        self.assertEqual(Decimal(response.data["purse_amount"]), Decimal("3005"))
        self.assertEqual(response.data["purse_type"], "coins")
        self.assertFalse(TeamOwner.objects.filter(team=team).exists())
        self.assertFalse(RoleProfile.objects.filter(team=team).exists())

    def test_project_purse_aliases_create_and_update_team_budgets(self):
        create_response = self.client.post(
            "/api/auctions/",
            {
                "name": "Project Purse Auction",
                "number_of_teams": 2,
                "purse": "3005",
                "purse_type": "PKR",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        auction = Auction.objects.get(pk=create_response.data["id"])
        self.assertEqual(auction.purse_amount, Decimal("3005"))
        self.assertEqual(auction.unit, "PKR")
        self.assertEqual(Decimal(create_response.data["purse"]), Decimal("3005"))
        self.assertEqual(create_response.data["purse_type"], "PKR")

        sold_player = self.players[0]
        sold_player.status = Player.Status.SOLD
        sold_player.sold_team = self.team
        sold_player.sold_price = Decimal("150")
        sold_player.save(update_fields=["status", "sold_team", "sold_price"])
        SoldPlayer.objects.create(
            auction=self.auction,
            player=sold_player,
            team=self.team,
            sold_price=Decimal("150"),
        )
        self.team.players_bought = 1
        self.team.remaining_purse = Decimal("850")
        self.team.save(update_fields=["players_bought", "remaining_purse"])

        update_response = self.client.patch(
            f"/api/auctions/{self.auction.auction_id}/",
            {"purse": "1200", "purse_type": "points"},
            format="json",
        )

        self.assertEqual(update_response.status_code, 200)
        self.team.refresh_from_db()
        self.auction.refresh_from_db()
        self.assertEqual(self.auction.purse_amount, Decimal("1200"))
        self.assertEqual(self.auction.unit, "points")
        self.assertEqual(self.team.purse_amount, Decimal("1200"))
        self.assertEqual(self.team.remaining_purse, Decimal("1050"))
        self.assertTrue(SoldPlayer.objects.filter(player=sold_player, team=self.team).exists())

    def test_owner_is_scoped_to_own_team_and_bid_reaches_admin_then_public_screen(self):
        owner = User.objects.create_user(username="team-owner", password="owner-pass")
        self.team.owner_name = "Team Owner"
        self.team.owner_username = owner.username
        self.team.owner_user = owner
        self.team.save(update_fields=["owner_name", "owner_username", "owner_user"])
        RoleProfile.objects.create(user=owner, role=RoleProfile.Role.TEAM_OWNER, team=self.team)

        other_team = Team.objects.create(
            auction=self.auction,
            name="Other Team",
            short_name="OT",
            purse_amount=Decimal("1000"),
            remaining_purse=Decimal("1000"),
            maximum_players=2,
        )
        own_player = Player.objects.create(
            auction=self.auction,
            category=self.category,
            first_name="Owned",
            last_name="Player",
            base_price=self.category.base_value,
            status=Player.Status.SOLD,
            sold_team=self.team,
            sold_price=Decimal("100"),
        )
        Player.objects.create(
            auction=self.auction,
            category=self.category,
            first_name="Other",
            last_name="Player",
            base_price=self.category.base_value,
            status=Player.Status.SOLD,
            sold_team=other_team,
            sold_price=Decimal("100"),
        )
        current_player = self.players[0]
        current_player.status = Player.Status.IN_AUCTION
        current_player.save(update_fields=["status"])
        self.auction.current_player = current_player
        self.auction.status = Auction.Status.LIVE
        self.auction.save(update_fields=["current_player", "status"])
        other_bid = Bid.objects.create(
            auction=self.auction,
            player=current_player,
            team=other_team,
            bid_amount=Decimal("100"),
            bid_type=Bid.BidType.TEAM_OWNER,
            bid_status=Bid.Status.PENDING,
        )

        self.client.force_authenticate(owner)
        roster_response = self.client.get(
            f"/api/auctions/{self.auction.auction_id}/team-roster/?team={other_team.team_id}"
        )
        self.assertEqual(roster_response.status_code, 200)
        self.assertEqual(roster_response.data["team"]["id"], self.team.pk)
        self.assertEqual([player["id"] for player in roster_response.data["players"]], [own_player.pk])

        roster_pdf_response = self.client.get(
            f"/api/auctions/{self.auction.auction_id}/team-roster-pdf/?team={other_team.team_id}"
        )
        self.assertEqual(roster_pdf_response.status_code, 200)
        self.assertTrue(roster_pdf_response.content.startswith(b"%PDF"))
        self.assertIn(b"Owned Player", roster_pdf_response.content)
        self.assertNotIn(b"Other Player", roster_pdf_response.content)

        owner_state = self.client.get(f"/api/auctions/{self.auction.auction_id}/live-state/")
        self.assertEqual(owner_state.status_code, 200)
        self.assertEqual([team["id"] for team in owner_state.data["teams"]], [self.team.pk])
        self.assertNotIn(other_bid.pk, [bid["id"] for bid in owner_state.data["pending_bids"]])
        self.assertEqual([team["team_id"] for team in owner_state.data["results"]["teams"]], [self.team.team_id])

        disabled_bid_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/team-owner-bid/",
            {"bid_amount": "110"},
            format="json",
        )
        self.assertEqual(disabled_bid_response.status_code, 403)
        self.assertIn("disabled", str(disabled_bid_response.data).lower())

        self.auction_settings.enable_owner_bidding = True
        self.auction_settings.save(update_fields=["enable_owner_bidding"])

        bid_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/team-owner-bid/",
            {"bid_amount": "110"},
            format="json",
        )
        self.assertEqual(bid_response.status_code, 201)
        self.assertEqual(bid_response.data["team"], self.team.pk)
        self.assertEqual(bid_response.data["bid_status"], Bid.Status.PENDING)

        self.client.force_authenticate(self.admin)
        admin_state = self.client.get(f"/api/auctions/{self.auction.auction_id}/live-state/")
        self.assertIn(bid_response.data["id"], [bid["id"] for bid in admin_state.data["pending_bids"]])
        approve_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/bids/{bid_response.data['id']}/approve/",
            {},
            format="json",
        )
        self.assertEqual(approve_response.status_code, 200)

        public_client = APIClient()
        public_state = public_client.get("/api/auctions/public-active/")
        self.assertEqual(public_state.status_code, 200)
        self.assertEqual(public_state.data["sold_players"][0]["player"], current_player.pk)
        self.assertIn(bid_response.data["id"], [bid["id"] for bid in public_state.data["bid_feed"]])

    def test_team_roster_pdf_is_team_and_project_scoped(self):
        roster_player = Player.objects.create(
            auction=self.auction,
            category=self.category,
            first_name="Roster",
            last_name="Player",
            base_price=self.category.base_value,
            status=Player.Status.SOLD,
            sold_team=self.team,
            sold_price=Decimal("175"),
        )
        SoldPlayer.objects.create(
            auction=self.auction,
            player=roster_player,
            team=self.team,
            sold_price=Decimal("175"),
        )
        other_team = Team.objects.create(
            auction=self.auction,
            name="Other Same Auction",
            short_name="OSA",
            purse_amount=Decimal("1000"),
            remaining_purse=Decimal("1000"),
        )
        other_player = Player.objects.create(
            auction=self.auction,
            category=self.category,
            first_name="Other",
            last_name="Same Auction",
            base_price=self.category.base_value,
            status=Player.Status.SOLD,
            sold_team=other_team,
            sold_price=Decimal("125"),
        )
        SoldPlayer.objects.create(
            auction=self.auction,
            player=other_player,
            team=other_team,
            sold_price=Decimal("125"),
        )
        other_project_player = Player.objects.create(
            auction=self.other_auction,
            first_name="Other",
            last_name="Project",
            status=Player.Status.SOLD,
            sold_price=Decimal("99"),
        )

        response = self.client.get(
            f"/api/auctions/{self.auction.auction_id}/team-roster-pdf/?team={self.team.team_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn(b"Roster Player", response.content)
        self.assertIn(b"175 points", response.content)
        self.assertNotIn(other_player.full_name.encode("latin1"), response.content)
        self.assertNotIn(other_project_player.full_name.encode("latin1"), response.content)
