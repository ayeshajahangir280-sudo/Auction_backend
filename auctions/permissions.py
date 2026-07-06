from rest_framework import permissions

from .models import RoleProfile


def get_profile(user):
    if not user or not user.is_authenticated:
        return None
    return getattr(user, "role_profile", None)


def is_super_admin(user) -> bool:
    profile = get_profile(user)
    return bool(user and user.is_authenticated and (user.is_superuser or (profile and profile.role == RoleProfile.Role.SUPER_ADMIN)))


def is_auction_manager(user) -> bool:
    profile = get_profile(user)
    return bool(profile and profile.role == RoleProfile.Role.AUCTION_MANAGER)


def is_team_owner(user) -> bool:
    profile = get_profile(user)
    return bool(profile and profile.role == RoleProfile.Role.TEAM_OWNER)


def scoped_auction_for_user(user):
    profile = get_profile(user)
    if not profile:
        return None
    if profile.role == RoleProfile.Role.AUCTION_MANAGER:
        return profile.assigned_auction
    if profile.role == RoleProfile.Role.TEAM_OWNER and profile.team:
        return profile.team.auction
    return None


class IsSuperAdmin(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        return is_super_admin(request.user)


class IsAuctionStaff(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        return is_super_admin(request.user) or is_auction_manager(request.user)


class IsTeamOwnerRole(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        return is_team_owner(request.user)
