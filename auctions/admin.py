from django.contrib import admin

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
    TeamOwner,
)


@admin.register(Auction)
class AuctionAdmin(admin.ModelAdmin):
    list_display = ("auction_id", "name", "auction_type", "status", "payment_status", "number_of_teams")
    search_fields = ("auction_id", "name", "allotted_to_user")
    list_filter = ("auction_type", "status", "payment_status")


admin.site.register(RoleProfile)
admin.site.register(AuctionSettings)
admin.site.register(Sponsor)
admin.site.register(Category)
admin.site.register(Player)
admin.site.register(Team)
admin.site.register(TeamOwner)
admin.site.register(Bid)
admin.site.register(SoldPlayer)
admin.site.register(AuctionLog)
