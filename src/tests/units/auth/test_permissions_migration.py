"""Verify the historical Stellar baseline independently of the live model."""

from datetime import datetime
from importlib import import_module

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from src.modules.auth.hashers import PBKDF2PasswordHasher


def test_baseline_bootstraps_admin_and_sets_disabled_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = import_module("src.migrations.versions.0001_stellar_baseline")
    monkeypatch.setenv("ADMIN_USERNAME", "initial_operator")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-only-bootstrap-password")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()

        assert set(sa.inspect(connection).get_table_names()) == {"users", "tokens"}
        users = sa.Table("users", sa.MetaData(), autoload_with=connection)
        admin = connection.execute(sa.select(users)).mappings().one()
        assert admin["username"] == "initial_operator"
        assert admin["is_admin"] and admin["is_active"]
        assert PBKDF2PasswordHasher().verify("test-only-bootstrap-password", admin["password"])[0]

        result = connection.execute(
            users.insert().values(username="operator", password="hash", created_at=datetime.now())
        )
        assert result.inserted_primary_key is not None
        member = (
            connection.execute(sa.select(users).where(users.c.id == result.inserted_primary_key[0]))
            .mappings()
            .one()
        )
        assert not member["is_admin"]
        assert not member["can_access_admin"] and not member["can_access_system_api"]

        migration.downgrade()
        assert sa.inspect(connection).get_table_names() == []
    engine.dispose()


def test_baseline_requires_a_bootstrap_password(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = import_module("src.migrations.versions.0001_stellar_baseline")
    monkeypatch.delenv("ADMIN_PASSWORD")
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        migration.upgrade()
