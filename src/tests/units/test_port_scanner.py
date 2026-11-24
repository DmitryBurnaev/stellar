"""Port scan results distinguish open, refused and filtered connections."""

import asyncio
import errno
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules import port_scanner


async def test_tcp_scan_reports_open_refused_and_timed_out(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = MagicMock()
    writer.wait_closed = AsyncMock()

    async def connect(address: str, port: int) -> tuple[None, MagicMock]:
        assert address == "192.0.2.1"
        if port == 2:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")
        if port == 3:
            raise TimeoutError()
        if port == 4:
            raise OSError(errno.EHOSTUNREACH, "filtered")
        if port == 5:
            raise ConnectionResetError(errno.ECONNRESET, "reset")
        return None, writer

    monkeypatch.setattr("src.modules.port_scanner.asyncio.open_connection", connect)
    result = await port_scanner.scan_tcp_ports("192.0.2.1", ports=range(1, 6), concurrency=2)
    assert result["open_tcp_ports"] == [1]
    assert result["checked"] == 5 and result["timed_out"] == 2
    writer.close.assert_called_once()


async def test_tcp_scan_fails_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.modules.port_scanner.asyncio.open_connection",
        AsyncMock(side_effect=OSError(errno.ENETUNREACH, "network unavailable")),
    )
    with pytest.raises(OSError) as error:
        await port_scanner.scan_tcp_ports("2001:db8::1", ports=range(1, 3), concurrency=1)
    assert error.value.errno == errno.ENETUNREACH


async def test_tcp_scan_bounds_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    active = 0
    maximum = 0
    writer = MagicMock()
    writer.wait_closed = AsyncMock()

    async def connect(address: str, port: int) -> tuple[None, MagicMock]:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.001)
        active -= 1
        return None, writer

    monkeypatch.setattr("src.modules.port_scanner.asyncio.open_connection", connect)
    result = await port_scanner.scan_tcp_ports("127.0.0.1", ports=range(1, 11), concurrency=3)
    assert maximum == 3
    assert result["checked"] == 10 and result["open_tcp_ports"] == list(range(1, 11))


async def test_real_tcp_connection_on_loopback() -> None:
    if os.getenv("STELLAR_TEST_LOCAL_NETWORK") != "1":
        pytest.skip("Set STELLAR_TEST_LOCAL_NETWORK=1 to test a real loopback connection")
    server = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        result = await port_scanner.scan_tcp_ports("127.0.0.1", ports=range(port, port + 1))
        assert result["open_tcp_ports"] == [port]
    finally:
        server.close()
        await server.wait_closed()
