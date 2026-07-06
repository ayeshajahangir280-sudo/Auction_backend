from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AuctionLogViewSet,
    AuctionViewSet,
    BidViewSet,
    CategoryViewSet,
    CurrentUserView,
    ImageUploadView,
    LoginView,
    PlayerViewSet,
    SoldPlayerViewSet,
    SponsorViewSet,
    TeamOwnerViewSet,
    TeamViewSet,
    UserViewSet,
)

router = DefaultRouter()
router.register("auctions", AuctionViewSet, basename="auction")
router.register("categories", CategoryViewSet, basename="category")
router.register("players", PlayerViewSet, basename="player")
router.register("teams", TeamViewSet, basename="team")
router.register("team-owners", TeamOwnerViewSet, basename="team-owner")
router.register("sponsors", SponsorViewSet, basename="sponsor")
router.register("bids", BidViewSet, basename="bid")
router.register("sold-players", SoldPlayerViewSet, basename="sold-player")
router.register("logs", AuctionLogViewSet, basename="auction-log")
router.register("users", UserViewSet, basename="user")

urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/me/", CurrentUserView.as_view(), name="current-user"),
    path("uploads/image/", ImageUploadView.as_view(), name="image-upload"),
    path("", include(router.urls)),
]
