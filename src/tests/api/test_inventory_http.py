"""Exercise inventory routes against an isolated PostgreSQL schema."""

import os
import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application import StellarAPP
from src.db import session as db_session
from src.db.models import BaseModel, FavoriteComponent, Provider, Server, ServerCheck, User
from src.modules.web import auth, views
from src.modules.ssh import SSHError
from src.modules.port_scanner import ScannerError
from src.settings.db import get_db_settings


@pytest.fixture
async def inventory_database(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker, None]:
    if os.getenv("TEST_POSTGRES_MIGRATIONS") != "1":
        pytest.skip("Set TEST_POSTGRES_MIGRATIONS=1 for PostgreSQL HTTP tests")
    schema = f"inventory_test_{uuid4().hex}"
    admin_engine = create_async_engine(get_db_settings().database_dsn)
    async with admin_engine.begin() as connection:
        await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_async_engine(
        get_db_settings().database_dsn, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(BaseModel.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(db_session, "get_session_factory", lambda: factory)
        yield factory
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        await admin_engine.dispose()


@pytest.mark.postgresql
async def test_inventory_permissions_checks_and_favorites(
    inventory_database: async_sessionmaker, test_app: StellarAPP, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with inventory_database() as session:
        provider = Provider(name="Example")
        server = Server(ip_address="192.0.2.1", ssh_alias="example", provider=provider)
        session.add(server)
        await session.commit()
        server_id = server.id
    user = User(username="operator", password="unused", is_active=True, is_admin=False)
    monkeypatch.setattr(auth, "get_current_web_user", AsyncMock(return_value=user))
    monkeypatch.setattr(
        views,
        "collect_server",
        AsyncMock(
            return_value={
                "available": True,
                "sections": {
                    "os": {"PRETTY_NAME": "Debian"},
                    "packages": [{"name": "curl", "version": "1", "candidate": "2"}],
                    "updates_known": True,
                    "images": [],
                },
                "errors": {},
            }
        ),
    )
    monkeypatch.setattr(views, "submit_scan", AsyncMock(return_value="scan-1"))
    monkeypatch.setattr(
        views,
        "get_scan",
        AsyncMock(
            return_value={
                "id": "scan-1",
                "address": "192.0.2.1",
                "status": "succeeded",
                "open_tcp_ports": [22, 443],
                "scanner": "test-vps",
            }
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app),
        base_url="https://testserver",
        headers={"Origin": "https://testserver"},
        follow_redirects=False,
    ) as client:
        assert (await client.get("/servers")).status_code == 403
        user.can_view_inventory = True
        assert "192.0.2.1" in (await client.get("/")).text
        assert (await client.get("/servers?group=&q=192.0.2")).status_code == 200
        assert "192.0.2.1" in (await client.get("/servers?availability=unknown&group=0")).text
        assert (await client.post(f"/servers/{server_id}/check")).status_code == 403
        user.can_run_server_checks = True
        assert (await client.post(f"/servers/{server_id}/check")).status_code == 303
        assert "192.0.2.1" in (await client.get("/servers?availability=available")).text
        assert (
            await client.post(
                f"/servers/{server_id}/favorites",
                data={"kind": "package", "name": "curl", "favorite": "1"},
            )
        ).status_code == 403
        user.can_manage_inventory = True
        assert (
            await client.post(
                f"/servers/{server_id}/favorites",
                data={"kind": "package", "name": "unknown", "favorite": "1"},
            )
        ).status_code == 400
        assert (
            await client.post(
                f"/servers/{server_id}/favorites",
                data={"kind": "package", "name": "curl", "favorite": "1"},
            )
        ).status_code == 303
        assert (await client.post(f"/servers/{server_id}/ports")).status_code == 303
        assert (await client.post(f"/servers/{server_id}/ports")).status_code == 303
        detail = await client.get(f"/servers/{server_id}")
        assert detail.status_code == 200
        assert "Debian" in detail.text and "curl" in detail.text and "443" in detail.text
        monkeypatch.setattr(
            views, "collect_server", AsyncMock(side_effect=SSHError("SSH connection was refused"))
        )
        failed = await client.post(
            f"/servers/{server_id}/check", data={"return_query": "q=curl&group=0"}
        )
        assert "return_query=q%3Dcurl%26group%3D0" in failed.headers["location"]
        detail = await client.get(f"/servers/{server_id}")
        assert "SSH connection was refused" in detail.text and "curl" in detail.text
        retry = AsyncMock(side_effect=[ScannerError("Could not start the port scan"), "scan-2"])
        monkeypatch.setattr(views, "submit_scan", retry)
        assert (await client.post(f"/servers/{server_id}/ports")).status_code == 303
        assert (await client.post(f"/servers/{server_id}/ports")).status_code == 303
        assert retry.await_args_list[0].args[1] == retry.await_args_list[1].args[1]
        monkeypatch.setattr(
            views,
            "get_scan",
            AsyncMock(
                return_value={"status": "succeeded", "open_tcp_ports": [], "scanner": "test-vps"}
            ),
        )
        assert (await client.post(f"/servers/{server_id}/ports")).status_code == 303
        assert "No open TCP ports found" in (await client.get(f"/servers/{server_id}")).text
        user.can_view_inventory = False
        assert (await client.get(f"/servers/{server_id}")).status_code == 403
        user.is_admin = True
        assert (await client.get(f"/servers/{server_id}")).status_code == 200
    async with inventory_database() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(FavoriteComponent)) == 1
        checks = (await session.scalars(sa.select(ServerCheck).order_by(ServerCheck.id))).all()
        assert [check.status for check in checks] == [
            "succeeded",
            "succeeded",
            "failed",
            "succeeded",
        ]


@pytest.mark.postgresql
async def test_simultaneous_checks_for_one_server_are_serialized(
    inventory_database: async_sessionmaker, test_app: StellarAPP, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with inventory_database() as session:
        server = Server(ip_address="192.0.2.2", ssh_alias="example", provider=Provider(name="Test"))
        session.add(server)
        await session.commit()
        server_id = server.id
    user = User(username="admin", password="unused", is_active=True, is_admin=True)
    monkeypatch.setattr(auth, "get_current_web_user", AsyncMock(return_value=user))
    active = 0
    maximum = 0

    async def collect(alias: str, settings: object) -> dict:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.1)
        active -= 1
        return {"available": True, "sections": {}, "errors": {}}

    monkeypatch.setattr(views, "collect_server", collect)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app),
        base_url="https://testserver",
        headers={"Origin": "https://testserver"},
    ) as client:
        responses = await asyncio.gather(
            client.post(f"/servers/{server_id}/check"), client.post(f"/servers/{server_id}/check")
        )
    assert [response.status_code for response in responses] == [303, 303]
    assert maximum == 1
