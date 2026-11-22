"""Create Stellar users, feature grants, API tokens, and the first administrator."""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from src.modules.auth.hashers import PBKDF2PasswordHasher
from src.settings.app import AdminSettings

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    admin = AdminSettings()
    password = admin.password.get_secret_value() if admin.password else ""
    if not admin.username.strip() or not password:
        raise RuntimeError("Set ADMIN_USERNAME and ADMIN_PASSWORD before the first migration")

    users = op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(128), nullable=False, unique=True),
        sa.Column("password", sa.String(128), nullable=False),
        sa.Column("email", sa.String(128), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("can_access_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("can_access_system_api", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("token", sa.String(512), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.bulk_insert(
        users,
        [
            {
                "username": admin.username,
                "password": PBKDF2PasswordHasher().encode(password),
                "is_admin": True,
                "created_at": datetime.now(UTC).replace(tzinfo=None),
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("tokens")
    op.drop_table("users")
