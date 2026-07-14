import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def make_code(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


class UploadedImage(models.Model):
    path = models.CharField(max_length=255, unique=True)
    content_type = models.CharField(max_length=80, default="image/jpeg")
    data = models.BinaryField()
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.path


class RoleProfile(models.Model):
    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "Super Admin"
        AUCTION_MANAGER = "auction_manager", "Auction Manager"
        TEAM_OWNER = "team_owner", "Team Owner"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="role_profile")
    role = models.CharField(max_length=32, choices=Role.choices)
    assigned_auction = models.ForeignKey(
        "Auction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_profiles",
    )
    team = models.OneToOneField(
        "Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owner_profile",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.user.username} - {self.get_role_display()}"


class Auction(models.Model):
    class AuctionType(models.TextChoices):
        IN_PERSON = "in_person", "Auction In Person"
        ONLINE = "online", "Online Auction"
        HYBRID = "hybrid", "Hybrid Auction"

    class PaymentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SETUP = "setup", "Setup"
        ACTIVE = "active", "Active"
        LIVE = "live", "Live"
        COMPLETED = "completed", "Completed"
        ARCHIVED = "archived", "Archived"

    auction_id = models.CharField(max_length=24, unique=True, editable=False)
    name = models.CharField(max_length=180)
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_auctions",
    )
    auction_type = models.CharField(max_length=24, choices=AuctionType.choices, default=AuctionType.IN_PERSON)
    number_of_teams = models.PositiveIntegerField(default=0)
    allotted_to_user = models.CharField(max_length=150, blank=True)
    payment_status = models.CharField(max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    logo_url = models.URLField(blank=True)
    unit = models.CharField(max_length=24, blank=True, default="")
    purse_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bid_increment = models.DecimalField(max_digits=14, decimal_places=2, default=1)
    timer_duration = models.PositiveIntegerField(default=30)
    minimum_players_per_team = models.PositiveIntegerField(default=0)
    maximum_players_per_team = models.PositiveIntegerField(default=0)
    current_player = models.ForeignKey(
        "Player",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    sold_animation_state = models.BooleanField(default=False)
    live_revision = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-updated_at", "-created_at"], name="auction_status_updated_idx"),
        ]

    def save(self, *args, **kwargs) -> None:
        if not self.auction_id:
            self.auction_id = make_code("AUC")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class AuctionSettings(models.Model):
    auction = models.OneToOneField(Auction, on_delete=models.CASCADE, related_name="settings")
    show_remaining_purse = models.BooleanField(default=True)
    require_bid_approval = models.BooleanField(default=True)
    enable_owner_bidding = models.BooleanField(default=False)
    auto_advance_after_sale = models.BooleanField(default=False)
    sponsor_rotation_seconds = models.PositiveIntegerField(default=8)
    public_screen_theme = models.CharField(max_length=32, default="purple_glow")
    notes = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"Settings - {self.auction.name}"


class Sponsor(models.Model):
    auction = models.ForeignKey(Auction, on_delete=models.CASCADE, related_name="sponsors")
    name = models.CharField(max_length=120)
    logo_url = models.URLField(blank=True)
    status = models.CharField(max_length=16, default="active")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["auction", "status", "sort_order"], name="sponsor_auc_status_idx"),
        ]

    def __str__(self) -> str:
        return self.name


class Category(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    auction = models.ForeignKey(Auction, on_delete=models.CASCADE, related_name="categories")
    category_id = models.CharField(max_length=24, editable=False)
    name = models.CharField(max_length=120)
    minimum_players = models.PositiveIntegerField(default=0)
    maximum_players = models.PositiveIntegerField(default=0)
    base_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    color = models.CharField(max_length=32, default="#7C3AED")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["auction", "status"], name="category_auc_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["auction", "category_id"], name="unique_category_code_per_auction"),
            models.UniqueConstraint(fields=["auction", "name"], name="unique_category_name_per_auction"),
        ]

    def save(self, *args, **kwargs) -> None:
        if not self.category_id:
            self.category_id = make_code("CAT")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.auction.name} - {self.name}"


class Team(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DRAFT = "draft", "Draft"
        SUSPENDED = "suspended", "Suspended"

    auction = models.ForeignKey(Auction, on_delete=models.CASCADE, related_name="teams")
    team_id = models.CharField(max_length=24, editable=False)
    name = models.CharField(max_length=140)
    short_name = models.CharField(max_length=16)
    logo_url = models.URLField(blank=True)
    owner_name = models.CharField(max_length=140, blank=True)
    owner_username = models.CharField(max_length=150, blank=True)
    owner_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_team",
    )
    purse_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    remaining_purse = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    players_bought = models.PositiveIntegerField(default=0)
    maximum_players = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["auction", "status"], name="team_auction_status_idx"),
            models.Index(fields=["auction", "name"], name="team_auction_name_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["auction", "team_id"], name="unique_team_code_per_auction"),
            models.UniqueConstraint(fields=["auction", "short_name"], name="unique_team_short_per_auction"),
        ]

    def save(self, *args, **kwargs) -> None:
        if not self.team_id:
            self.team_id = make_code("TEAM")
        if self.remaining_purse == 0 and self.purse_amount:
            self.remaining_purse = self.purse_amount
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.short_name} - {self.name}"


class TeamCategoryLimit(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="category_limits")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="team_limits")
    maximum_players = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["category__name"]
        constraints = [
            models.UniqueConstraint(fields=["team", "category"], name="unique_team_category_limit"),
        ]

    def __str__(self) -> str:
        return f"{self.team.short_name} - {self.category.name}: {self.maximum_players}"


class TeamOwner(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_owner")
    auction = models.ForeignKey(Auction, on_delete=models.CASCADE, related_name="team_owners")
    team = models.OneToOneField(Team, on_delete=models.CASCADE, related_name="team_owner_record")
    owner_name = models.CharField(max_length=140)
    username = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["owner_name"]

    def __str__(self) -> str:
        return f"{self.owner_name} - {self.team.short_name}"


class Player(models.Model):
    class Role(models.TextChoices):
        BATTER = "Batter", "Batter"
        BOWLER = "Bowler", "Bowler"
        ALL_ROUNDER = "All-rounder", "All-rounder"
        WICKET_KEEPER = "Wicket-keeper", "Wicket-keeper"

    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        IN_AUCTION = "in_auction", "In Auction"
        SOLD = "sold", "Sold"
        UNSOLD = "unsold", "Unsold"

    auction = models.ForeignKey(Auction, on_delete=models.CASCADE, related_name="players")
    player_id = models.CharField(max_length=24, editable=False)
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80, blank=True)
    full_name = models.CharField(max_length=180, blank=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="players")
    image_url = models.URLField(blank=True)
    role = models.CharField(max_length=32, choices=Role.choices, blank=True, default="")
    country = models.CharField(max_length=80, blank=True)
    age = models.PositiveIntegerField(default=0)
    base_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    extra_field_1 = models.CharField(max_length=160, blank=True)
    extra_field_2 = models.CharField(max_length=160, blank=True)
    extra_field_3 = models.CharField(max_length=160, blank=True)
    extra_field_4 = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.AVAILABLE)
    sold_team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="players")
    sold_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    queue_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["queue_order", "id"]
        indexes = [
            models.Index(fields=["auction", "status", "queue_order", "id"], name="player_auc_status_queue_idx"),
            models.Index(fields=["auction", "sold_team", "status"], name="player_auc_sold_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["auction", "player_id"], name="unique_player_code_per_auction"),
        ]

    def save(self, *args, **kwargs) -> None:
        if not self.player_id:
            self.player_id = make_code("PLY")
        self.full_name = f"{self.first_name} {self.last_name}".strip()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.full_name


class Bid(models.Model):
    class BidType(models.TextChoices):
        MANUAL = "manual", "Manual"
        TEAM_OWNER = "team_owner", "Team Owner"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    auction = models.ForeignKey(Auction, on_delete=models.CASCADE, related_name="bids")
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="bids")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="bids")
    bid_amount = models.DecimalField(max_digits=14, decimal_places=2)
    bid_type = models.CharField(max_length=16, choices=BidType.choices)
    bid_status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_bids",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["auction", "player", "bid_status", "-bid_amount", "-created_at"], name="bid_auc_player_status_idx"),
            models.Index(fields=["auction", "bid_status", "-created_at"], name="bid_auc_status_created_idx"),
        ]

    def approve(self, user) -> None:
        self.bid_status = self.Status.APPROVED
        self.approved_by_admin = user
        self.approved_at = timezone.now()
        self.save(update_fields=["bid_status", "approved_by_admin", "approved_at"])

    def reject(self, user) -> None:
        self.bid_status = self.Status.REJECTED
        self.approved_by_admin = user
        self.approved_at = timezone.now()
        self.save(update_fields=["bid_status", "approved_by_admin", "approved_at"])

    def __str__(self) -> str:
        return f"{self.team.short_name} {self.bid_amount} for {self.player.full_name}"


class SoldPlayer(models.Model):
    auction = models.ForeignKey(Auction, on_delete=models.CASCADE, related_name="sold_players")
    player = models.OneToOneField(Player, on_delete=models.CASCADE, related_name="sold_record")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="sold_players")
    sold_price = models.DecimalField(max_digits=14, decimal_places=2)
    sold_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sold_time"]
        indexes = [
            models.Index(fields=["auction", "team", "-sold_time"], name="sold_auc_team_time_idx"),
            models.Index(fields=["auction", "-sold_price"], name="sold_auc_price_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.player.full_name} sold to {self.team.short_name}"


class AuctionLog(models.Model):
    auction = models.ForeignKey(Auction, on_delete=models.CASCADE, related_name="logs")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=80)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["auction", "-created_at"], name="log_auc_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action} - {self.auction.name}"
