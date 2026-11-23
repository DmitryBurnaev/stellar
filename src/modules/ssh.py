"""Run non-interactive commands on configured servers through OpenSSH."""

import asyncio
import re
from contextlib import suppress
from pathlib import Path


class SSHError(Exception):
    """A remote command could not be completed."""


async def run_ssh_command(
    host: str,
    command: str,
    *,
    config_file: Path,
    known_hosts_file: Path,
    connect_timeout: int = 15,
    command_timeout: int = 40,
) -> str:
    """Execute a command with host-key verification and bounded execution time."""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", host) is None:
        raise SSHError("Invalid SSH alias")
    process = await asyncio.create_subprocess_exec(
        "ssh",
        "-F",
        str(config_file),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts_file}",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        "--",
        host,
        command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=command_timeout)
    except (TimeoutError, asyncio.CancelledError):
        with suppress(ProcessLookupError):
            process.kill()
        await process.communicate()
        raise
    if process.returncode != 0:
        diagnostic = stderr.decode(errors="replace").lower()
        if (
            "host key verification failed" in diagnostic
            or "remote host identification has changed" in diagnostic
        ):
            message = "SSH host key verification failed"
        elif "permission denied" in diagnostic:
            message = "SSH authentication or command permission failed"
        elif "could not resolve hostname" in diagnostic:
            message = "SSH host name could not be resolved"
        elif "connection refused" in diagnostic:
            message = "SSH connection was refused"
        elif "connection timed out" in diagnostic or "operation timed out" in diagnostic:
            message = "SSH connection timed out"
        else:
            message = f"SSH command failed (exit {process.returncode})"
        raise SSHError(message)
    return stdout.decode(errors="replace")
