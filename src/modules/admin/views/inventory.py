"""SQLAdmin views for the server inventory."""

from ipaddress import ip_address
from typing import Any

from starlette.requests import Request
from wtforms.validators import IPAddress, Optional, Regexp

from src.db.models import Application, Provider, Server, ServerDomain, ServerGroup
from src.modules.admin.views.base import BaseModelView
from src.modules.auth.permissions import Permission


class ProviderAdminView(BaseModelView, model=Provider):
    required_permission = Permission.INVENTORY_ADMIN
    icon = "fa-solid fa-cloud"
    column_list = (Provider.id, Provider.name, Provider.description)
    form_columns = (Provider.name, Provider.description)


class ApplicationAdminView(BaseModelView, model=Application):
    required_permission = Permission.INVENTORY_ADMIN
    icon = "fa-solid fa-box"
    column_list = (Application.id, Application.name, Application.description)
    form_columns = (Application.name, Application.description)


class ServerGroupAdminView(BaseModelView, model=ServerGroup):
    required_permission = Permission.INVENTORY_ADMIN
    icon = "fa-solid fa-layer-group"
    column_list = (ServerGroup.id, ServerGroup.name, ServerGroup.description)
    form_columns = (ServerGroup.name, ServerGroup.description, ServerGroup.servers)


class ServerAdminView(BaseModelView, model=Server):
    required_permission = Permission.INVENTORY_ADMIN
    icon = "fa-solid fa-server"
    column_labels = {
        Server.ip_address: "IP address",
        Server.scan_address: "External scan address",
        Server.ssh_alias: "SSH alias",
        Server.os_name: "Operating system",
        Server.os_version: "OS version",
        Server.last_updated_at: "Last manual update (UTC)",
    }
    form_args = {
        "ip_address": {"validators": [IPAddress(ipv4=True, ipv6=True)]},
        "scan_address": {"validators": [Optional(), IPAddress(ipv4=True, ipv6=True)]},
        "ssh_alias": {"validators": [Regexp(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")]},
    }
    column_list = (Server.id, Server.ip_address, Server.provider, Server.os_name, Server.os_version)
    form_columns = (
        Server.ip_address,
        Server.scan_address,
        Server.ssh_alias,
        Server.provider,
        Server.os_name,
        Server.os_version,
        Server.last_updated_at,
        Server.groups,
        Server.applications,
    )

    async def on_model_change(
        self, data: dict[str, Any], model: Any, is_created: bool, request: Request
    ) -> None:
        data["ip_address"] = str(ip_address(data["ip_address"]))
        model.ip_address = data["ip_address"]
        if data.get("scan_address"):
            data["scan_address"] = str(ip_address(data["scan_address"]))


class ServerDomainAdminView(BaseModelView, model=ServerDomain):
    required_permission = Permission.INVENTORY_ADMIN
    icon = "fa-solid fa-globe"
    form_args = {
        "name": {
            "validators": [
                Regexp(
                    r"(?i)^(?=.{1,253}[.]?$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?[.])*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?[.]?$"
                )
            ]
        }
    }
    column_list = (ServerDomain.id, ServerDomain.name, ServerDomain.server)
    form_columns = (ServerDomain.server, ServerDomain.name)

    async def on_model_change(
        self, data: dict[str, Any], model: Any, is_created: bool, request: Request
    ) -> None:
        data["name"] = data["name"].lower().rstrip(".")
