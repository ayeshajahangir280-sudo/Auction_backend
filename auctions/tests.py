from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from config.settings import LOCAL_DEV_CORS_ALLOWED_ORIGIN_REGEXES

from .models import Auction, Bid, Category, Player, RoleProfile, Sponsor, Team, TeamCategoryLimit, TeamOwner


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

    def test_category_rejects_more_selected_players_than_its_maximum(self):
        response = self.client.post(
            f"/api/categories/{self.category.pk}/assign-players/",
            {"player_ids": [player.pk for player in self.players]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Select at most 2 players", str(response.data))

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
        self.assertFalse(TeamOwner.objects.filter(team=team).exists())
        self.assertFalse(RoleProfile.objects.filter(team=team).exists())

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

        owner_state = self.client.get(f"/api/auctions/{self.auction.auction_id}/live-state/")
        self.assertEqual(owner_state.status_code, 200)
        self.assertEqual([team["id"] for team in owner_state.data["teams"]], [self.team.pk])
        self.assertNotIn(other_bid.pk, [bid["id"] for bid in owner_state.data["pending_bids"]])
        self.assertEqual([team["team_id"] for team in owner_state.data["results"]["teams"]], [self.team.team_id])

        bid_response = self.client.post(
            f"/api/auctions/{self.auction.auction_id}/team-owner-bid/",
            {"bid_amount": "100"},
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
        self.assertEqual(public_state.data["current_bid"]["id"], bid_response.data["id"])
        self.assertIn(bid_response.data["id"], [bid["id"] for bid in public_state.data["current_player_bids"]])
