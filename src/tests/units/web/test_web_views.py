from typing import Any, Generator
from unittest.mock import AsyncMock

import pytest
from fastapi.routing import iter_route_contexts
from starlette.testclient import TestClient

from src.application import StellarAPP
from src.modules.web import views
from src.tests.mocks import MockUser


@pytest.fixture
def web_client(test_app: StellarAPP) -> Generator[TestClient, Any, None]:
    client = TestClient(
        test_app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    )
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def active_user() -> MockUser:
    return MockUser(id=3, is_active=True, username="agent", can_access_system_api=True)


def test_app_registers_web_routes_static_files_and_isolated_session(
    test_app: StellarAPP,
) -> None:
    web_route_paths = {route.path for route in iter_route_contexts(views.router.routes)}
    assert {"/", "/login", "/logout"} <= web_route_paths
    assert any(getattr(route, "path", None) == "/static" for route in test_app.routes)

    middleware = next(
        middleware
        for middleware in test_app.user_middleware
        if middleware.cls.__name__ == "WebSessionMiddleware"  # type: ignore
    )
    assert middleware.kwargs["session_cookie"] == "stellar_web_session"
    assert middleware.kwargs["max_age"] == 2 * 24 * 3600
    assert middleware.kwargs["https_only"] is True


def test_login_page_and_static_assets_render(web_client: TestClient) -> None:
    response = web_client.get("/login")

    assert response.status_code == 200
    assert "Sign in" in response.text
    assert "Stellar" in response.text
    assert web_client.get("/static/css/stellar.css").status_code == 200
    assert web_client.get("/static/js/web-ui.js").status_code == 200


def test_dashboard_redirects_anonymous_users(web_client: TestClient) -> None:
    response = web_client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_requires_both_credentials(web_client: TestClient) -> None:
    response = web_client.post("/login", data={"username": "agent"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=missing"


def test_login_rejects_invalid_or_inactive_users(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "authenticate_web_user", AsyncMock(return_value=None))

    response = web_client.post(
        "/login",
        data={"username": "inactive", "password": "not-used"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=invalid"
    assert "token" not in response.headers.get("set-cookie", "").lower()


@pytest.mark.parametrize("system,admin", [(False, False), (True, False), (False, True)])
def test_dashboard_visibility_follows_grants(
    test_app: StellarAPP,
    web_client: TestClient,
    active_user: MockUser,
    monkeypatch: pytest.MonkeyPatch,
    system: bool,
    admin: bool,
) -> None:
    active_user.can_access_system_api = system
    active_user.can_access_admin = admin
    monkeypatch.setattr(
        "src.modules.web.auth.get_current_web_user", AsyncMock(return_value=active_user)
    )
    response = web_client.get("/")
    assert response.status_code == 200
    assert "System overview" in response.text
    assert ("System health API" in response.text) is system
    assert (f'href="{test_app.settings.admin.base_url}/"' in response.text) is admin
    assert test_app.settings.app_version in response.text
