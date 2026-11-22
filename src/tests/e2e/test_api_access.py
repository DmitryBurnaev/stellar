"""Exercise Stellar through real HTTP and a disposable PostgreSQL database."""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from typing import Any
from uuid import uuid4

from alembic import command
from alembic.config import Config
import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from src.settings.db import DBSettings
from src.tests.e2e.server import wait_for_server

ROOT = Path(__file__).resolve().parents[3]
FLAGS = ("can_access_admin", "can_access_system_api")
ROLES = {
    "none": (),
    "system": ("can_access_system_api",),
    "admin_panel": ("can_access_admin",),
    "superuser": (),
}
HEALTH_PATH = "/api/system/health/"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("RUN_API_E2E") != "1", reason="Set RUN_API_E2E=1 for real PostgreSQL/HTTP tests"
    ),
]


class TokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.token: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "input" and attributes.get("id") == "token-value-input":
            self.token = attributes.get("value")


@dataclass
class Account:
    id: int
    name: str
    token_id: int
    token: str = field(repr=False)
    password: str = field(repr=False)


@dataclass
class Harness:
    dsn: str = field(repr=False)
    client: httpx.Client = field(repr=False)
    admin: httpx.Client = field(repr=False)
    artifacts: Path
    accounts: dict[str, Account] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)
    setup: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def query(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        async def execute() -> list[dict[str, Any]]:
            engine = create_async_engine(self.dsn)
            try:
                async with engine.begin() as connection:
                    rows = await connection.execute(sa.text(sql), params)
                    return [dict(row) for row in rows.mappings()] if rows.returns_rows else []
            finally:
                await engine.dispose()

        return asyncio.run(execute())

    def admin_post(self, path: str, data: dict[str, str], expected: int) -> httpx.Response:
        response = self.admin.post(path, data=data)
        self.setup.append({"method": "POST", "path": path, "status": response.status_code})
        assert response.status_code == expected, f"Provisioning {path}: {response.status_code}"
        return response

    def issue_token(self, user_id: int, name: str, expires: str = "") -> tuple[int, str]:
        response = self.admin_post(
            "/admin/token/create",
            {
                "user": str(user_id),
                "name": name,
                "expires_at": expires,
                "save": "Save",
            },
            200,
        )
        parser = TokenParser()
        parser.feed(response.text)
        assert response.headers["cache-control"] == "no-store"
        assert parser.token, "Issued token missing from create response"
        rows = self.query("SELECT id, token FROM tokens WHERE name = :name", name=name)
        assert len(rows) == 1
        assert rows[0]["token"] != parser.token and len(rows[0]["token"]) == 128
        token_id = rows[0]["id"]
        details = self.admin.get(f"/admin/token/details/{token_id}")
        assert details.status_code == 200
        assert "token-value-input" not in details.text
        return token_id, parser.token

    def create_account(self, role: str, flags: tuple[str, ...] = ()) -> Account:
        name = f"e2e_{role}"
        data = {
            "username": name,
            "email": f"{name}@example.test",
            "new_password": secrets.token_urlsafe(24),
            "is_active": "y",
            "save": "Save",
        }
        data["repeat_password"] = data["new_password"]
        data.update({flag: "y" for flag in flags})
        if role == "superuser":
            data["is_admin"] = "y"
        self.admin_post("/admin/user/create", data, 302)
        row = self.query("SELECT * FROM users WHERE username = :name", name=name)[0]
        assert row["is_admin"] is (role == "superuser")
        assert all(row[flag] is (flag in flags) for flag in FLAGS)
        token_id, token = self.issue_token(row["id"], name)
        account = Account(row["id"], name, token_id, token, data["new_password"])
        self.accounts[role] = account
        return account

    def update_account(
        self,
        account: Account,
        flags: tuple[str, ...] = (),
        *,
        active: bool = True,
        superuser: bool = False,
    ) -> None:
        data = {"email": f"{account.name}@example.test", "save": "Save"}
        data.update({flag: "y" for flag in flags})
        if active:
            data["is_active"] = "y"
        if superuser:
            data["is_admin"] = "y"
        self.admin_post(f"/admin/user/edit/{account.id}", data, 302)
        row = self.query("SELECT * FROM users WHERE id = :id", id=account.id)[0]
        assert row["is_active"] is active and row["is_admin"] is superuser
        assert all(row[flag] is (flag in flags) for flag in FLAGS)

    def request(
        self,
        case: str,
        role: str,
        expected: int,
        *,
        authorization: str | None = None,
    ) -> None:
        headers = {} if authorization is None else {"Authorization": authorization}
        response = self.client.get(HEALTH_PATH, headers=headers)
        result = {"case": case, "role": role, "expected": expected, "actual": response.status_code}
        self.results.append(result)
        assert response.status_code == expected, result
        if expected == 200:
            assert response.json()["status"] == "healthy"
        else:
            assert "timestamp" not in response.json()


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Harness]:
    work = tmp_path_factory.mktemp("api-access")
    artifacts = Path(os.getenv("E2E_ARTIFACT_DIR", str(work / "results"))).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    database = "stellar_e2e_" + uuid4().hex
    password = os.getenv("E2E_POSTGRES_PASSWORD", os.getenv("DB_PASSWORD"))
    if password is None:
        password = DBSettings().password
    connection = sa.URL.create(
        "postgresql+asyncpg",
        username=os.getenv("E2E_POSTGRES_USER", "postgres"),
        password=password,
        host=os.getenv("E2E_POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("E2E_POSTGRES_PORT", "5432")),
        database="postgres",
    )
    dsn = connection.set(database=database).render_as_string(hide_password=False)
    admin_password = secrets.token_urlsafe(24)
    env = {
        "DB_HOST": connection.host or "127.0.0.1",
        "DB_PORT": str(connection.port),
        "DB_USER": connection.username or "postgres",
        "DB_PASSWORD": connection.password or "",
        "DB_NAME": database,
        "APP_SECRET_KEY": secrets.token_urlsafe(48),
        "ADMIN_USERNAME": "e2e_bootstrap",
        "ADMIN_PASSWORD": admin_password,
        "LOG_LEVEL": "WARNING",
        "LOG_FORMAT": "%(message)s",
        "WEB_SESSION_HTTPS_ONLY": "false",
        "PYTHONPATH": str(ROOT),
        "API_DOCS_ENABLED": "false",
    }
    previous = {name: os.environ.get(name) for name in env}
    previous_directory = Path.cwd()
    os.environ.update(env)
    # Both migrations and Uvicorn load settings from a clean directory, never the project's .env.
    os.chdir(work)

    async def manage_database(create: bool) -> None:
        assert database.startswith("stellar_e2e_") and len(database) == len("stellar_e2e_") + 32
        engine = create_async_engine(connection, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                sql = (
                    f'CREATE DATABASE "{database}"'
                    if create
                    else f'DROP DATABASE "{database}" WITH (FORCE)'
                )
                await conn.exec_driver_sql(sql)
        finally:
            await engine.dispose()

    async def migrate() -> None:
        engine = create_async_engine(dsn)
        try:
            async with engine.begin() as conn:

                def upgrade(sync_connection: sa.Connection) -> None:
                    config = Config()
                    config.set_main_option("script_location", str(ROOT / "src" / "migrations"))
                    config.attributes["connection"] = sync_connection
                    command.upgrade(config, "head")

                await conn.run_sync(upgrade)
        finally:
            await engine.dispose()

    process: subprocess.Popen[bytes] | None = None
    created = False
    api = httpx.Client(base_url=f"http://127.0.0.1:{free_port()}", timeout=15, trust_env=False)
    admin = httpx.Client(
        base_url=api.base_url,
        timeout=15,
        trust_env=False,
        headers={"Origin": str(api.base_url).rstrip("/")},
    )
    run = Harness(dsn, api, admin, artifacts)
    run.metadata.update(
        {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "database": database,
            "test_process": os.getpid(),
            "api_url": str(api.base_url),
        }
    )
    try:
        asyncio.run(manage_database(True))
        created = True
        asyncio.run(migrate())
        run.metadata["postgres_version"] = run.query("SELECT version() AS version")[0]["version"]
        run.metadata["alembic_head"] = run.query("SELECT version_num FROM alembic_version")[0][
            "version_num"
        ]
        uvicorn = Path(sys.executable).parent / "uvicorn"
        with (work / "app.log").open("wb") as log:
            process = subprocess.Popen(
                [
                    str(uvicorn),
                    "src.main:make_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api.base_url.port),
                    "--no-access-log",
                ],
                cwd=work,
                env=os.environ.copy(),
                stdout=log,
                stderr=log,
            )
        run.metadata["app_process"] = process.pid
        wait_for_server(process, api, work / "app.log")
        run.admin_post(
            "/admin/login", {"username": "e2e_bootstrap", "password": admin_password}, 302
        )
        for role, flags in ROLES.items():
            run.create_account(role, flags)
        yield run
    finally:
        api.close()
        admin.close()
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if created:
            asyncio.run(manage_database(False))
            run.metadata["temporary_database_removed"] = True
        os.chdir(previous_directory)
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        run.metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
        (artifacts / "results.json").write_text(
            json.dumps(
                {"metadata": run.metadata, "provisioning": run.setup, "requests": run.results},
                indent=2,
            ),
            encoding="utf-8",
        )


@pytest.mark.parametrize("role", list(ROLES))
def test_role_matrix(harness: Harness, role: str) -> None:
    allowed = role == "superuser" or "can_access_system_api" in ROLES[role]
    token = "Bearer " + harness.accounts[role].token
    harness.request("role-matrix", role, 200 if allowed else 403, authorization=token)


@pytest.fixture(scope="module")
def invalid_tokens(harness: Harness) -> dict[str, str | None]:
    inactive = harness.create_account("inactive", ("can_access_system_api",))
    harness.update_account(inactive, active=False, superuser=True)
    disabled = harness.create_account("disabled_token", ("can_access_system_api",))
    response = harness.admin.get(f"/admin/token/action/deactivate?pks={disabled.token_id}")
    assert response.status_code in (302, 303, 307)
    unknown = harness.create_account("deleted_token", ("can_access_system_api",))
    response = harness.admin.delete(f"/admin/token/delete?pks={unknown.token_id}")
    assert response.status_code == 200
    _, expired = harness.issue_token(
        harness.accounts["superuser"].id, "expired", "2020-01-01 00:00:00"
    )
    valid = harness.accounts["system"].token
    signature_start = len(valid) - 3 - int(valid[-3:])
    replacement = "B" if valid[signature_start] == "A" else "A"
    tampered = valid[:signature_start] + replacement + valid[signature_start + 1 :]
    return {
        "missing": None,
        "empty": "",
        "bare-bearer": "Bearer",
        "malformed": "not-a-token",
        "tampered": "Bearer " + tampered,
        "expired": "Bearer " + expired,
        "disabled-token": "Bearer " + disabled.token,
        "deleted-token": "Bearer " + unknown.token,
        "inactive-superuser": "Bearer " + inactive.token,
    }


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "empty",
        "bare-bearer",
        "malformed",
        "tampered",
        "expired",
        "disabled-token",
        "deleted-token",
        "inactive-superuser",
    ],
)
def test_invalid_authentication(
    harness: Harness, invalid_tokens: dict[str, str | None], kind: str
) -> None:
    harness.request("invalid-authentication", kind, 401, authorization=invalid_tokens[kind])


def test_grants_revocation_and_superuser_use_existing_token(harness: Harness) -> None:
    account = harness.create_account("mutable", ("can_access_system_api",))
    authorization = "Bearer " + account.token
    steps = [
        (("can_access_system_api",), False, 200),
        (("can_access_admin",), False, 403),
        ((), True, 200),
        ((), False, 403),
    ]
    for number, (flags, superuser, expected) in enumerate(steps):
        harness.update_account(account, flags, superuser=superuser)
        harness.request(
            f"existing-token-{number}", "mutable", expected, authorization=authorization
        )


def test_token_deactivation_and_reactivation(harness: Harness) -> None:
    account = harness.create_account("reactivate", ("can_access_system_api",))
    auth = "Bearer " + account.token
    harness.request("before-token-disable", "reactivate", 200, authorization=auth)
    for action, expected in (("deactivate", 401), ("activate", 200)):
        response = harness.admin.get(f"/admin/token/action/{action}?pks={account.token_id}")
        assert response.status_code in (302, 303, 307)
        harness.request(action, "reactivate", expected, authorization=auth)


def test_database_schema_and_permission_defaults(harness: Harness) -> None:
    rows = harness.query(
        "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'users' AND column_name LIKE 'can_%'"
    )
    assert {row["column_name"] for row in rows} == set(FLAGS)
    assert all(row["is_nullable"] == "NO" and row["column_default"] == "false" for row in rows)
    assert harness.metadata["alembic_head"] == "0001"


def test_independent_sessions_and_live_browser_permissions(harness: Harness) -> None:
    account = harness.create_account("browser", ("can_access_admin", "can_access_system_api"))
    credentials = {"username": account.name, "password": account.password}
    with httpx.Client(
        base_url=harness.client.base_url,
        headers={"Origin": str(harness.client.base_url).rstrip("/")},
        trust_env=False,
    ) as browser:
        assert browser.post("/login", data=credentials).status_code == 303
        assert browser.post("/admin/login", data=credentials).status_code == 302
        dashboard = browser.get("/")
        assert dashboard.status_code == 200
        assert "System health API" in dashboard.text and 'href="/admin/"' in dashboard.text
        assert browser.get("/admin/user/list").status_code == 200

        assert browser.get("/admin/logout").is_redirect
        assert browser.get("/").status_code == 200
        assert browser.get("/admin/user/list").is_redirect

        assert browser.post("/admin/login", data=credentials).status_code == 302
        assert browser.post("/logout").status_code == 303
        assert browser.get("/").headers["location"] == "/login"
        assert browser.get("/admin/user/list").status_code == 200

        assert browser.post("/login", data=credentials).status_code == 303
        harness.update_account(account)
        dashboard = browser.get("/")
        assert dashboard.status_code == 200
        assert "System health API" not in dashboard.text and 'href="/admin/"' not in dashboard.text
        for method, path in (
            ("GET", "/admin/user/list"),
            ("GET", "/admin/token/create"),
            ("POST", "/admin/token/create"),
            ("GET", f"/admin/token/action/deactivate?pks={account.token_id}"),
        ):
            response = browser.request(method, path)
            assert response.is_redirect
            assert response.headers["location"].endswith("/admin/login")
        assert browser.post("/admin/login", data=credentials).status_code == 400
        rows = harness.query("SELECT is_active FROM tokens WHERE id = :id", id=account.token_id)
        assert rows[0]["is_active"]

        harness.update_account(account, active=False)
        assert browser.get("/").headers["location"] == "/login"
