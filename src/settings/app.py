import os
from pathlib import Path
from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.settings.log import LogSettings


def get_default_app_version() -> str:
    """Derive a display version from the deployment image when available."""
    image = os.getenv("DOCKER_IMAGE")
    if image is None:
        return "develop"

    image_name = image.rsplit("/", maxsplit=1)[-1]
    if ":" not in image_name:
        return "latest"

    return image_name.rsplit(":", maxsplit=1)[-1]


class AdminSettings(BaseSettings):
    """Implements settings which are loaded from environment variables"""

    model_config = SettingsConfigDict(env_prefix="ADMIN_", env_file=".env", extra="ignore")

    username: str = Field(default="admin", min_length=1, max_length=128)
    password: SecretStr | None = None
    session_expiration_time: int = 2 * 24 * 3600
    base_url: str = "/admin"
    title: str = "Stellar Admin"


class WebSettings(BaseSettings):
    """Settings for the browser-facing Stellar application."""

    model_config = SettingsConfigDict(env_prefix="WEB_", env_file=".env", extra="ignore")

    session_cookie_name: str = "stellar_web_session"
    session_expiration_time: int = 2 * 24 * 3600
    session_https_only: bool = True


class AppSettings(BaseSettings):
    """Application settings which are loaded from environment variables"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_docs_enabled: bool = False
    app_secret_key: SecretStr = Field(description="Application secret key")
    app_host: str = "localhost"
    app_port: int = 8003
    app_version: str = Field(default_factory=get_default_app_version)
    jwt_algorithm: str = "HS256"
    ssh_config_file: Path | None = None
    ssh_known_hosts_file: Path | None = None
    ssh_connect_timeout: int = Field(default=15, gt=0)
    ssh_command_timeout: int = Field(default=40, gt=0)
    port_scanner_url: str | None = None
    port_scanner_token: SecretStr | None = None
    admin: AdminSettings = Field(default_factory=AdminSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    log: LogSettings = Field(default_factory=LogSettings)
