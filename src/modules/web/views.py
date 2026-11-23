"""HTML views for the browser-facing Stellar application."""

import logging
from typing import Annotated, Any, NamedTuple, TypeAlias
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi import HTTPException
import sqlalchemy as sa
from sqlalchemy.orm import selectinload
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from src.constants import APP_DIR
from src.db.models import User, Server, ServerDomain, ServerGroup, ServerCheck, FavoriteComponent
from src.db import session as db_session
from src.modules.server_checks import collect_server
from src.modules.ssh import SSHError
from src.modules.port_scanner import ScannerError, get_scan, submit_scan
from src.utils import utcnow
from src.modules.web.auth import (
    authenticate_web_user,
    login_web_user,
    logout_web_user,
)
from src.settings import AppSettings, get_app_settings
from src.modules.auth.permissions import Permission, has_permission
from src.modules.auth.dependencies import WebAccess
from src.modules.web import auth as web_auth

router = APIRouter(include_in_schema=False)
public_router = APIRouter(include_in_schema=False)
dashboard_access = WebAccess()
dashboard_router = APIRouter(dependencies=[Depends(dashboard_access)], include_in_schema=False)
inventory_access = WebAccess(Permission.INVENTORY)
inventory_router = APIRouter(dependencies=[Depends(inventory_access)], include_in_schema=False)
checks_router = APIRouter(
    dependencies=[Depends(inventory_access), Depends(WebAccess(Permission.SERVER_CHECKS))],
    include_in_schema=False,
)
favorites_router = APIRouter(
    dependencies=[Depends(inventory_access), Depends(WebAccess(Permission.INVENTORY_ADMIN))],
    include_in_schema=False,
)
DashboardUser: TypeAlias = Annotated[User, Depends(dashboard_access)]
InventoryUser: TypeAlias = Annotated[User, Depends(inventory_access)]
templates = Jinja2Templates(directory=APP_DIR / "modules" / "web" / "templates")
STATIC_ASSETS_DIR = APP_DIR / "static"


class NavigationItem(NamedTuple):
    """A link shown in the application sidebar."""

    title: str
    path: str
    slug: str
    permission: Permission | None = None


NAVIGATION = (
    NavigationItem("Overview", "/", "dashboard"),
    NavigationItem("Servers", "/servers", "servers", Permission.INVENTORY),
    NavigationItem("Administration", "", "admin", Permission.ADMIN_PANEL),
)


def get_app_version(request: Request) -> str:
    """Return the version configured for the currently running application."""
    app = getattr(request, "app", None)
    settings = getattr(app, "settings", None)
    if isinstance(settings, AppSettings):
        return settings.app_version

    return get_app_settings().app_version


def static_assets_version() -> str:
    """Return a cache-busting version that changes with the web assets."""
    assets = (
        STATIC_ASSETS_DIR / "css" / "stellar.css",
        STATIC_ASSETS_DIR / "js" / "web-ui.js",
        STATIC_ASSETS_DIR / "js" / "inventory.js",
    )
    return str(max(asset.stat().st_mtime_ns for asset in assets))


def render_template(
    request: Request,
    template_name: str,
    *,
    title: str,
    current: str,
    current_user: User | None,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render a page with values shared by the application shell."""
    return templates.TemplateResponse(
        request,
        template_name,
        {
            "title": title,
            "current": current,
            "current_user": current_user,
            "navigation": tuple(
                (
                    link._replace(path=request.app.settings.admin.base_url + "/")
                    if link.slug == "admin"
                    else link
                )
                for link in NAVIGATION
                if link.permission is None or has_permission(current_user, link.permission)
            ),
            "feature_access": {
                permission.value: has_permission(current_user, permission)
                for permission in Permission
            },
            "app_version": get_app_version(request),
            "static_assets_version": static_assets_version(),
        }
        | (context or {}),
        status_code=status_code,
    )


@public_router.get("/login", response_class=HTMLResponse, response_model=None)
async def login_page(request: Request) -> HTMLResponse | RedirectResponse:
    """Render the login form for anonymous users."""
    user = await web_auth.get_current_web_user(request)
    if user is not None:
        return RedirectResponse(url="/", status_code=303)

    return render_template(
        request,
        "login.html",
        title="Sign in",
        current="",
        current_user=None,
        context={"login_error": request.query_params.get("error")},
    )


@public_router.post("/login", response_model=None)
async def login_submit(request: Request) -> RedirectResponse:
    """Authenticate username and password submitted by the login form."""
    form = await request.form()
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    if not username or not password:
        logging.getLogger(__name__).warning("[web-auth] Login rejected: reason=missing_credentials")
        return RedirectResponse(url="/login?error=missing", status_code=303)

    user = await authenticate_web_user(username=username, password=password)
    if user is None:
        return RedirectResponse(url="/login?error=invalid", status_code=303)

    login_web_user(request, user)
    return RedirectResponse(url="/", status_code=303)


@public_router.post("/logout", response_model=None)
async def logout(request: Request) -> RedirectResponse:
    """Clear the browser session and return to login page."""
    logout_web_user(request)
    return RedirectResponse(url="/login", status_code=303)


@dashboard_router.get("/", response_class=HTMLResponse, response_model=None)
async def dashboard(request: Request, user: DashboardUser) -> HTMLResponse:
    """Render the dashboard after authentication and successful application startup."""
    servers: list[Server] = []
    if has_permission(user, Permission.INVENTORY):
        async with db_session.get_session_factory()() as session:
            statement = (
                sa.select(Server)
                .options(selectinload(Server.groups), selectinload(Server.domains))
                .order_by(Server.ip_address)
            )
            servers = list((await session.scalars(statement)).unique().all())
    return render_template(
        request,
        "dashboard.html",
        title="Dashboard",
        current="dashboard",
        current_user=user,
        context={"servers": servers},
    )


@inventory_router.get("/servers", response_class=HTMLResponse)
async def server_list(
    request: Request,
    user: InventoryUser,
    q: str = "",
    group: str = "",
    availability: str = "",
) -> HTMLResponse:
    statement = sa.select(Server).options(selectinload(Server.groups), selectinload(Server.domains))
    if q.strip():
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            sa.or_(
                Server.ip_address.ilike(pattern),
                Server.domains.any(ServerDomain.name.ilike(pattern)),
            )
        )
    if group:
        if not group.isdecimal():
            raise HTTPException(status_code=400, detail="Invalid group")
        group_id = int(group)
        statement = statement.where(
            ~Server.groups.any() if group_id == 0 else Server.groups.any(ServerGroup.id == group_id)
        )
    latest_status = (
        sa.select(ServerCheck.status)
        .where(ServerCheck.server_id == Server.id, ServerCheck.kind == "ssh")
        .order_by(ServerCheck.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    latest_checked_at = (
        sa.select(ServerCheck.finished_at)
        .where(ServerCheck.server_id == Server.id, ServerCheck.kind == "ssh")
        .order_by(ServerCheck.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    if availability == "available":
        statement = statement.where(latest_status.in_(["succeeded", "partial"]))
    elif availability == "failed":
        statement = statement.where(latest_status == "failed")
    elif availability == "unknown":
        statement = statement.where(latest_status.is_(None))
    async with db_session.get_session_factory()() as session:
        rows = (
            (
                await session.execute(
                    statement.add_columns(latest_status, latest_checked_at).order_by(
                        Server.ip_address
                    )
                )
            )
            .unique()
            .all()
        )
        servers = [(row[0], row[1], row[2]) for row in rows]
        groups = (await session.scalars(sa.select(ServerGroup).order_by(ServerGroup.name))).all()
    return render_template(
        request,
        "servers.html",
        title="Servers",
        current="servers",
        current_user=user,
        context={
            "servers": servers,
            "groups": groups,
            "q": q,
            "group": group,
            "availability": availability,
        },
    )


@inventory_router.get("/servers/{server_id}", response_class=HTMLResponse)
async def server_detail(request: Request, server_id: int, user: InventoryUser) -> HTMLResponse:
    async with db_session.get_session_factory()() as session:
        statement = (
            sa.select(Server)
            .where(Server.id == server_id)
            .options(
                selectinload(Server.groups),
                selectinload(Server.domains),
                selectinload(Server.applications),
            )
        )
        server = (await session.scalars(statement)).first()
        checks = (
            await session.scalars(
                sa.select(ServerCheck)
                .where(ServerCheck.server_id == server_id)
                .order_by(ServerCheck.id.desc())
            )
        ).all()
        favorites = (
            await session.scalars(
                sa.select(FavoriteComponent).where(FavoriteComponent.server_id == server_id)
            )
        ).all()
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    sections: dict[str, Any] = {}
    section_dates: dict[str, Any] = {}
    for check in checks:
        if check.kind == "ssh" and check.result:
            for name, value in check.result.get("sections", {}).items():
                if name not in sections:
                    sections[name] = value
                    section_dates[name] = check.finished_at
    favorite_keys = {(item.kind, item.name) for item in favorites}
    present_keys = {
        (kind, item["name"])
        for kind, section in (("package", "packages"), ("image", "images"))
        for item in sections.get(section, [])
    }
    missing_favorites = sorted(favorite_keys - present_keys)
    latest_ssh = next((item for item in checks if item.kind == "ssh"), None)
    latest_scan = next((item for item in checks if item.kind == "ports"), None)
    successful_scan = next(
        (item for item in checks if item.kind == "ports" and item.status == "succeeded"), None
    )
    return render_template(
        request,
        "server_detail.html",
        title=str(server),
        current="servers",
        current_user=user,
        context={
            "server": server,
            "return_query": request.query_params.get("return_query", ""),
            "checks": checks[:10],
            "latest_ssh": latest_ssh,
            "latest_scan": latest_scan,
            "successful_scan": successful_scan,
            "sections": sections,
            "section_dates": section_dates,
            "favorites": favorite_keys,
            "missing_favorites": missing_favorites,
        },
    )


@checks_router.post("/servers/{server_id}/check")
async def check_server(request: Request, server_id: int) -> RedirectResponse:
    form = await request.form()
    async with db_session.get_session_factory()() as session:
        async with session.begin():
            server = await session.scalar(
                sa.select(Server).where(Server.id == server_id).with_for_update(of=Server)
            )
            if server is None:
                raise HTTPException(status_code=404, detail="Server not found")
            attempt = ServerCheck(
                server_id=server_id, kind="ssh", status="failed", started_at=utcnow()
            )
            try:
                result = await collect_server(server.ssh_alias, request.app.settings)
                attempt.result = result
                attempt.status = "partial" if result["errors"] else "succeeded"
            except (SSHError, OSError, TimeoutError, ValueError) as exc:
                if isinstance(exc, (SSHError, ValueError)):
                    attempt.error = str(exc)
                elif isinstance(exc, TimeoutError):
                    attempt.error = "SSH check timed out"
                else:
                    attempt.error = "SSH program or configuration could not be accessed"
                logging.getLogger(__name__).warning(
                    "Server SSH check failed: server_id=%s reason=%s", server_id, attempt.error
                )
            attempt.finished_at = utcnow()
            session.add(attempt)
    return detail_redirect(server_id, str(form.get("return_query", "")))


@favorites_router.post("/servers/{server_id}/favorites")
async def set_favorite(request: Request, server_id: int) -> RedirectResponse:
    form = await request.form()
    kind = str(form.get("kind", ""))
    name = str(form.get("name", ""))
    if kind not in {"package", "image"} or not name or len(name) > 512:
        raise HTTPException(status_code=400, detail="Invalid component")
    async with db_session.get_session_factory()() as session:
        async with session.begin():
            server = await session.scalar(
                sa.select(Server).where(Server.id == server_id).with_for_update(of=Server)
            )
            if server is None:
                raise HTTPException(status_code=404, detail="Server not found")
            existing = await session.scalar(
                sa.select(FavoriteComponent).where(
                    FavoriteComponent.server_id == server_id,
                    FavoriteComponent.kind == kind,
                    FavoriteComponent.name == name,
                )
            )
            if str(form.get("favorite")) == "1" and existing is None:
                section = "packages" if kind == "package" else "images"
                checks = (
                    await session.scalars(
                        sa.select(ServerCheck)
                        .where(ServerCheck.server_id == server_id, ServerCheck.kind == "ssh")
                        .order_by(ServerCheck.id.desc())
                    )
                ).all()
                items: list[dict[str, Any]] = next(
                    (
                        check.result["sections"][section]
                        for check in checks
                        if check.result and section in check.result.get("sections", {})
                    ),
                    [],
                )
                if not any(item.get("name") == name for item in items):
                    raise HTTPException(
                        status_code=400, detail="Component is not in the server inventory"
                    )
                session.add(FavoriteComponent(server_id=server_id, kind=kind, name=name))
            elif str(form.get("favorite")) == "0" and existing is not None:
                await session.delete(existing)
    return detail_redirect(server_id, str(form.get("return_query", "")))


@checks_router.post("/servers/{server_id}/ports")
async def check_ports(request: Request, server_id: int) -> RedirectResponse:
    form = await request.form()
    async with db_session.get_session_factory()() as session:
        async with session.begin():
            server = await session.scalar(
                sa.select(Server).where(Server.id == server_id).with_for_update(of=Server)
            )
            if server is None:
                raise HTTPException(status_code=404, detail="Server not found")
            attempt = await session.scalar(
                sa.select(ServerCheck)
                .where(
                    ServerCheck.server_id == server_id,
                    ServerCheck.kind == "ports",
                    ServerCheck.status.in_(["submitting", "queued", "running"]),
                )
                .order_by(ServerCheck.id.desc())
                .limit(1)
            )
            if attempt is None:
                attempt = ServerCheck(
                    server_id=server_id,
                    kind="ports",
                    status="submitting",
                    started_at=utcnow(),
                    result={
                        "address": server.scan_address or server.ip_address,
                        "key": str(uuid4()),
                    },
                )
                session.add(attempt)
    # Commit the idempotency key before contacting the scanner, so retries remain safe.
    async with db_session.get_session_factory()() as session:
        async with session.begin():
            await session.scalar(
                sa.select(Server.id).where(Server.id == server_id).with_for_update()
            )
            attempt = await session.scalar(
                sa.select(ServerCheck)
                .where(
                    ServerCheck.server_id == server_id,
                    ServerCheck.kind == "ports",
                    ServerCheck.status.in_(["submitting", "queued", "running"]),
                )
                .order_by(ServerCheck.id.desc())
                .limit(1)
            )
            if attempt is not None and attempt.result:
                result = dict(attempt.result)
                try:
                    if attempt.status == "submitting":
                        result["scan_id"] = await submit_scan(
                            result["address"], result["key"], request.app.settings
                        )
                        attempt.status = "queued"
                    else:
                        response = await get_scan(
                            result["scan_id"], result["address"], request.app.settings
                        )
                        attempt.status = response["status"]
                        if attempt.status in {"succeeded", "failed"}:
                            attempt.finished_at = utcnow()
                        if attempt.status == "succeeded":
                            result["open_tcp_ports"] = response["open_tcp_ports"]
                            result["scanner"] = response.get("scanner", "Unknown")
                        elif attempt.status == "failed":
                            attempt.error = "The scanner reported a failed scan"
                    attempt.result = result
                    if attempt.status != "failed":
                        attempt.error = None
                except ScannerError as exc:
                    attempt.error = str(exc)
    return detail_redirect(server_id, str(form.get("return_query", "")))


def detail_redirect(server_id: int, return_query: str) -> RedirectResponse:
    query = f"?{urlencode({'return_query': return_query})}" if return_query else ""
    return RedirectResponse(f"/servers/{server_id}{query}", status_code=303)


router.include_router(public_router)
router.include_router(dashboard_router)
router.include_router(inventory_router)
router.include_router(checks_router)
router.include_router(favorites_router)
