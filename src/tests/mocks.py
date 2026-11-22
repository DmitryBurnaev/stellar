import dataclasses
from dataclasses import field
from unittest.mock import MagicMock


@dataclasses.dataclass
class MockUser:
    id: int
    is_active: bool = False
    username: str = "test-user"
    is_admin: bool = False
    can_access_admin: bool = False
    can_access_system_api: bool = False
    email: str | None = None
    verify_password: MagicMock = field(default_factory=lambda: MagicMock(return_value=True))


@dataclasses.dataclass
class MockAPIToken:
    is_active: bool
    user: MockUser
