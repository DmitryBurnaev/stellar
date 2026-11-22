import sys
import logging.config
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from starlette.staticfiles import StaticFiles

from src.application import StellarAPP
from src.modules.auth.routing import validate_access_policies
from src.modules.admin.app import make_admin
from src.modules.web.views import router as web_router
from src.modules.web.middleware import WebSessionMiddleware
from src.modules.web.csrf import CSRFProtectionMiddleware
from src.constants import APP_DIR
from src.exceptions import AppSettingsError, StartupError
from src.settings import get_app_settings, AppSettings
from src.modules.api import system_router
from src.db.session import initialize_database, close_database
from src.db.diagnostics import failure_details

logger = logging.getLogger("src.main")


@asynccontextmanager
async def lifespan(app: StellarAPP) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown events."""
    logger.info("Starting up application...")
    try:
        await initialize_database()
    except Exception as exc:
        logger.critical("Application startup failed: %s", failure_details(exc))
        # The safe causal diagnostics above replace Uvicorn's raw driver traceback.
        raise StartupError("Application startup failed; see diagnostic log") from None

    try:
        make_admin(app)
        validate_access_policies(app)
        logger.info("Application startup completed successfully")
        yield
    finally:
        try:
            await close_database()
        except Exception as exc:
            logger.error("Error during application shutdown: %s", failure_details(exc))


def make_app(settings: AppSettings | None = None) -> StellarAPP:
    """Forming Application instance with required settings and dependencies"""

    if settings is None:
        try:
            settings = get_app_settings()
        except AppSettingsError as exc:
            logger.error("Unable to get settings from environment: %r", exc)
            sys.exit(1)

    logging.config.dictConfig(settings.log.dict_config_any)
    logging.captureWarnings(capture=True)

    logger.info("Setting up application...")
    app = StellarAPP(
        title="Stellar API",
        description="Stellar system API",
        openapi_url="/api/openapi.json" if settings.api_docs_enabled else None,
        docs_url="/api/docs/" if settings.api_docs_enabled else None,
        redoc_url="/api/redoc/" if settings.api_docs_enabled else None,
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
        lifespan=lifespan,
    )
    app.set_settings(settings)
    app.dependency_overrides[get_app_settings] = lambda: app.settings
    logger.info(
        "[web-auth] Session configuration: cookie=%s https_only=%s max_age=%s",
        settings.web.session_cookie_name,
        settings.web.session_https_only,
        settings.web.session_expiration_time,
    )
    app.add_middleware(CSRFProtectionMiddleware, admin_path=settings.admin.base_url)
    app.add_middleware(
        WebSessionMiddleware,
        admin_path=settings.admin.base_url,
        secret_key=settings.app_secret_key.get_secret_value(),
        session_cookie=settings.web.session_cookie_name,
        max_age=settings.web.session_expiration_time,
        same_site="lax",
        https_only=settings.web.session_https_only,
    )

    logger.info("Setting up routes...")
    app.include_router(system_router, prefix="/api")
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    validate_access_policies(app)

    logger.info("Application configured!")
    return app


if __name__ == "__main__":
    """Prepares App and run uvicorn instance"""
    app: StellarAPP = make_app()
    uvicorn.run(
        app,
        host=app.settings.app_host,
        port=app.settings.app_port,
        log_config=app.settings.log.dict_config_any,
        proxy_headers=True,
    )
