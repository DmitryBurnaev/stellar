"""Client for the external TCP scanner API proposed by Stellar."""

from typing import Any
from urllib.parse import quote

import httpx

from src.settings.app import AppSettings


class ScannerError(Exception):
    """The scanner is unavailable or returned an invalid response."""


def scanner_settings(settings: AppSettings) -> tuple[str, str]:
    if not settings.port_scanner_url or not settings.port_scanner_token:
        raise ScannerError("Port scanner is not configured")
    url = settings.port_scanner_url.rstrip("/")
    if not url.startswith("https://"):
        raise ScannerError("Port scanner URL must use HTTPS")
    return url, settings.port_scanner_token.get_secret_value()


async def submit_scan(address: str, key: str, settings: AppSettings) -> str:
    url, token = scanner_settings(settings)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(
                f"{url}/v1/scans",
                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key},
                json={"address": address},
            )
            response.raise_for_status()
            data = response.json()
            if response.status_code != 202 or not isinstance(data, dict):
                raise ScannerError("Invalid scanner response")
            scan_id = data.get("id")
            if not isinstance(scan_id, str) or not scan_id or len(scan_id) > 200:
                raise ScannerError("Invalid scan identifier")
            return scan_id
    except (httpx.HTTPError, ValueError) as exc:
        raise ScannerError("Could not start the port scan") from exc


async def get_scan(scan_id: str, address: str, settings: AppSettings) -> dict[str, Any]:
    url, token = scanner_settings(settings)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(
                f"{url}/v1/scans/{quote(scan_id, safe='')}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ScannerError("Could not retrieve the port scan") from exc
    if not isinstance(data, dict) or data.get("id") != scan_id or data.get("address") != address:
        raise ScannerError("Invalid scanner response")
    if not isinstance(data.get("status"), str) or data["status"] not in {
        "queued",
        "running",
        "failed",
        "succeeded",
    }:
        raise ScannerError("Invalid scan status")
    if data["status"] == "succeeded":
        ports = data.get("open_tcp_ports")
        if not isinstance(ports, list) or any(
            type(port) is not int or not 1 <= port <= 65535 for port in ports
        ):
            raise ScannerError("Invalid scanner port list")
        data["open_tcp_ports"] = sorted(set(ports))
    return data
