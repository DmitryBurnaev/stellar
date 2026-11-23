"""Collection and external scanner behavior."""

from pathlib import Path
import os
from unittest.mock import AsyncMock, MagicMock

import httpx
from pydantic import SecretStr
import pytest

from src.modules import port_scanner, server_checks, ssh
from src.modules.ssh import SSHError
from src.settings.app import AppSettings


@pytest.mark.parametrize(
    "os_release, expected",
    [
        (
            'ID=debian\nPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nVERSION_ID="12"\n',
            "Debian GNU/Linux 12 (bookworm)",
        ),
        ('ID=ubuntu\nPRETTY_NAME="Ubuntu 24.04 LTS"\nVERSION_ID="24.04"\n', "Ubuntu 24.04 LTS"),
        ('ID=armbian\nID_LIKE=debian\nPRETTY_NAME="Armbian"\nVERSION_ID="24.11"\n', "Armbian"),
    ],
)
def test_os_release_formats(os_release: str, expected: str) -> None:
    assert server_checks.parse_os(os_release)["PRETTY_NAME"] == expected


def test_package_versions_and_docker_metadata() -> None:
    packages = server_checks.parse_packages(
        "openssl\t1.0\tarm64\tii \nlibssl:arm64\t2.0\tarm64\tii \nold\t1\tarm64\trc \n",
        "Listing...\nopenssl/stable 1.1 arm64 [upgradable from: 1.0]\n",
    )
    assert packages == [
        {"name": "openssl", "version": "1.0", "architecture": "arm64", "candidate": "1.1"},
        {"name": "libssl:arm64", "version": "2.0", "architecture": "arm64", "candidate": None},
    ]
    assert server_checks.parse_images('postgres:17\t"sha256:123"\t["postgres@sha256:abc"]\n') == [
        {"name": "postgres:17", "id": "sha256:123", "digests": ["postgres@sha256:abc"]}
    ]


async def test_collection_keeps_successful_sections_when_docker_fails(
    app_settings_test: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_settings_test.ssh_config_file = Path("/config")
    app_settings_test.ssh_known_hosts_file = Path("/known_hosts")

    async def run(host: str, command: str, **kwargs: object) -> str:
        assert host == "server-alias"
        if command == "true":
            return ""
        outputs = {
            server_checks.COMMANDS["os"]: 'ID=debian\nPRETTY_NAME="Debian 12"\n',
            server_checks.COMMANDS["packages"]: "openssl\t1.0\tamd64\tii \n",
            server_checks.COMMANDS["updates"]: "openssl/stable 1.1 amd64\n",
            server_checks.COMMANDS["apt_checked"]: "1700000000\n",
        }
        if command == server_checks.COMMANDS["images"]:
            raise SSHError("sensitive remote error")
        return outputs[command]

    monkeypatch.setattr(server_checks, "run_ssh_command", run)
    result = await server_checks.collect_server("server-alias", app_settings_test)
    assert result["available"] is True
    assert result["sections"]["os"]["PRETTY_NAME"] == "Debian 12"
    assert result["sections"]["packages"][0]["candidate"] == "1.1"
    assert result["errors"] == {"images": "Could not collect this section"}


async def test_scanner_submit_and_result(
    app_settings_test: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_settings_test.port_scanner_url = "https://scanner.example.test"
    app_settings_test.port_scanner_token = SecretStr("test-token")
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["authorization"] == "Bearer test-token"
        if request.method == "POST":
            assert request.headers["idempotency-key"] == "stable-key"
            return httpx.Response(202, json={"id": "scan-1"})
        return httpx.Response(
            200,
            json={
                "id": "scan-1",
                "address": "192.0.2.1",
                "status": "succeeded",
                "open_tcp_ports": [443, 22, 22],
            },
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        "src.modules.port_scanner.httpx.AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(handle)),
    )
    scan_id = await port_scanner.submit_scan("192.0.2.1", "stable-key", app_settings_test)
    result = await port_scanner.get_scan(scan_id, "192.0.2.1", app_settings_test)
    assert result["open_tcp_ports"] == [22, 443]
    assert len(calls) == 2


async def test_ssh_unavailable_does_not_claim_success(
    app_settings_test: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_settings_test.ssh_config_file = Path("/config")
    app_settings_test.ssh_known_hosts_file = Path("/known_hosts")
    monkeypatch.setattr(server_checks, "run_ssh_command", AsyncMock(side_effect=SSHError("failed")))
    with pytest.raises(SSHError):
        await server_checks.collect_server("server-alias", app_settings_test)


async def test_ssh_uses_known_hosts_and_redacts_remote_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = MagicMock()
    process.returncode = 255
    process.communicate = AsyncMock(return_value=(b"", b"Permission denied: secret remote detail"))
    start = AsyncMock(return_value=process)
    monkeypatch.setattr("src.modules.ssh.asyncio.create_subprocess_exec", start)
    with pytest.raises(SSHError, match="SSH authentication or command permission failed") as error:
        await ssh.run_ssh_command(
            "server-alias",
            "true",
            config_file=Path("/config"),
            known_hosts_file=Path("/known_hosts"),
        )
    assert "secret" not in str(error.value)
    assert start.await_args is not None
    assert "StrictHostKeyChecking=yes" in start.await_args.args
    assert "UserKnownHostsFile=/known_hosts" in start.await_args.args
    assert start.await_args.args[-2:] == ("server-alias", "true")
    with pytest.raises(SSHError, match="Invalid SSH alias"):
        await ssh.run_ssh_command(
            "-oProxyCommand=bad",
            "true",
            config_file=Path("/config"),
            known_hosts_file=Path("/known_hosts"),
        )


@pytest.mark.parametrize(
    "status,ports", [("succeeded", [True]), ("succeeded", [65536]), (["bad"], [])]
)
async def test_scanner_rejects_malformed_responses(
    app_settings_test: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
    status: object,
    ports: list[object],
) -> None:
    app_settings_test.port_scanner_url = "https://scanner.example.test"
    app_settings_test.port_scanner_token = SecretStr("test-token")
    client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "id": "scan-1",
                "address": "192.0.2.1",
                "status": status,
                "open_tcp_ports": ports,
            },
        )
    )
    monkeypatch.setattr(
        "src.modules.port_scanner.httpx.AsyncClient", lambda **kwargs: client(transport=transport)
    )
    with pytest.raises(port_scanner.ScannerError):
        await port_scanner.get_scan("scan-1", "192.0.2.1", app_settings_test)


async def test_real_ssh_collection(app_settings_test: AppSettings, tmp_path: Path) -> None:
    config = os.getenv("STELLAR_TEST_SSH_CONFIG")
    known_hosts = os.getenv("STELLAR_TEST_SSH_KNOWN_HOSTS")
    if not config or not known_hosts:
        pytest.skip(
            "Set STELLAR_TEST_SSH_CONFIG and STELLAR_TEST_SSH_KNOWN_HOSTS for SSH integration"
        )
    app_settings_test.ssh_config_file = Path(config)
    app_settings_test.ssh_known_hosts_file = Path(known_hosts)
    alias = os.getenv("STELLAR_TEST_SSH_ALIAS", "stellar-test")
    expected_os = os.getenv("STELLAR_TEST_SSH_EXPECTED_OS", "debian")
    result = await server_checks.collect_server(alias, app_settings_test)
    assert result["sections"]["os"]["ID"] == expected_os
    assert result["sections"]["packages"]
    assert result["sections"]["updates_known"] is True
    assert "images" in result["errors"] or "images" in result["sections"]
    empty_known_hosts = tmp_path / "known_hosts"
    empty_known_hosts.touch()
    app_settings_test.ssh_known_hosts_file = empty_known_hosts
    with pytest.raises(SSHError, match="host key verification"):
        await server_checks.collect_server(alias, app_settings_test)
