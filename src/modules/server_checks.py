"""Read server package and container metadata using fixed SSH commands."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from src.modules.ssh import SSHError, run_ssh_command
from src.settings.app import AppSettings

logger = logging.getLogger(__name__)


class SSHOptions(TypedDict):
    config_file: Path
    known_hosts_file: Path
    connect_timeout: int
    command_timeout: int


COMMANDS = {
    "os": "cat /etc/os-release",
    "packages": "dpkg-query -W -f='${binary:Package}\\t${Version}\\t${Architecture}\\t${db:Status-Abbrev}\\n'",
    "updates": "LC_ALL=C apt list --upgradable 2>/dev/null",
    "apt_checked": "stat -c %Y /var/lib/apt/periodic/update-success-stamp",
    "images": "set -e; images=$(docker ps -a --format '{{.Image}}'); [ -n \"$images\" ] || exit 0; printf '%s\\n' \"$images\" | sort -u | while IFS= read -r image; do printf '%s\\t' \"$image\"; docker image inspect --format '{{json .Id}}\t{{json .RepoDigests}}' \"$image\"; done",
}


def parse_os(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"ID", "ID_LIKE", "PRETTY_NAME", "VERSION_ID"}:
            values[key] = value.strip().strip("\"'")
    return values


def parse_packages(installed: str, upgrades: str) -> list[dict[str, Any]]:
    candidates: dict[tuple[str, str], str] = {}
    for line in upgrades.splitlines():
        parts = line.split()
        if len(parts) >= 3 and "/" in parts[0]:
            candidates[(parts[0].split("/", 1)[0], parts[2])] = parts[1]
    packages: list[dict[str, Any]] = []
    for line in installed.splitlines():
        parts = line.split("\t")
        if len(parts) != 4 or not parts[3].startswith("ii"):
            continue
        name, version, architecture = parts[:3]
        candidate = candidates.get((name.split(":", 1)[0], architecture))
        packages.append(
            {"name": name, "version": version, "architecture": architecture, "candidate": candidate}
        )
    return packages


def parse_images(output: str) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            raise ValueError("Invalid Docker image response")
        image_id = json.loads(parts[1])
        digests = json.loads(parts[2])
        if not isinstance(image_id, str) or not isinstance(digests, (list, type(None))):
            raise ValueError("Invalid Docker image metadata")
        images.append({"name": parts[0], "id": image_id, "digests": digests or []})
    return images


async def collect_server(alias: str, settings: AppSettings) -> dict[str, Any]:
    """Preserve independently successful sections when another command fails."""
    if settings.ssh_config_file is None or settings.ssh_known_hosts_file is None:
        raise ValueError("SSH configuration and known hosts files are required")
    kwargs: SSHOptions = {
        "config_file": settings.ssh_config_file,
        "known_hosts_file": settings.ssh_known_hosts_file,
        "connect_timeout": settings.ssh_connect_timeout,
        "command_timeout": settings.ssh_command_timeout,
    }
    await run_ssh_command(alias, "true", **kwargs)
    result: dict[str, Any] = {"available": True, "sections": {}, "errors": {}}
    output: dict[str, str] = {}
    for section, command in COMMANDS.items():
        try:
            output[section] = await run_ssh_command(alias, command, **kwargs)
        except (SSHError, OSError, TimeoutError):
            logger.warning("Server check section failed: section=%s", section)
            result["errors"][section] = "Could not collect this section"
    sections = result["sections"]
    if "os" in output:
        sections["os"] = parse_os(output["os"])
    if "packages" in output:
        sections["packages"] = parse_packages(output["packages"], output.get("updates", ""))
        sections["updates_known"] = "updates" in output
    if "apt_checked" in output:
        try:
            sections["apt_checked_at"] = datetime.fromtimestamp(
                int(output["apt_checked"].strip()), UTC
            ).isoformat()
        except (ValueError, OverflowError, OSError):
            result["errors"]["apt_checked"] = "Invalid APT index timestamp"
    if "images" in output:
        try:
            sections["images"] = parse_images(output["images"])
        except (ValueError, json.JSONDecodeError):
            result["errors"]["images"] = "Invalid Docker image metadata"
    return result
