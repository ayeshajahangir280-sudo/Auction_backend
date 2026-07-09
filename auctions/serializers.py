from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from .models import (
    Auction,
    AuctionLog,
    AuctionSettings,
    Bid,
    Category,
    Player,
    RoleProfile,
    SoldPlayer,
    Sponsor,
    Team,
    TeamCategoryLimit,
    TeamOwner,
)

User = get_user_model()


class UserSummarySerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    auction_id = serializers.SerializerMethodField()
    team_id = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "last_login",
            "date_joined",
            "role",
            "auction_id",
            "team_id",
        ]

    def get_role(self, obj):
        profile = getattr(obj, "role_profile", None)
        return profile.role if profile else ("super_admin" if obj.is_superuser else "")

    def get_auction_id(self, obj):
        profile = getattr(obj, "role_profile", None)
        auction = profile.assigned_auction if profile else None
        if not auction and profile and profile.team:
            auction = profile.team.auction
        return auction.auction_id if auction else None

    def get_team_id(self, obj):
        profile = getattr(obj, "role_profile", None)
        return profile.team.team_id if profile and profile.team else None


class SponsorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sponsor
        fields = ["id", "auction", "name", "logo_url", "status", "sort_order"]
        read_only_fields = ["id"]


class AuctionSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuctionSettings
        fields = [
            "show_remaining_purse",
            "require_bid_approval",
            "auto_advance_after_sale",
            "sponsor_rotation_seconds",
            "public_screen_theme",
            "notes",
        ]


class AuctionSerializer(serializers.ModelSerializer):
    manager_username = serializers.CharField(write_only=True, required=False, allow_blank=True)
    manager_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    manager_name = serializers.CharField(source="manager.get_username", read_only=True)
    sponsors = SponsorSerializer(many=True, read_only=True)
    settings = AuctionSettingsSerializer(read_only=True)
    team_count = serializers.IntegerField(source="teams.count", read_only=True)
    player_count = serializers.IntegerField(source="players.count", read_only=True)
    setup_enabled = serializers.SerializerMethodField()
    purse = serializers.SerializerMethodField()
    purse_type = serializers.SerializerMethodField()

    class Meta:
        model = Auction
        fields = [
            "id",
            "auction_id",
            "name",
            "manager",
            "manager_name",
            "manager_username",
            "manager_password",
            "auction_type",
            "number_of_teams",
            "allotted_to_user",
            "payment_status",
            "status",
            "logo_url",
            "unit",
            "purse_amount",
            "purse",
            "purse_type",
            "bid_increment",
            "timer_duration",
            "minimum_players_per_team",
            "maximum_players_per_team",
            "current_player",
            "sold_animation_state",
            "live_revision",
            "team_count",
            "player_count",
            "setup_enabled",
            "sponsors",
            "settings",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "auction_id", "current_player", "live_revision", "created_at", "updated_at"]

    def get_setup_enabled(self, obj) -> bool:
        return bool(obj.pk)

    def get_purse(self, obj):
        return str(obj.purse_amount)

    def get_purse_type(self, obj):
        return obj.unit

    def to_internal_value(self, data):
        if hasattr(data, "copy"):
            data = data.copy()
        if "purse" in data and "purse_amount" not in data:
            data["purse_amount"] = data["purse"]
        if "purse_type" in data and "unit" not in data:
            data["unit"] = data["purse_type"]
        return super().to_internal_value(data)

    @transaction.atomic
    def create(self, validated_data):
        username = validated_data.pop("manager_username", "").strip()
        password = validated_data.pop("manager_password", "").strip()
        manager = None
        if username:
            manager, _ = User.objects.get_or_create(username=username)
            if password:
                manager.set_password(password)
            elif not manager.has_usable_password():
                manager.set_password(User.objects.make_random_password())
            manager.save()

        auction = Auction.objects.create(manager=manager, **validated_data)
        AuctionSettings.objects.create(auction=auction)

        if manager:
            RoleProfile.objects.update_or_create(
                user=manager,
                defaults={
                    "role": RoleProfile.Role.AUCTION_MANAGER,
                    "assigned_auction": auction,
                    "team": None,
                },
            )
        return auction

    @transaction.atomic
    def update(self, instance, validated_data):
        username = validated_data.pop("manager_username", "").strip()
        password = validated_data.pop("manager_password", "").strip()
        purse_was_updated = "purse_amount" in validated_data

        if username:
            manager, _ = User.objects.get_or_create(username=username)
            if password:
                manager.set_password(password)
            elif not manager.has_usable_password():
                manager.set_password(User.objects.make_random_password())
            manager.save()
            instance.manager = manager

        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.save()

        if purse_was_updated:
            self._sync_team_purses(instance)

        return instance

    def _sync_team_purses(self, auction: Auction) -> None:
        for team in auction.teams.prefetch_related("sold_players"):
            sold_total = sum((sold.sold_price for sold in team.sold_players.all()), Decimal("0"))
            team.purse_amount = auction.purse_amount
            team.remaining_purse = auction.purse_amount - sold_total
            team.save(update_fields=["purse_amount", "remaining_purse"])


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = [
            "id",
            "auction",
            "category_id",
            "name",
            "minimum_players",
            "maximum_players",
            "base_value",
            "color",
            "status",
        ]
        read_only_fields = ["id", "category_id"]


class TeamCategoryLimitSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_base_value = serializers.DecimalField(source="category.base_value", max_digits=14, decimal_places=2, read_only=True)
    bought_count = serializers.SerializerMethodField()
    remaining_slots = serializers.SerializerMethodField()
    required_points = serializers.SerializerMethodField()

    class Meta:
        model = TeamCategoryLimit
        fields = [
            "id",
            "team",
            "category",
            "category_name",
            "category_base_value",
            "maximum_players",
            "bought_count",
            "remaining_slots",
            "required_points",
        ]
        read_only_fields = ["id", "team", "category_name", "category_base_value", "bought_count", "remaining_slots", "required_points"]

    def get_bought_count(self, obj):
        prefetched_players = getattr(obj.team, "_prefetched_objects_cache", {}).get("players")
        if prefetched_players is not None:
            return sum(1 for player in prefetched_players if player.category_id == obj.category_id)
        return obj.team.players.filter(category=obj.category).count()

    def get_remaining_slots(self, obj):
        return max(obj.maximum_players - self.get_bought_count(obj), 0)

    def get_required_points(self, obj):
        return str(obj.category.base_value * self.get_remaining_slots(obj))


class TeamSerializer(serializers.ModelSerializer):
    owner_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    owner_user_id = serializers.IntegerField(source="owner_user.id", read_only=True)
    purse_amount = serializers.DecimalField(source="auction.purse_amount", max_digits=14, decimal_places=2, read_only=True)
    purse_type = serializers.CharField(source="auction.unit", read_only=True)
    category_limits = TeamCategoryLimitSerializer(many=True, read_only=True)
    required_points = serializers.SerializerMethodField()
    budget_left_after_required = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = [
            "id",
            "auction",
            "team_id",
            "name",
            "short_name",
            "logo_url",
            "owner_name",
            "owner_username",
            "owner_password",
            "owner_user_id",
            "purse_amount",
            "purse_type",
            "remaining_purse",
            "players_bought",
            "maximum_players",
            "category_limits",
            "required_points",
            "budget_left_after_required",
            "status",
        ]
        read_only_fields = [
            "id",
            "team_id",
            "owner_user_id",
            "purse_amount",
            "purse_type",
            "remaining_purse",
            "players_bought",
            "category_limits",
            "required_points",
            "budget_left_after_required",
        ]

    def _required_points(self, team: Team) -> Decimal:
        total = Decimal("0")
        cache = getattr(team, "_prefetched_objects_cache", {})
        limits = cache.get("category_limits")
        if limits is None:
            limits = team.category_limits.select_related("category")
        prefetched_players = cache.get("players")
        for limit in limits:
            if prefetched_players is None:
                bought_count = team.players.filter(category=limit.category).count()
            else:
                bought_count = sum(1 for player in prefetched_players if player.category_id == limit.category_id)
            total += limit.category.base_value * max(limit.maximum_players - bought_count, 0)
        return total

    def get_required_points(self, team: Team):
        return str(self._required_points(team))

    def get_budget_left_after_required(self, team: Team):
        return str(team.remaining_purse - self._required_points(team))

    @transaction.atomic
    def create(self, validated_data):
        password = validated_data.pop("owner_password", "").strip()
        auction = validated_data.get("auction")
        if auction:
            validated_data["purse_amount"] = auction.purse_amount
            validated_data["remaining_purse"] = auction.purse_amount
        team = Team.objects.create(**validated_data)
        self._sync_owner(team, password)
        return team

    @transaction.atomic
    def update(self, instance, validated_data):
        password = validated_data.pop("owner_password", "").strip()
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.save()
        self._sync_owner(instance, password)
        return instance

    def _sync_owner(self, team: Team, password: str) -> None:
        if not team.owner_username:
            return
        user, _ = User.objects.get_or_create(username=team.owner_username)
        user.first_name = team.owner_name
        if password:
            user.set_password(password)
        elif not user.has_usable_password():
            user.set_password(User.objects.make_random_password())
        user.save()
        team.owner_user = user
        team.save(update_fields=["owner_user"])
        TeamOwner.objects.update_or_create(
            team=team,
            defaults={
                "user": user,
                "auction": team.auction,
                "owner_name": team.owner_name or team.owner_username,
                "username": team.owner_username,
            },
        )
        RoleProfile.objects.update_or_create(
            user=user,
            defaults={
                "role": RoleProfile.Role.TEAM_OWNER,
                "assigned_auction": None,
                "team": team,
            },
        )


class TeamOwnerSerializer(serializers.ModelSerializer):
    team_name = serializers.CharField(source="team.name", read_only=True)
    team_short_name = serializers.CharField(source="team.short_name", read_only=True)

    class Meta:
        model = TeamOwner
        fields = ["id", "auction", "team", "team_name", "team_short_name", "owner_name", "username", "created_at"]
        read_only_fields = ["id", "created_at"]


class PlayerSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    sold_team_name = serializers.CharField(source="sold_team.name", read_only=True)
    role = serializers.ChoiceField(choices=Player.Role.choices, required=False, allow_blank=True)

    class Meta:
        model = Player
        fields = [
            "id",
            "auction",
            "player_id",
            "first_name",
            "last_name",
            "full_name",
            "category",
            "category_name",
            "image_url",
            "role",
            "country",
            "age",
            "base_price",
            "extra_field_1",
            "extra_field_2",
            "extra_field_3",
            "extra_field_4",
            "status",
            "sold_team",
            "sold_team_name",
            "sold_price",
            "queue_order",
        ]
        read_only_fields = ["id", "player_id", "full_name", "sold_team", "sold_price"]


class BidSerializer(serializers.ModelSerializer):
    player_name = serializers.CharField(source="player.full_name", read_only=True)
    player_image_url = serializers.CharField(source="player.image_url", read_only=True)
    player_category = serializers.SerializerMethodField()
    team_name = serializers.CharField(source="team.name", read_only=True)
    team_short_name = serializers.CharField(source="team.short_name", read_only=True)
    team_logo_url = serializers.CharField(source="team.logo_url", read_only=True)
    owner_name = serializers.CharField(source="team.owner_name", read_only=True)
    team_limit_reached = serializers.SerializerMethodField()
    team_limit_message = serializers.SerializerMethodField()

    class Meta:
        model = Bid
        fields = [
            "id",
            "auction",
            "player",
            "player_name",
            "player_image_url",
            "player_category",
            "team",
            "team_name",
            "team_short_name",
            "team_logo_url",
            "owner_name",
            "bid_amount",
            "bid_type",
            "bid_status",
            "created_at",
            "approved_by_admin",
            "approved_at",
            "team_limit_reached",
            "team_limit_message",
        ]
        read_only_fields = ["id", "created_at", "approved_by_admin", "approved_at", "team_limit_reached", "team_limit_message"]

    def get_team_limit_reached(self, obj):
        return bool(self._team_limit_message(obj))

    def get_team_limit_message(self, obj):
        return self._team_limit_message(obj)

    def get_player_category(self, obj):
        return obj.player.category.name if obj.player and obj.player.category else ""

    def _team_limit_message(self, obj):
        player = obj.player
        team = obj.team
        if team.maximum_players and team.players.count() >= team.maximum_players:
            return f"{team.short_name} already has the maximum squad of {team.maximum_players} players."
        if not player.category_id:
            return ""
        limit = team.category_limits.filter(category=player.category).first()
        if not limit or limit.maximum_players == 0:
            return ""
        bought_count = team.players.filter(category=player.category).count()
        if bought_count >= limit.maximum_players:
            return (
                f"Category limit reached: {team.short_name} already has the maximum players "
                f"for {player.category.name} ({bought_count}/{limit.maximum_players})."
            )
        return ""


class SoldPlayerSerializer(serializers.ModelSerializer):
    player_name = serializers.CharField(source="player.full_name", read_only=True)
    player_image_url = serializers.CharField(source="player.image_url", read_only=True)
    player_role = serializers.CharField(source="player.role", read_only=True)
    player_category = serializers.SerializerMethodField()
    team_name = serializers.CharField(source="team.name", read_only=True)
    team_short_name = serializers.CharField(source="team.short_name", read_only=True)
    team_logo_url = serializers.CharField(source="team.logo_url", read_only=True)

    class Meta:
        model = SoldPlayer
        fields = [
            "id",
            "auction",
            "player",
            "player_name",
            "player_image_url",
            "player_role",
            "player_category",
            "team",
            "team_name",
            "team_short_name",
            "team_logo_url",
            "sold_price",
            "sold_time",
        ]
        read_only_fields = ["id", "sold_time"]

    def get_player_category(self, obj):
        return obj.player.category.name if obj.player and obj.player.category else ""


class AuctionLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = AuctionLog
        fields = ["id", "auction", "actor", "actor_name", "action", "message", "metadata", "created_at"]


class LiveStateSerializer(serializers.Serializer):
    auction = AuctionSerializer()
    current_player = PlayerSerializer(allow_null=True)
    teams = TeamSerializer(many=True)
    current_bid = BidSerializer(allow_null=True)
    current_player_bids = BidSerializer(many=True)
    pending_bids = BidSerializer(many=True)
    bid_feed = BidSerializer(many=True)
    sold_players = SoldPlayerSerializer(many=True)
    results = serializers.DictField()


def decimal_to_float(value) -> float:
    if value is None:
        return 0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)
