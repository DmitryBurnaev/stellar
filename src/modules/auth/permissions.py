"""Feature permissions shared by API, admin, and browser authorization."""

from enum import StrEnum
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from typing import Protocol


class Permission(StrEnum):
    ADMIN_PANEL = "can_access_admin"
    SYSTEM_API = "can_access_system_api"
    INVENTORY = "can_view_inventory"
    INVENTORY_ADMIN = "can_manage_inventory"
    SERVER_CHECKS = "can_run_server_checks"


@dataclass(frozen=True)
class PermissionDefinition:
    label: str
    description: str


PERMISSION_DEFINITIONS: Mapping[Permission, PermissionDefinition] = MappingProxyType(
    {
        Permission.ADMIN_PANEL: PermissionDefinition(
            "Admin", "Manage users, permissions, and API tokens."
        ),
        Permission.SYSTEM_API: PermissionDefinition(
            "System", "Access the protected system health endpoint."
        ),
        Permission.INVENTORY: PermissionDefinition("Inventory", "View servers and their status."),
        Permission.INVENTORY_ADMIN: PermissionDefinition(
            "Manage inventory", "Manage servers, catalogs, groups and favorites."
        ),
        Permission.SERVER_CHECKS: PermissionDefinition(
            "Server checks", "Run server and external port checks."
        ),
    }
)


class PermissionUser(Protocol):
    is_active: bool
    is_admin: bool


def has_permission(user: PermissionUser | None, permission: Permission) -> bool:
    """Active superusers pass every check; other users need the explicit flag."""
    return (
        user is not None
        and user.is_active is True
        and (user.is_admin is True or getattr(user, permission.value, False) is True)
    )
