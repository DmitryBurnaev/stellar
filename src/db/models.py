from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, relationship, Mapped, mapped_column, backref

from src.utils import utcnow
from src.modules.auth.hashers import PBKDF2PasswordHasher


class BaseModel(AsyncAttrs, DeclarativeBase):
    id: Mapped[int]


class User(BaseModel):
    """Users table for an authorization process"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(sa.String(128), unique=True)
    password: Mapped[str] = mapped_column(sa.String(128))
    email: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    is_admin: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())
    can_access_admin: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    can_access_system_api: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    can_view_inventory: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, server_default=sa.false()
    )
    can_manage_inventory: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, server_default=sa.false()
    )
    can_run_server_checks: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, server_default=sa.false()
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    @classmethod
    def make_password(cls, raw_password: str) -> str:
        hasher = PBKDF2PasswordHasher()
        return hasher.encode(raw_password)

    def verify_password(self, raw_password: str) -> bool:
        hasher = PBKDF2PasswordHasher()
        verified, _ = hasher.verify(raw_password, encoded=str(self.password))
        return verified

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return self.username

    def __str__(self) -> str:
        return f"User '{self.username}'"

    def __repr__(self) -> str:
        return (
            f"User("
            f"login='{self.username}', "
            f"email='{self.email}', "
            f"is_active='{self.is_active}', "
            f"is_admin='{self.is_admin}')"
        )


class Token(BaseModel):
    """Simple token storage for authorizing and API usages"""

    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(sa.String(128))
    token: Mapped[str] = mapped_column(sa.String(512), unique=True)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True, onupdate=utcnow)

    # relations
    user: Mapped[User] = relationship(
        User,
        backref=backref("tokens", cascade="all, delete-orphan"),
        lazy="joined",
    )

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return (
            f"Token("
            f"id={self.id}, "
            f"name={self.name}, "
            f"user_id={self.user_id}, "
            f"token='[MASKED]', "
            f"expires_at={self.expires_at}, "
            f"created_at={self.created_at}, "
            f"updated_at={self.updated_at}"
            f")"
        )


server_groups = sa.Table(
    "server_groups",
    BaseModel.metadata,
    sa.Column("server_id", sa.ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("group_id", sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)
server_applications = sa.Table(
    "server_applications",
    BaseModel.metadata,
    sa.Column("server_id", sa.ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True),
    sa.Column(
        "application_id", sa.ForeignKey("applications.id", ondelete="RESTRICT"), primary_key=True
    ),
)


class Provider(BaseModel):
    __tablename__ = "providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(128), unique=True)
    description: Mapped[str | None] = mapped_column(sa.Text)

    def __str__(self) -> str:
        return self.name


class Application(BaseModel):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(128), unique=True)
    description: Mapped[str | None] = mapped_column(sa.Text)

    def __str__(self) -> str:
        return self.name


class ServerGroup(BaseModel):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(128), unique=True)
    description: Mapped[str | None] = mapped_column(sa.Text)
    servers: Mapped[list["Server"]] = relationship(secondary=server_groups, back_populates="groups")

    def __str__(self) -> str:
        return self.name


class Server(BaseModel):
    __tablename__ = "servers"
    id: Mapped[int] = mapped_column(primary_key=True)
    ip_address: Mapped[str] = mapped_column(sa.String(45), unique=True)
    scan_address: Mapped[str | None] = mapped_column(sa.String(45))
    ssh_alias: Mapped[str] = mapped_column(sa.String(255))
    provider_id: Mapped[int] = mapped_column(sa.ForeignKey("providers.id", ondelete="RESTRICT"))
    os_name: Mapped[str | None] = mapped_column(sa.String(128))
    os_version: Mapped[str | None] = mapped_column(sa.String(128))
    last_updated_at: Mapped[datetime | None]
    provider: Mapped[Provider] = relationship(lazy="joined")
    domains: Mapped[list["ServerDomain"]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )
    groups: Mapped[list[ServerGroup]] = relationship(
        secondary=server_groups, back_populates="servers"
    )
    applications: Mapped[list[Application]] = relationship(secondary=server_applications)

    def __str__(self) -> str:
        return self.ip_address


class ServerDomain(BaseModel):
    __tablename__ = "server_domains"
    __table_args__ = (sa.UniqueConstraint("server_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(sa.ForeignKey("servers.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(sa.String(253))
    server: Mapped[Server] = relationship(back_populates="domains")

    def __str__(self) -> str:
        return self.name


class ServerCheck(BaseModel):
    """A completed attempt, including partial results and safe diagnostics."""

    __tablename__ = "server_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(
        sa.ForeignKey("servers.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(32))
    status: Mapped[str] = mapped_column(sa.String(32))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    result: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    error: Mapped[str | None] = mapped_column(sa.Text)


class FavoriteComponent(BaseModel):
    __tablename__ = "favorite_components"
    __table_args__ = (sa.UniqueConstraint("server_id", "kind", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(
        sa.ForeignKey("servers.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(16))
    name: Mapped[str] = mapped_column(sa.String(512))
