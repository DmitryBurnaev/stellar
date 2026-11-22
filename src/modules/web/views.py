"""HTML views for the browser-facing Stellar application."""

import logging
from typing import Annotated, Any, NamedTuple, TypeAlias

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from src.constants import APP_DIR
from src.db.models import User
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
DashboardUser: TypeAlias = Annotated[User, Depends(dashboard_access)]
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
    assets = (STATIC_ASSETS_DIR / "css" / "stellar.css", STATIC_ASSETS_DIR / "js" / "web-ui.js")
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
    return render_template(
        request,
        "dashboard.html",
        title="Dashboard",
        current="dashboard",
        current_user=user,
    )


router.include_router(public_router)
router.include_router(dashboard_router)
