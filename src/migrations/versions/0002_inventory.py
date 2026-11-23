"""Add server inventory and feature permissions."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("can_view_inventory", "can_manage_inventory", "can_run_server_checks"):
        op.add_column(
            "users", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
        )
    for name in ("providers", "applications", "groups"):
        op.create_table(
            name,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(128), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
        )
    op.create_table(
        "servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ip_address", sa.String(45), nullable=False, unique=True),
        sa.Column("scan_address", sa.String(45)),
        sa.Column("ssh_alias", sa.String(255), nullable=False),
        sa.Column(
            "provider_id",
            sa.Integer(),
            sa.ForeignKey("providers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("os_name", sa.String(128)),
        sa.Column("os_version", sa.String(128)),
        sa.Column("last_updated_at", sa.DateTime()),
    )
    op.create_table(
        "server_domains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "server_id",
            sa.Integer(),
            sa.ForeignKey("servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(253), nullable=False),
        sa.UniqueConstraint("server_id", "name"),
    )
    for table, column, target in (
        ("server_groups", "group_id", "groups.id"),
        ("server_applications", "application_id", "applications.id"),
    ):
        op.create_table(
            table,
            sa.Column(
                "server_id",
                sa.Integer(),
                sa.ForeignKey("servers.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                column,
                sa.Integer(),
                sa.ForeignKey(
                    target, ondelete="RESTRICT" if table == "server_applications" else "CASCADE"
                ),
                primary_key=True,
            ),
        )
    op.create_table(
        "server_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "server_id",
            sa.Integer(),
            sa.ForeignKey("servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_server_checks_server_id", "server_checks", ["server_id"])
    op.create_table(
        "favorite_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "server_id",
            sa.Integer(),
            sa.ForeignKey("servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.UniqueConstraint("server_id", "kind", "name"),
    )
    op.create_index("ix_favorite_components_server_id", "favorite_components", ["server_id"])


def downgrade() -> None:
    for table in (
        "favorite_components",
        "server_checks",
        "server_applications",
        "server_groups",
        "server_domains",
        "servers",
        "groups",
        "applications",
        "providers",
    ):
        op.drop_table(table)
    for name in ("can_run_server_checks", "can_manage_inventory", "can_view_inventory"):
        op.drop_column("users", name)
