"""Run bounded TCP scans from the Stellar host and persist their results."""

import asyncio
import errno
import ipaddress
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import sqlalchemy as sa

from src.db import session as db_session
from src.db.diagnostics import failure_details
from src.db.models import ServerCheck
from src.utils import utcnow

logger = logging.getLogger(__name__)
SCANNER_LOCK = 0x5354454C4C4152
PORTS = range(1, 65536)
CONCURRENCY = 128
CONNECT_TIMEOUT = 0.75
Progress = Callable[[int, int], Awaitable[None]]


async def scan_tcp_ports(
    address: str,
    *,
    ports: range = PORTS,
    concurrency: int = CONCURRENCY,
    timeout: float = CONNECT_TIMEOUT,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Connect to each port, distinguishing timeouts from refused connections."""
    address = str(ipaddress.ip_address(address))
    iterator = iter(ports)
    open_ports: list[int] = []
    timed_out = 0
    completed = 0
    progress_lock = asyncio.Lock()

    async def probe() -> None:
        nonlocal completed, timed_out
        for port in iterator:
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(address, port), timeout)
                writer.close()
                with suppress(ConnectionError):
                    await writer.wait_closed()
                open_ports.append(port)
            except TimeoutError:
                timed_out += 1
            except OSError as exc:
                if exc.errno in {errno.ECONNREFUSED, errno.ECONNRESET}:
                    pass
                elif exc.errno in {errno.ETIMEDOUT, errno.EHOSTUNREACH}:
                    timed_out += 1
                else:
                    raise OSError(exc.errno, "TCP scan could not continue") from exc
            completed += 1
            if progress is not None and completed % 512 == 0:
                async with progress_lock:
                    await progress(completed, len(ports))

    try:
        async with asyncio.TaskGroup() as group:
            for _ in range(min(concurrency, len(ports))):
                group.create_task(probe())
    except* OSError as group:
        raise group.exceptions[0]
    return {
        "source": "local",
        "address": address,
        "open_tcp_ports": sorted(open_ports),
        "timed_out": timed_out,
        "checked": completed,
        "total": len(ports),
    }


async def _record_progress(check_id: int, checked: int, total: int) -> None:
    async with db_session.get_session_factory()() as session:
        async with session.begin():
            check = await session.get(ServerCheck, check_id)
            if check is not None and check.status == "running" and check.result:
                check.result = {**check.result, "checked": checked, "total": total}


async def _run_next() -> bool:
    async with db_session.get_session_factory()() as session:
        async with session.begin():
            check = await session.scalar(
                sa.select(ServerCheck)
                .where(ServerCheck.kind == "ports", ServerCheck.status == "queued")
                .order_by(ServerCheck.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if check is None:
                return False
            check_id = check.id
            data = check.result or {}
            if data.get("source") != "local":
                check.status = "failed"
                check.error = "The previous external scan was interrupted"
                check.finished_at = utcnow()
                return True
            try:
                address = str(ipaddress.ip_address(data.get("address", "")))
            except ValueError:
                check.status = "failed"
                check.error = "The scan address is invalid"
                check.finished_at = utcnow()
                return True
            check.status = "running"
            check.started_at = utcnow()

    async def progress(checked: int, total: int) -> None:
        await _record_progress(check_id, checked, total)

    try:
        result = await scan_tcp_ports(address, progress=progress)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("TCP scan failed: check_id=%s details=%s", check_id, failure_details(exc))
        result = None
    async with db_session.get_session_factory()() as session:
        async with session.begin():
            check = await session.get(ServerCheck, check_id)
            if check is not None and check.status == "running":
                check.finished_at = utcnow()
                if result is None:
                    check.status = "failed"
                    check.error = "TCP scan failed; check network connectivity and application logs"
                else:
                    check.status = "succeeded"
                    check.result = result
                    check.error = None
    return True


async def run_scan_worker() -> None:
    """Use a PostgreSQL advisory lock to run one worker across app processes."""
    while True:
        try:
            engine = db_session.AsyncDBConnectors().engine
            if engine is None:
                raise RuntimeError("Database engine is not initialized")
            async with engine.connect() as connection:
                locked = await connection.scalar(
                    sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": SCANNER_LOCK}
                )
                await connection.commit()
                if not locked:
                    await asyncio.sleep(2)
                    continue
                try:
                    async with db_session.get_session_factory()() as session:
                        async with session.begin():
                            await session.execute(
                                sa.update(ServerCheck)
                                .where(
                                    ServerCheck.kind == "ports",
                                    ServerCheck.status.in_(["running", "submitting"]),
                                )
                                .values(
                                    status="failed",
                                    error="Scan interrupted by application restart",
                                    finished_at=utcnow(),
                                )
                            )
                    while True:
                        if not await _run_next():
                            await asyncio.sleep(2)
                finally:
                    with suppress(Exception):
                        await connection.execute(
                            sa.text("SELECT pg_advisory_unlock(:key)"), {"key": SCANNER_LOCK}
                        )
                        await connection.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("TCP scan worker failed; retrying: %s", failure_details(exc))
            await asyncio.sleep(2)
