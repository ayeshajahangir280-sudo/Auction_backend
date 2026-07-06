import csv
import re
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib.auth import authenticate, get_user_model
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Count, F, Sum
from openpyxl import load_workbook
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Auction, AuctionLog, Bid, Category, Player, RoleProfile, SoldPlayer, Sponsor, Team, TeamCategoryLimit, TeamOwner
from .permissions import is_auction_manager, is_super_admin, is_team_owner, scoped_auction_for_user
from .serializers import (
    AuctionLogSerializer,
    AuctionSerializer,
    BidSerializer,
    CategorySerializer,
    PlayerSerializer,
    SoldPlayerSerializer,
    SponsorSerializer,
    TeamOwnerSerializer,
    TeamSerializer,
    UserSummarySerializer,
)

User = get_user_model()

PLAYER_IMPORT_HEADER_ALIASES = {
    "first": "first_name",
    "firstname": "first_name",
    "first_name": "first_name",
    "last": "last_name",
    "lastname": "last_name",
    "last_name": "last_name",
    "name": "full_name",
    "fullname": "full_name",
    "full_name": "full_name",
    "playername": "full_name",
    "player_name": "full_name",
    "category": "category",
    "categoryname": "category",
    "category_name": "category",
    "image": "image_url",
    "image_link": "image_url",
    "imagelink": "image_url",
    "imageurl": "image_url",
    "image_url": "image_url",
    "image_urls": "image_url",
    "img": "image_url",
    "imgurl": "image_url",
    "img_url": "image_url",
    "photo": "image_url",
    "photo_url": "image_url",
    "photourl": "image_url",
    "picture": "image_url",
    "picture_url": "image_url",
    "profile": "image_url",
    "profile_image": "image_url",
    "profile_photo": "image_url",
    "playerimage": "image_url",
    "player_image": "image_url",
    "player_photo": "image_url",
    "player_picture": "image_url",
    "fileurl": "image_url",
    "file_url": "image_url",
    "uploadurl": "image_url",
    "upload_url": "image_url",
    "attachmenturl": "image_url",
    "attachment_url": "image_url",
    "photo_file_url": "image_url",
    "role": "role",
    "type": "role",
    "playerrole": "role",
    "player_role": "role",
    "country": "country",
    "nation": "country",
    "age": "age",
    "baseprice": "base_price",
    "base_price": "base_price",
    "basevalue": "base_price",
    "base_value": "base_price",
    "price": "base_price",
    "order": "queue_order",
    "queueorder": "queue_order",
    "queue_order": "queue_order",
    "srno": "queue_order",
    "sr_no": "queue_order",
}

PLAYER_ROLE_ALIASES = {
    "bat": Player.Role.BATTER,
    "batter": Player.Role.BATTER,
    "bowler": Player.Role.BOWLER,
    "allrounder": Player.Role.ALL_ROUNDER,
    "all_rounder": Player.Role.ALL_ROUNDER,
    "wicketkeeper": Player.Role.WICKET_KEEPER,
    "wicket_keeper": Player.Role.WICKET_KEEPER,
    "keeper": Player.Role.WICKET_KEEPER,
}


def clean_import_cell(value) -> str:
    if hasattr(value, "value"):
        value = value.value
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def clean_import_url(value) -> str:
    text = clean_import_cell(value)
    hyperlink = getattr(value, "hyperlink", None)
    if hyperlink and getattr(hyperlink, "target", None):
        text = clean_import_cell(hyperlink.target)
    if text.lower().startswith("www."):
        return f"https://{text}"
    return text


def normalize_import_key(value) -> str:
    return re.sub(r"[^a-z0-9]+", "_", clean_import_cell(value).lower()).strip("_")


def canonical_import_key(value) -> str | None:
    key = normalize_import_key(value)
    return PLAYER_IMPORT_HEADER_ALIASES.get(key) or PLAYER_IMPORT_HEADER_ALIASES.get(key.replace("_", ""))


def parse_import_decimal(value, default: str = "0") -> Decimal:
    text = clean_import_cell(value).replace(",", "")
    if not text:
        text = default
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("Base price must be a number.") from exc


def parse_import_int(value, default: int = 0) -> int:
    text = clean_import_cell(value)
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError as exc:
        raise ValueError("Age and order must be numbers.") from exc


def resolve_import_role(value) -> str:
    text = normalize_import_key(value)
    if not text:
        return ""
    role = PLAYER_ROLE_ALIASES.get(text) or PLAYER_ROLE_ALIASES.get(text.replace("_", ""))
    if not role:
        raise ValueError(f"Role '{clean_import_cell(value)}' is not valid.")
    return role


def resolve_import_category(auction: Auction, value):
    text = clean_import_cell(value)
    if not text:
        return None
    category = auction.categories.filter(category_id__iexact=text).first() or auction.categories.filter(name__iexact=text).first()
    if not category and text.isdigit():
        category = auction.categories.filter(pk=int(text)).first()
    if not category:
        raise ValueError(f"Category '{text}' was not found.")
    return category


def user_can_access_auction(user, auction: Auction) -> bool:
    if is_super_admin(user):
        return True
    scoped = scoped_auction_for_user(user)
    return bool(scoped and scoped.pk == auction.pk)


def require_auction_staff(user) -> None:
    if not (is_super_admin(user) or is_auction_manager(user)):
        raise PermissionDenied("Only Super Admin or the assigned auction manager can perform this action.")


def highest_approved_bid(auction: Auction, player: Player | None = None):
    qs = auction.bids.filter(bid_status=Bid.Status.APPROVED)
    if player:
        qs = qs.filter(player=player)
    return qs.order_by("-bid_amount", "-approved_at", "-created_at").first()


def remaining_required_points(team: Team) -> Decimal:
    total = Decimal("0")
    for limit in team.category_limits.select_related("category"):
        bought_count = team.players.filter(category=limit.category).count()
        total += limit.category.base_value * max(limit.maximum_players - bought_count, 0)
    return total


def validate_team_category_limit(team: Team, player: Player) -> None:
    if not player.category_id:
        return
    limit = team.category_limits.filter(category=player.category).first()
    if not limit or limit.maximum_players == 0:
        return
    bought_count = team.players.filter(category=player.category).count()
    if bought_count >= limit.maximum_players:
        raise ValidationError({"category": f"{team.short_name} already has the maximum players for {player.category.name}."})


def validate_team_player_limits(team: Team, player: Player) -> None:
    if team.maximum_players and team.players.count() >= team.maximum_players:
        raise ValidationError({"team": f"{team.short_name} already has the maximum squad of {team.maximum_players} players."})
    validate_team_category_limit(team, player)


def build_results(auction: Auction) -> dict:
    sold_qs = auction.sold_players.select_related("player", "team")
    sold_count = sold_qs.count()
    total_spend = sold_qs.aggregate(total=Sum("sold_price"))["total"] or Decimal("0")
    top_sale = sold_qs.order_by("-sold_price").first()
    teams = []
    for team in auction.teams.annotate(roster_count=Count("players")).order_by("name"):
        required = remaining_required_points(team)
        teams.append(
            {
                "team_id": team.team_id,
                "name": team.name,
                "short_name": team.short_name,
                "remaining_purse": team.remaining_purse,
                "players_bought": team.players_bought,
                "maximum_players": team.maximum_players,
                "required_points": required,
                "budget_left_after_required": team.remaining_purse - required,
            }
        )
    return {
        "sold_count": sold_count,
        "unsold_count": auction.players.filter(status=Player.Status.UNSOLD).count(),
        "available_count": auction.players.filter(status=Player.Status.AVAILABLE).count(),
        "total_spend": total_spend,
        "top_sale": SoldPlayerSerializer(top_sale).data if top_sale else None,
        "teams": teams,
    }


def serialize_live_state(auction: Auction, team_scope: Team | None = None) -> dict:
    current_player = auction.current_player
    current_player_bids = (
        auction.bids.select_related("player", "team")
        .filter(player=current_player, bid_status=Bid.Status.APPROVED)
        .order_by("-bid_amount", "-approved_at", "-created_at")
        if current_player
        else Bid.objects.none()
    )
    bid_feed = auction.bids.select_related("player", "team").filter(bid_status=Bid.Status.APPROVED)[:20]
    pending_qs = auction.bids.select_related("player", "team").filter(bid_status=Bid.Status.PENDING)
    teams_qs = auction.teams.all()
    sold_players_qs = auction.sold_players.select_related("player", "team")
    results = build_results(auction)
    if team_scope:
        pending_qs = pending_qs.filter(team=team_scope)
        teams_qs = teams_qs.filter(pk=team_scope.pk)
        sold_players_qs = sold_players_qs.filter(team=team_scope)
        results["teams"] = [team for team in results["teams"] if team["team_id"] == team_scope.team_id]
        if results["top_sale"] and results["top_sale"]["team"] != team_scope.pk:
            results["top_sale"] = None
    pending = pending_qs[:20]
    return {
        "auction": AuctionSerializer(auction).data,
        "current_player": PlayerSerializer(current_player).data if current_player else None,
        "teams": TeamSerializer(teams_qs, many=True).data,
        "current_bid": BidSerializer(highest_approved_bid(auction, current_player)).data if current_player and highest_approved_bid(auction, current_player) else None,
        "current_player_bids": BidSerializer(current_player_bids, many=True).data,
        "pending_bids": BidSerializer(pending, many=True).data,
        "bid_feed": BidSerializer(bid_feed, many=True).data,
        "sold_players": SoldPlayerSerializer(sold_players_qs[:10], many=True).data,
        "results": results,
    }


def advance_to_random_player(auction: Auction, actor=None) -> Player | None:
    auction.players.filter(status=Player.Status.IN_AUCTION).update(status=Player.Status.AVAILABLE)
    next_player = auction.players.filter(status=Player.Status.AVAILABLE).order_by("?").first()
    if not next_player:
        auction.current_player = None
        auction.sold_animation_state = False
        auction.save(update_fields=["current_player", "sold_animation_state"])
        return None

    next_player.status = Player.Status.IN_AUCTION
    next_player.save(update_fields=["status"])
    auction.current_player = next_player
    auction.status = Auction.Status.LIVE
    auction.sold_animation_state = False
    auction.save(update_fields=["current_player", "status", "sold_animation_state"])
    AuctionLog.objects.create(
        auction=auction,
        actor=actor,
        action="auction.next_player",
        message=f"{next_player.full_name} moved to the block randomly.",
    )
    return next_player


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get("username", "").strip()
        password = request.data.get("password", "")
        user = authenticate(username=username, password=password)
        if not user:
            return Response({"detail": "Invalid username or password."}, status=status.HTTP_401_UNAUTHORIZED)
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSummarySerializer(user).data,
            }
        )


class CurrentUserView(APIView):
    def get(self, request):
        return Response(UserSummarySerializer(request.user).data)


class ImageUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            raise ValidationError({"file": "Choose an image file to upload."})

        content_type = getattr(uploaded_file, "content_type", "")
        if not content_type.startswith("image/"):
            raise ValidationError({"file": "Only image files can be uploaded."})

        suffix = Path(uploaded_file.name).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg"}:
            suffix = ".jpg"
        saved_path = default_storage.save(f"uploads/images/{uuid.uuid4().hex}{suffix}", uploaded_file)
        file_url = default_storage.url(saved_path)
        absolute_url = request.build_absolute_uri(file_url if file_url.startswith("/") else f"/{file_url}")
        return Response({"url": absolute_url, "path": saved_path}, status=status.HTTP_201_CREATED)


class UserViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = UserSummarySerializer

    def get_queryset(self):
        qs = User.objects.select_related("role_profile", "role_profile__assigned_auction", "role_profile__team").order_by("username")
        if is_super_admin(self.request.user):
            role = self.request.query_params.get("role")
            if role:
                qs = qs.filter(role_profile__role=role)
            return qs
        return qs.filter(pk=self.request.user.pk)


class ScopedModelViewSet(viewsets.ModelViewSet):
    auction_field = "auction"

    def get_queryset(self):
        qs = super().get_queryset()
        if is_super_admin(self.request.user):
            auction_id = self.request.query_params.get("auction")
            return qs.filter(auction__auction_id=auction_id) if auction_id else qs
        if is_team_owner(self.request.user):
            return qs.none()
        auction = scoped_auction_for_user(self.request.user)
        if not auction:
            return qs.none()
        return qs.filter(auction=auction)

    def perform_create(self, serializer):
        require_auction_staff(self.request.user)
        auction = serializer.validated_data.get("auction") or scoped_auction_for_user(self.request.user)
        if not auction:
            raise PermissionDenied("Choose an auction or log in as an assigned auction user.")
        if not user_can_access_auction(self.request.user, auction):
            raise PermissionDenied("You cannot write data for this auction.")
        serializer.save(auction=auction)

    def perform_update(self, serializer):
        require_auction_staff(self.request.user)
        auction = serializer.validated_data.get("auction") or getattr(serializer.instance, "auction", None)
        if auction and not user_can_access_auction(self.request.user, auction):
            raise PermissionDenied("You cannot write data for this auction.")
        serializer.save()

    def perform_destroy(self, instance):
        require_auction_staff(self.request.user)
        auction = getattr(instance, "auction", None)
        if auction and not user_can_access_auction(self.request.user, auction):
            raise PermissionDenied("You cannot delete data for this auction.")
        instance.delete()


class AuctionViewSet(viewsets.ModelViewSet):
    queryset = Auction.objects.select_related("manager", "current_player").prefetch_related("sponsors", "teams", "players")
    serializer_class = AuctionSerializer
    lookup_field = "auction_id"

    def get_permissions(self):
        public_actions = {"public_active", "public_live", "projector"}
        if self.action in public_actions:
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action in {"public_active", "public_live", "projector"}:
            return qs
        if is_super_admin(self.request.user):
            return qs
        if is_team_owner(self.request.user) and self.action not in {"live_state", "team_owner_bid", "team_roster"}:
            return qs.none()
        auction = scoped_auction_for_user(self.request.user)
        return qs.filter(pk=auction.pk) if auction else qs.none()

    def perform_create(self, serializer):
        if not is_super_admin(self.request.user):
            raise PermissionDenied("Only Super Admin can create auctions.")
        auction = serializer.save()
        AuctionLog.objects.create(
            auction=auction,
            actor=self.request.user,
            action="auction.created",
            message="Auction project was created.",
        )

    def perform_update(self, serializer):
        require_auction_staff(self.request.user)
        if not user_can_access_auction(self.request.user, serializer.instance):
            raise PermissionDenied("You cannot update this auction.")
        serializer.save()

    def perform_destroy(self, instance):
        require_auction_staff(self.request.user)
        if not user_can_access_auction(self.request.user, instance):
            raise PermissionDenied("You cannot delete this auction.")
        instance.delete()

    @action(detail=False, methods=["get"], url_path="manager-dashboard")
    def manager_dashboard(self, request):
        auction = scoped_auction_for_user(request.user)
        if not auction and is_super_admin(request.user):
            auction = self.get_queryset().first()
        if not auction:
            return Response({"detail": "No assigned auction found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "auction": AuctionSerializer(auction).data,
                "category_count": auction.categories.count(),
                "player_count": auction.players.count(),
                "team_count": auction.teams.count(),
                "pending_bid_count": auction.bids.filter(bid_status=Bid.Status.PENDING).count(),
                "sold_count": auction.sold_players.count(),
                "results": build_results(auction),
            }
        )

    @action(detail=True, methods=["get"], url_path="live-state")
    def live_state(self, request, auction_id=None):
        auction = self.get_object()
        team_scope = request.user.role_profile.team if is_team_owner(request.user) else None
        return Response(serialize_live_state(auction, team_scope=team_scope))

    @action(detail=True, methods=["get"], url_path="current-player")
    def current_player(self, request, auction_id=None):
        auction = self.get_object()
        return Response(PlayerSerializer(auction.current_player).data if auction.current_player else None)

    @action(detail=True, methods=["post"], url_path="start-auction")
    def start_auction(self, request, auction_id=None):
        require_auction_staff(request.user)
        auction = self.get_object()
        if not auction.players.filter(status=Player.Status.AVAILABLE).exists() and not auction.current_player:
            raise ValidationError({"players": "Add available players before starting the auction."})
        if auction.current_player:
            auction.status = Auction.Status.LIVE
            auction.save(update_fields=["status"])
        else:
            if not advance_to_random_player(auction, request.user):
                raise ValidationError({"players": "No available players left to start."})
        AuctionLog.objects.create(
            auction=auction,
            actor=request.user,
            action="auction.started",
            message="Auction was started.",
        )
        return Response(serialize_live_state(auction))

    @action(detail=True, methods=["post"], url_path="set-current-player")
    def set_current_player(self, request, auction_id=None):
        require_auction_staff(request.user)
        auction = self.get_object()
        player_code = request.data.get("player_id") or request.data.get("player")
        player = auction.players.filter(player_id=player_code).first() or auction.players.filter(pk=player_code).first()
        if not player:
            raise ValidationError({"player_id": "Player not found in this auction."})
        auction.players.filter(status=Player.Status.IN_AUCTION).update(status=Player.Status.AVAILABLE)
        player.status = Player.Status.IN_AUCTION
        player.save(update_fields=["status"])
        auction.current_player = player
        auction.status = Auction.Status.LIVE
        auction.sold_animation_state = False
        auction.save(update_fields=["current_player", "status", "sold_animation_state"])
        AuctionLog.objects.create(auction=auction, actor=request.user, action="auction.current_player", message=f"{player.full_name} is now live.")
        return Response(serialize_live_state(auction))

    @action(detail=True, methods=["post"], url_path="manual-bid")
    def manual_bid(self, request, auction_id=None):
        auction = self.get_object()
        require_auction_staff(request.user)
        return self._create_bid(request, auction, Bid.BidType.MANUAL)

    @action(detail=True, methods=["post"], url_path="team-owner-bid")
    def team_owner_bid(self, request, auction_id=None):
        if not is_team_owner(request.user):
            raise PermissionDenied("Only team owners can bid from the owner screen.")
        auction = self.get_object()
        return self._create_bid(request, auction, Bid.BidType.TEAM_OWNER)

    def _create_bid(self, request, auction: Auction, bid_type: str):
        player = auction.current_player
        if not player:
            raise ValidationError({"player": "Set a current player before bidding."})
        team_value = request.data.get("team") or request.data.get("team_id")
        if is_team_owner(request.user):
            profile = request.user.role_profile
            team = profile.team
        else:
            team = auction.teams.filter(team_id=team_value).first() or auction.teams.filter(pk=team_value).first()
        if not team or team.auction_id != auction.pk:
            raise ValidationError({"team": "Team not found in this auction."})
        validate_team_player_limits(team, player)
        amount = Decimal(str(request.data.get("bid_amount") or request.data.get("amount") or "0"))
        if amount > team.remaining_purse:
            raise ValidationError({"bid_amount": "Team does not have enough remaining points."})
        current = highest_approved_bid(auction, player)
        minimum = (current.bid_amount if current else player.base_price) + (auction.bid_increment if current else Decimal("0"))
        if amount < minimum:
            raise ValidationError({"bid_amount": f"Bid must be at least {minimum}."})
        bid = Bid.objects.create(
            auction=auction,
            player=player,
            team=team,
            bid_amount=amount,
            bid_type=bid_type,
            bid_status=Bid.Status.PENDING,
        )
        AuctionLog.objects.create(
            auction=auction,
            actor=request.user,
            action="bid.pending",
            message=f"{team.short_name} bid {amount} for {player.full_name}.",
        )
        return Response(BidSerializer(bid).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="pending-bids")
    def pending_bids(self, request, auction_id=None):
        require_auction_staff(request.user)
        auction = self.get_object()
        bids = auction.bids.select_related("player", "team").filter(bid_status=Bid.Status.PENDING)
        return Response(BidSerializer(bids, many=True).data)

    @action(detail=True, methods=["post"], url_path=r"bids/(?P<bid_pk>[^/.]+)/approve")
    def approve_bid(self, request, auction_id=None, bid_pk=None):
        require_auction_staff(request.user)
        auction = self.get_object()
        bid = auction.bids.get(pk=bid_pk)
        bid.approve(request.user)
        AuctionLog.objects.create(auction=auction, actor=request.user, action="bid.approved", message=f"Approved {bid.team.short_name} bid.")
        return Response(serialize_live_state(auction))

    @action(detail=True, methods=["post"], url_path=r"bids/(?P<bid_pk>[^/.]+)/reject")
    def reject_bid(self, request, auction_id=None, bid_pk=None):
        require_auction_staff(request.user)
        auction = self.get_object()
        bid = auction.bids.get(pk=bid_pk)
        bid.reject(request.user)
        AuctionLog.objects.create(auction=auction, actor=request.user, action="bid.rejected", message=f"Rejected {bid.team.short_name} bid.")
        return Response(BidSerializer(bid).data)

    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="sell-player")
    def sell_player(self, request, auction_id=None):
        require_auction_staff(request.user)
        auction = self.get_object()
        player = auction.current_player
        if not player:
            raise ValidationError({"player": "No current player selected."})
        winning_bid = highest_approved_bid(auction, player)
        team_value = request.data.get("team") or request.data.get("team_id")
        team = None
        if team_value:
            team = auction.teams.filter(team_id=team_value).first() or auction.teams.filter(pk=team_value).first()
        if not team and winning_bid:
            team = winning_bid.team
        if not team:
            raise ValidationError({"team": "Approve a bid or choose a winning team."})
        validate_team_player_limits(team, player)
        sold_price = Decimal(str(request.data.get("sold_price") or request.data.get("amount") or (winning_bid.bid_amount if winning_bid else player.base_price)))
        if sold_price > team.remaining_purse:
            raise ValidationError({"sold_price": "Winning team does not have enough remaining purse."})

        player.status = Player.Status.SOLD
        player.sold_team = team
        player.sold_price = sold_price
        player.save(update_fields=["status", "sold_team", "sold_price"])
        team.remaining_purse -= sold_price
        team.players_bought += 1
        team.save(update_fields=["remaining_purse", "players_bought"])
        SoldPlayer.objects.update_or_create(
            auction=auction,
            player=player,
            defaults={"team": team, "sold_price": sold_price},
        )
        AuctionLog.objects.create(auction=auction, actor=request.user, action="player.sold", message=f"{player.full_name} sold to {team.short_name}.")
        advance_to_random_player(auction, request.user)
        return Response(serialize_live_state(auction))

    @action(detail=True, methods=["post"], url_path="mark-unsold")
    def mark_unsold(self, request, auction_id=None):
        require_auction_staff(request.user)
        auction = self.get_object()
        player = auction.current_player
        if not player:
            raise ValidationError({"player": "No current player selected."})
        player.status = Player.Status.UNSOLD
        player.save(update_fields=["status"])
        AuctionLog.objects.create(auction=auction, actor=request.user, action="player.unsold", message=f"{player.full_name} marked unsold.")
        advance_to_random_player(auction, request.user)
        return Response(serialize_live_state(auction))

    @action(detail=True, methods=["post"], url_path="next-player")
    def next_player(self, request, auction_id=None):
        require_auction_staff(request.user)
        auction = self.get_object()
        advance_to_random_player(auction, request.user)
        return Response(serialize_live_state(auction))

    @action(detail=True, methods=["get"], url_path="team-roster")
    def team_roster(self, request, auction_id=None):
        auction = self.get_object()
        if is_team_owner(request.user):
            team = request.user.role_profile.team
            if not team or team.auction_id != auction.pk:
                raise PermissionDenied("This owner is not assigned to a team in this auction.")
        else:
            team_value = request.query_params.get("team") or request.query_params.get("team_id")
            team = auction.teams.filter(team_id=team_value).first() or auction.teams.filter(pk=team_value).first()
        if not team:
            raise ValidationError({"team": "Team is required."})
        players = auction.players.filter(sold_team=team).select_related("category", "sold_team")
        return Response({"team": TeamSerializer(team).data, "players": PlayerSerializer(players, many=True).data})

    @action(detail=True, methods=["get"], url_path="sold-players")
    def sold_players(self, request, auction_id=None):
        auction = self.get_object()
        return Response(SoldPlayerSerializer(auction.sold_players.select_related("player", "team"), many=True).data)

    @action(detail=True, methods=["get"], url_path="results")
    def results(self, request, auction_id=None):
        return Response(build_results(self.get_object()))

    @action(detail=True, methods=["get"], url_path="public-live")
    def public_live(self, request, auction_id=None):
        return Response(serialize_live_state(self.get_object()))

    @action(detail=False, methods=["get"], url_path="public-active")
    def public_active(self, request):
        auction = (
            self.get_queryset()
            .filter(status__in=[Auction.Status.LIVE, Auction.Status.ACTIVE, Auction.Status.SETUP])
            .order_by("-updated_at", "-created_at")
            .first()
        )
        if not auction:
            return Response({"detail": "No active auction is available."}, status=status.HTTP_404_NOT_FOUND)
        return Response(serialize_live_state(auction))

    @action(detail=True, methods=["get"], url_path="projector")
    def projector(self, request, auction_id=None):
        state = serialize_live_state(self.get_object())
        state["mode"] = "projector"
        return Response(state)


class CategoryViewSet(ScopedModelViewSet):
    queryset = Category.objects.select_related("auction")
    serializer_class = CategorySerializer

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self.sync_category_players(serializer.instance)

    def perform_update(self, serializer):
        max_players = serializer.validated_data.get("maximum_players", serializer.instance.maximum_players)
        if max_players and serializer.instance.players.count() > max_players:
            raise ValidationError({"maximum_players": "This category already has more selected players than this max."})
        super().perform_update(serializer)
        self.sync_category_players(serializer.instance)

    def sync_category_players(self, category):
        category.players.update(base_price=category.base_value)

    def perform_destroy(self, instance):
        require_auction_staff(self.request.user)
        if not user_can_access_auction(self.request.user, instance.auction):
            raise PermissionDenied("You cannot delete this category.")
        instance.players.update(category=None, base_price=0)
        instance.delete()

    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="assign-players")
    def assign_players(self, request, pk=None):
        require_auction_staff(request.user)
        category = self.get_object()
        raw_player_ids = request.data.get("player_ids", [])
        if not isinstance(raw_player_ids, list):
            raise ValidationError({"player_ids": "Send player_ids as a list."})
        try:
            player_ids = [int(player_id) for player_id in raw_player_ids]
        except (TypeError, ValueError) as exc:
            raise ValidationError({"player_ids": "Every player id must be a number."}) from exc

        selected_players = category.auction.players.filter(pk__in=player_ids)
        found_ids = set(selected_players.values_list("id", flat=True))
        missing_ids = sorted(set(player_ids) - found_ids)
        if missing_ids:
            raise ValidationError({"player_ids": f"Players not found for this auction: {missing_ids}."})
        if category.maximum_players and len(found_ids) > category.maximum_players:
            raise ValidationError({"player_ids": f"Select at most {category.maximum_players} players for {category.name}."})

        removed_count = category.players.exclude(pk__in=found_ids).update(category=None, base_price=0)
        assigned_count = selected_players.update(category=category, base_price=category.base_value)
        AuctionLog.objects.create(
            auction=category.auction,
            actor=request.user,
            action="category.assign_players",
            message=f"Assigned {assigned_count} players to {category.name}.",
            metadata={"category_id": category.category_id, "assigned_count": assigned_count, "removed_count": removed_count},
        )
        return Response(
            {
                "category": CategorySerializer(category).data,
                "assigned_count": assigned_count,
                "removed_count": removed_count,
            }
        )


class PlayerViewSet(ScopedModelViewSet):
    queryset = Player.objects.select_related("auction", "category", "sold_team")
    serializer_class = PlayerSerializer

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self.sync_player_base_price(serializer.instance)

    def perform_update(self, serializer):
        super().perform_update(serializer)
        self.sync_player_base_price(serializer.instance)

    def sync_player_base_price(self, player):
        base_price = player.category.base_value if player.category else Decimal("0")
        if player.base_price != base_price:
            player.base_price = base_price
            player.save(update_fields=["base_price"])

    @transaction.atomic
    @action(detail=False, methods=["delete"], url_path="bulk-delete")
    def bulk_delete(self, request):
        require_auction_staff(request.user)
        auction_value = request.data.get("auction") or request.query_params.get("auction")
        auction = None
        if auction_value:
            auction = Auction.objects.filter(auction_id=auction_value).first()
            if not auction and str(auction_value).isdigit():
                auction = Auction.objects.filter(pk=int(auction_value)).first()
        if not auction:
            auction = scoped_auction_for_user(request.user)
        if not auction:
            raise ValidationError({"auction": "Choose an auction before deleting players."})
        if not user_can_access_auction(request.user, auction):
            raise PermissionDenied("You cannot delete players for this auction.")

        player_count = auction.players.count()
        auction.current_player = None
        auction.sold_animation_state = False
        auction.save(update_fields=["current_player", "sold_animation_state"])
        auction.players.all().delete()
        auction.teams.update(players_bought=0, remaining_purse=F("purse_amount"))
        AuctionLog.objects.create(
            auction=auction,
            actor=request.user,
            action="players.bulk_delete",
            message=f"Deleted {player_count} players.",
            metadata={"deleted_count": player_count},
        )
        return Response({"deleted_count": player_count})

    @action(detail=False, methods=["post"], url_path="import-excel")
    def import_excel(self, request):
        require_auction_staff(request.user)
        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            raise ValidationError({"file": "Choose an Excel or CSV file to upload."})

        suffix = Path(uploaded_file.name).suffix.lower()
        if suffix not in {".xlsx", ".xlsm", ".csv"}:
            raise ValidationError({"file": "Upload an .xlsx, .xlsm, or .csv file."})

        auction_value = request.data.get("auction") or request.query_params.get("auction")
        auction = None
        if auction_value:
            auction = Auction.objects.filter(pk=auction_value).first() or Auction.objects.filter(auction_id=auction_value).first()
        if not auction:
            auction = scoped_auction_for_user(request.user)
        if not auction:
            raise ValidationError({"auction": "Choose an auction before importing players."})
        if not user_can_access_auction(request.user, auction):
            raise PermissionDenied("You cannot import players for this auction.")

        workbook = None
        try:
            if suffix == ".csv":
                raw_file = uploaded_file.read()
                try:
                    csv_text = raw_file.decode("utf-8-sig")
                except UnicodeDecodeError:
                    csv_text = raw_file.decode("latin-1")
                rows = csv.reader(csv_text.splitlines())
            else:
                try:
                    workbook = load_workbook(uploaded_file, read_only=False, data_only=True)
                except Exception as exc:
                    raise ValidationError({"file": "The Excel file could not be read."}) from exc
                sheet = workbook.active
                rows = sheet.iter_rows()

            header = next(rows, None)
            if not header:
                raise ValidationError({"file": "The import file is empty."})

            columns = {}
            for index, heading in enumerate(header):
                key = canonical_import_key(heading)
                if key and key not in columns:
                    columns[key] = index

            if "first_name" not in columns and "full_name" not in columns:
                raise ValidationError({"file": "Add a Name or First Name column to the Excel sheet."})

            def row_value(row, key):
                index = columns.get(key)
                if index is None or index >= len(row):
                    return ""
                return row[index]

            created_players = []
            errors = []
            for row_number, row in enumerate(rows, start=2):
                if not any(clean_import_cell(cell) for cell in row):
                    continue
                try:
                    full_name = clean_import_cell(row_value(row, "full_name"))
                    first_name = clean_import_cell(row_value(row, "first_name"))
                    last_name = clean_import_cell(row_value(row, "last_name"))
                    if full_name and not first_name:
                        parts = full_name.split()
                        first_name = parts[0]
                        last_name = " ".join(parts[1:])
                    if not first_name:
                        raise ValueError("Name or First Name is required.")
                    category = resolve_import_category(auction, row_value(row, "category"))

                    player = Player.objects.create(
                        auction=auction,
                        first_name=first_name[:80],
                        last_name=last_name[:80],
                        category=category,
                        image_url=clean_import_url(row_value(row, "image_url")),
                        role=resolve_import_role(row_value(row, "role")),
                        country=clean_import_cell(row_value(row, "country"))[:80],
                        age=max(parse_import_int(row_value(row, "age"), 0), 0),
                        base_price=category.base_value if category else Decimal("0"),
                        queue_order=max(parse_import_int(row_value(row, "queue_order"), 0), 0),
                    )
                    created_players.append(player)
                except ValueError as exc:
                    errors.append({"row": row_number, "message": str(exc)})

            AuctionLog.objects.create(
                auction=auction,
                actor=request.user,
                action="players.import",
                message=f"Imported {len(created_players)} players from import file.",
                metadata={"file": uploaded_file.name, "created_count": len(created_players), "error_count": len(errors)},
            )
            return Response(
                {
                    "created_count": len(created_players),
                    "error_count": len(errors),
                    "errors": errors[:50],
                    "players": PlayerSerializer(created_players[:25], many=True).data,
                },
                status=status.HTTP_201_CREATED,
            )
        finally:
            if workbook:
                workbook.close()


class TeamViewSet(ScopedModelViewSet):
    queryset = Team.objects.select_related("auction", "owner_user")
    serializer_class = TeamSerializer

    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="category-limits")
    def category_limits(self, request, pk=None):
        require_auction_staff(request.user)
        team = self.get_object()
        raw_limits = request.data.get("limits", [])
        if not isinstance(raw_limits, list):
            raise ValidationError({"limits": "Send limits as a list."})

        seen_categories = set()
        total_maximum_players = 0
        for item in raw_limits:
            if not isinstance(item, dict):
                raise ValidationError({"limits": "Every limit must be an object."})
            category_value = item.get("category")
            maximum_value = item.get("maximum_players", 0)
            try:
                maximum_players = max(int(maximum_value or 0), 0)
            except (TypeError, ValueError) as exc:
                raise ValidationError({"maximum_players": "Max players must be a number."}) from exc
            category = team.auction.categories.filter(category_id=category_value).first()
            if not category and str(category_value).isdigit():
                category = team.auction.categories.filter(pk=int(category_value)).first()
            if not category:
                raise ValidationError({"category": "Category not found for this auction."})
            if category.pk in seen_categories:
                raise ValidationError({"category": f"{category.name} was included more than once."})
            bought_count = team.players.filter(category=category).count()
            if maximum_players < bought_count:
                raise ValidationError(
                    {
                        "maximum_players": (
                            f"{team.short_name} already has {bought_count} players in {category.name}. "
                            "Max players for this category cannot be lower than that."
                        )
                    }
                )
            seen_categories.add(category.pk)
            total_maximum_players += maximum_players
            if maximum_players:
                TeamCategoryLimit.objects.update_or_create(
                    team=team,
                    category=category,
                    defaults={"maximum_players": maximum_players},
                )
            else:
                TeamCategoryLimit.objects.filter(team=team, category=category).delete()

        if team.maximum_players and total_maximum_players > team.maximum_players:
            raise ValidationError(
                {
                    "limits": (
                        f"Category limits total {total_maximum_players}, but {team.short_name}'s squad maximum "
                        f"is {team.maximum_players}."
                    )
                }
            )

        TeamCategoryLimit.objects.filter(team=team).exclude(category_id__in=seen_categories).delete()
        AuctionLog.objects.create(
            auction=team.auction,
            actor=request.user,
            action="team.category_limits",
            message=f"Updated category limits for {team.short_name}.",
        )
        return Response(TeamSerializer(team).data)

    def perform_destroy(self, instance):
        require_auction_staff(self.request.user)
        if not user_can_access_auction(self.request.user, instance.auction):
            raise PermissionDenied("You cannot delete this team.")
        owner_user = instance.owner_user
        if owner_user and hasattr(owner_user, "role_profile"):
            owner_user.role_profile.delete()
        instance.delete()


class TeamOwnerViewSet(ScopedModelViewSet):
    queryset = TeamOwner.objects.select_related("auction", "team", "user")
    serializer_class = TeamOwnerSerializer


class SponsorViewSet(ScopedModelViewSet):
    queryset = Sponsor.objects.select_related("auction")
    serializer_class = SponsorSerializer


class BidViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Bid.objects.select_related("auction", "player", "team")
    serializer_class = BidSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if is_super_admin(self.request.user):
            return qs
        auction = scoped_auction_for_user(self.request.user)
        if not auction:
            return qs.none()
        if is_team_owner(self.request.user):
            return qs.filter(auction=auction, team=self.request.user.role_profile.team)
        return qs.filter(auction=auction)


class SoldPlayerViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = SoldPlayer.objects.select_related("auction", "player", "team")
    serializer_class = SoldPlayerSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if is_super_admin(self.request.user):
            return qs
        auction = scoped_auction_for_user(self.request.user)
        return qs.filter(auction=auction) if auction else qs.none()


class AuctionLogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = AuctionLog.objects.select_related("auction", "actor")
    serializer_class = AuctionLogSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if is_super_admin(self.request.user):
            return qs
        auction = scoped_auction_for_user(self.request.user)
        return qs.filter(auction=auction) if auction else qs.none()
