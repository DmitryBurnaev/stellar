"""Authorization through real API, browser, and admin HTTP entry points."""

from typing import Generator
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import JSONResponse
from starlette.requests import Request
from starlette.testclient import TestClient

from src.db.models import BaseModel, Token, User
from src.db.repositories import (
    TokenRepository,
    UserRepository,
)
from src.application import StellarAPP
from src.main import make_app
from src.modules.admin.app import AdminApp, make_admin
from src.modules.admin.auth import AdminAuth
from src.modules.admin.views.base import BaseAPPView, BaseModelView
from sqladmin import BaseView, action, expose
from src.modules.auth.permissions import Permission, has_permission
from src.modules.auth.tokens import GeneratedToken, make_api_token
from src.settings import AppSettings, get_app_settings


@pytest.fixture
def user() -> User:
    return User(
        id=7,
        username="agent",
        email="agent@example.test",
        password=User.make_password("secret"),
        is_active=True,
        is_admin=False,
        can_access_admin=False,
        can_access_system_api=False,
    )


@pytest.fixture
def generated_token(app_settings_test: AppSettings) -> GeneratedToken:
    return make_api_token(expires_at=None, settings=app_settings_test)


@pytest.fixture
def api_token(user: User, generated_token: GeneratedToken) -> Token:
    return Token(id=1, user=user, token=generated_token.hashed_value, is_active=True, name="test")


@pytest.fixture
def test_app(
    app_settings_test: AppSettings, user: User, api_token: Token, monkeypatch: pytest.MonkeyPatch
) -> StellarAPP:
    # The HTTP stack and authentication are real; persistence is isolated.
    factory = async_sessionmaker[AsyncSession]()
    monkeypatch.setattr("src.db.session.get_session_factory", lambda: factory)

    async def find_token(hashed_token: str) -> Token | None:
        return api_token if hashed_token == api_token.token else None

    monkeypatch.setattr(TokenRepository, "get_by_token", AsyncMock(side_effect=find_token))
    monkeypatch.setattr(UserRepository, "get_by_username", AsyncMock(return_value=user))
    monkeypatch.setattr(UserRepository, "first", AsyncMock(return_value=user))
    app = make_app(app_settings_test)
    app.dependency_overrides[get_app_settings] = lambda: app_settings_test
    app.state.test_admin = make_admin(app)
    return app


@pytest.fixture
def permission_client(
    test_app: StellarAPP, generated_token: GeneratedToken
) -> Generator[TestClient, None]:
    # No lifespan: unbound sessions are enough for mocked repository operations.
    client = TestClient(
        test_app,
        base_url="https://testserver",
        headers={
            "Authorization": f"Bearer {generated_token.value}",
            "Origin": "https://testserver",
        },
    )
    try:
        yield client
    finally:
        client.close()


@pytest.mark.parametrize("permission", list(Permission))
def test_permissions_are_independent_and_superuser_overrides(
    user: User, permission: Permission
) -> None:
    assert not has_permission(user, permission)
    setattr(user, permission.value, True)
    for candidate in Permission:
        assert has_permission(user, candidate) is (candidate == permission)
    setattr(user, permission.value, False)
    user.is_admin = True
    assert all(has_permission(user, candidate) for candidate in Permission)
    user.is_active = False
    assert not any(has_permission(user, candidate) for candidate in Permission)


def test_token_permissions_are_reloaded_on_each_request(
    permission_client: TestClient, user: User
) -> None:
    user.can_access_system_api = True
    assert permission_client.get("/api/system/health/").status_code == 200
    user.can_access_system_api = False
    assert permission_client.get("/api/system/health/").status_code == 403
    user.is_admin = True
    assert permission_client.get("/api/system/health/").status_code == 200
    user.is_admin = False
    assert permission_client.get("/api/system/health/").status_code == 403


@pytest.mark.parametrize("inactive", ["user", "token"])
def test_inactive_superuser_or_token_still_gets_401(
    permission_client: TestClient, user: User, api_token: Token, inactive: str
) -> None:
    user.is_admin = True
    if inactive == "user":
        user.is_active = False
    else:
        api_token.is_active = False
    assert permission_client.get("/api/system/health/").status_code == 401


@pytest.mark.parametrize("authorization", ["", "invalid-token"])
def test_api_authentication_failure_remains_401(
    permission_client: TestClient, authorization: str
) -> None:
    assert (
        permission_client.get(
            "/api/system/health/", headers={"Authorization": authorization}
        ).status_code
        == 401
    )


@pytest.mark.parametrize("superuser", [False, True])
def test_admin_login_and_session_recheck_permissions(
    permission_client: TestClient,
    user: User,
    test_app: StellarAPP,
    superuser: bool,
) -> None:
    base = test_app.settings.admin.base_url
    credentials = {"username": "agent", "password": "secret"}
    assert (
        permission_client.post(
            base + "/login", data=credentials, follow_redirects=False
        ).status_code
        == 400
    )
    user.is_admin = superuser
    user.can_access_admin = not superuser
    assert (
        permission_client.post(
            base + "/login", data=credentials, follow_redirects=False
        ).status_code
        == 302
    )
    response = permission_client.get(base + "/user/create")
    assert response.status_code == 200
    assert "Is Admin (all rights)" in response.text
    for permission in Permission:
        assert f'name="{permission.value}"' in response.text
    assert permission_client.get("/api/system/health/").status_code == (200 if superuser else 403)
    user.is_admin = False
    user.can_access_admin = False
    assert permission_client.get(base + "/user/create", follow_redirects=False).status_code == 302


def test_inactive_user_loses_web_and_admin_sessions(
    permission_client: TestClient,
    user: User,
    test_app: StellarAPP,
) -> None:
    user.is_admin = True
    credentials = {"username": "agent", "password": "secret"}
    permission_client.post("/login", data=credentials, follow_redirects=False)
    base = test_app.settings.admin.base_url
    permission_client.post(base + "/login", data=credentials, follow_redirects=False)
    user.is_active = False
    assert permission_client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert permission_client.get(base + "/user/create", follow_redirects=False).status_code == 302


def test_inventory_admin_menu_icons(
    permission_client: TestClient, user: User, test_app: StellarAPP
) -> None:
    user.is_admin = True
    base = test_app.settings.admin.base_url
    response = permission_client.post(
        base + "/login", data={"username": "agent", "password": "secret"}, follow_redirects=False
    )
    assert response.status_code == 302
    page = permission_client.get(base + "/")
    stylesheet = permission_client.get(base + "/statics/css/fontawesome-all.min.css")
    assert page.status_code == 200 and stylesheet.status_code == 200
    for icon in ("cloud", "box", "layer-group", "server", "globe"):
        assert f'<i class="fa-solid fa-{icon}"></i>' in page.text
        assert f".fa-{icon}{{" in stylesheet.text


class ExampleSystemView(BaseAPPView):
    required_permission = Permission.SYSTEM_API
    name = "Example System Feature"

    @expose("/example-system", methods=["GET", "POST"])
    async def system_feature(self, request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})


class AuthorizationTestBase(DeclarativeBase):
    pass


class AuthorizationProbe(AuthorizationTestBase):
    __tablename__ = "authorization_probe"
    id: Mapped[int] = mapped_column(primary_key=True)


class ExampleSystemModelView(BaseModelView, model=AuthorizationProbe):
    required_permission = Permission.SYSTEM_API

    @action("probe")
    async def probe(self, request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})


def test_sqladmin_enforces_feature_grants_for_crud_and_custom_actions(
    permission_client: TestClient,
    user: User,
    test_app: StellarAPP,
) -> None:
    admin: AdminApp = test_app.state.test_admin
    admin.add_view(ExampleSystemModelView)
    base = f"{test_app.settings.admin.base_url}/{ExampleSystemModelView.identity}"
    user.can_access_admin = True
    permission_client.post(
        test_app.settings.admin.base_url + "/login",
        data={"username": "agent", "password": "secret"},
        follow_redirects=False,
    )
    for method, path in (
        ("GET", "/list"),
        ("GET", "/details/1"),
        ("GET", "/create"),
        ("POST", "/create"),
        ("GET", "/edit/1"),
        ("POST", "/edit/1"),
        ("DELETE", "/delete?pks=1"),
        ("GET", "/action/probe"),
    ):
        assert permission_client.request(method, base + path).status_code == 403
    user.can_access_system_api = True
    assert permission_client.get(base + "/create").status_code == 200
    assert permission_client.get(base + "/action/probe").status_code == 200
    user.can_access_system_api = False
    assert permission_client.get(base + "/action/probe").status_code == 403
    user.is_admin = True
    assert permission_client.get(base + "/action/probe").status_code == 200


def test_feature_admin_views_require_panel_and_feature_access(
    permission_client: TestClient,
    user: User,
    test_app: StellarAPP,
) -> None:
    admin: AdminApp = test_app.state.test_admin
    admin.add_view(ExampleSystemView)
    base = test_app.settings.admin.base_url
    user.can_access_admin = True
    permission_client.post(
        base + "/login", data={"username": "agent", "password": "secret"}, follow_redirects=False
    )
    for method in ("GET", "POST"):
        assert permission_client.request(method, base + "/example-system").status_code == 403
    assert ExampleSystemView.name not in permission_client.get(base + "/user/create").text
    user.can_access_system_api = True
    assert permission_client.get(base + "/example-system").status_code == 200
    assert ExampleSystemView.name in permission_client.get(base + "/user/create").text
    user.can_access_system_api = False
    assert permission_client.post(base + "/example-system").status_code == 403
    user.is_admin = True
    assert permission_client.post(base + "/example-system").status_code == 200
    user.is_admin = False
    user.can_access_admin = False
    user.can_access_system_api = True
    assert (
        permission_client.get(base + "/example-system", follow_redirects=False).status_code == 302
    )


def test_admin_registration_rejects_views_without_explicit_shared_policy(
    test_app: StellarAPP,
) -> None:
    admin: AdminApp = test_app.state.test_admin

    class MissingPermission(BaseAPPView):
        pass

    class UncheckedView(BaseView):
        pass

    with pytest.raises(ValueError, match="required_permission"):
        admin.add_view(MissingPermission)
    with pytest.raises(ValueError, match="required_permission"):
        admin.add_base_view(MissingPermission)
    with pytest.raises(ValueError, match="shared access-controlled base"):
        admin.add_view(UncheckedView)
    with pytest.raises(ValueError, match="shared access-controlled base"):
        admin.add_base_view(UncheckedView)


@pytest.mark.parametrize("permission", list(Permission))
def test_admin_form_persists_each_permission_independently(
    test_app: StellarAPP,
    permission_client: TestClient,
    user: User,
    permission: Permission,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = sa.create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    BaseModel.metadata.create_all(engine)
    factory = sessionmaker(engine)
    backend = AdminAuth(test_app.settings.app_secret_key.get_secret_value(), test_app.settings)
    AdminApp(
        test_app, base_url="/test-admin", session_maker=factory, authentication_backend=backend
    )
    user.can_access_admin = True
    monkeypatch.setattr(UserRepository, "get_by_username", AsyncMock(return_value=user))
    assert (
        permission_client.post(
            "/test-admin/login",
            data={"username": "agent", "password": "secret"},
            follow_redirects=False,
        ).status_code
        == 302
    )
    monkeypatch.setattr(UserRepository, "get_by_username", AsyncMock(return_value=None))
    data = {
        "username": "new-user",
        "email": "new@example.test",
        "new_password": "new-secret",
        "repeat_password": "new-secret",
        "is_active": "y",
        permission.value: "y",
        "save": "Save",
    }
    try:
        response = permission_client.post(
            "/test-admin/user/create", data=data, follow_redirects=False
        )
        assert response.status_code == 302, response.text
        with Session(engine) as session:
            created: User = session.scalars(sa.select(User)).one()
            assert created.is_admin is False
            assert created.verify_password("new-secret")
            assert all(getattr(created, p.value) is (p == permission) for p in Permission)
            pk = created.id
        edit = permission_client.get(f"/test-admin/user/edit/{pk}")
        assert edit.status_code == 200
        # Saving without the checkbox must revoke its stored value.
        response = permission_client.post(
            f"/test-admin/user/edit/{pk}",
            data={"email": "new@example.test", "is_active": "y", "save": "Save"},
            follow_redirects=False,
        )
        assert response.status_code == 302, response.text
        with Session(engine) as session:
            updated = session.get(User, pk)
            assert updated is not None
            assert not any(getattr(updated, p.value) for p in Permission)
            assert updated.verify_password("new-secret")
    finally:
        engine.dispose()
