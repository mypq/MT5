
"""Conservative Windows host clock synchronization inspection.

This module checks the BACKEND HOST clock only.
It does not verify MT5 broker time or authorize trading.
"""

import platform
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


@dataclass(frozen=True)
class WindowsTimeSyncStatus:
    status: str
    source: str | None
    stratum: int | None
    last_successful_sync_utc: str | None
    checked_at_utc: str
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def _run(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=8,
        check=False,
        shell=False,
    )

    if completed.returncode != 0:
        raise RuntimeError("Windows time inspection command failed")

    return completed.stdout


def _parse_last_sync(value: str) -> datetime | None:
    value = value.strip()

    if not value or value.lower() in {"unspecified", "never"}:
        return None

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            return None

        return parsed.astimezone(timezone.utc)

    except ValueError:
        pass

    formats = (
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    )

    for date_format in formats:
        try:
            return datetime.strptime(
                value,
                date_format,
            ).replace(tzinfo=timezone.utc)

        except ValueError:
            continue

    return None


def inspect_windows_time_sync(
    *,
    platform_name: str | None = None,
    command_runner: Callable[[list[str]], str] | None = None,
    now_utc: datetime | None = None,
) -> dict:
    """Inspect Windows Time Service; fail closed when evidence is incomplete."""

    checked_at = now_utc or datetime.now(timezone.utc)

    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware")

    checked_at = checked_at.astimezone(timezone.utc)

    def result(
        status: str,
        reason: str,
        source: str | None = None,
        stratum: int | None = None,
        last_sync: datetime | None = None,
    ) -> dict:
        return WindowsTimeSyncStatus(
            status=status,
            source=source,
            stratum=stratum,
            last_successful_sync_utc=(
                last_sync.isoformat() if last_sync else None
            ),
            checked_at_utc=checked_at.isoformat(),
            reason=reason,
        ).as_dict()

    if (platform_name or platform.system()).lower() != "windows":
        return result(
            "UNVERIFIED",
            "Backend host is not Windows; Windows Time Service was not checked.",
        )

    run = command_runner or _run

    try:
        service_output = run([
            "sc.exe",
            "query",
            "w32time",
        ])

        status_output = run([
            "w32tm",
            "/query",
            "/status",
        ])

    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        return result(
            "UNVERIFIED",
            f"Unable to inspect Windows Time Service: {type(exc).__name__}.",
        )

    service_running = bool(
        re.search(
            r"STATE\s*:\s*4\s+RUNNING",
            service_output,
            re.IGNORECASE,
        )
    )

    if not service_running:
        return result(
            "UNVERIFIED",
            "Windows Time Service is not confirmed as RUNNING.",
        )

    leap_match = re.search(
        r"Leap Indicator\s*:\s*(\d+)",
        status_output,
        re.IGNORECASE,
    )

    stratum_match = re.search(
        r"Stratum\s*:\s*(\d+)",
        status_output,
        re.IGNORECASE,
    )

    source_match = re.search(
        r"^\s*Source\s*:\s*(.+?)\s*$",
        status_output,
        re.IGNORECASE | re.MULTILINE,
    )

    sync_match = re.search(
        r"^\s*Last Successful Sync Time\s*:\s*(.+?)\s*$",
        status_output,
        re.IGNORECASE | re.MULTILINE,
    )

    if not all((
        leap_match,
        stratum_match,
        source_match,
        sync_match,
    )):
        return result(
            "UNVERIFIED",
            "w32tm output is incomplete or uses an unsupported locale; no trust decision was made.",
        )

    leap_indicator = int(leap_match.group(1))
    stratum = int(stratum_match.group(1))
    source = source_match.group(1).strip()
    last_sync = _parse_last_sync(sync_match.group(1))

    if not source or source.lower() in {
        "local cmos clock",
        "free-running system clock",
    }:
        return result(
            "UNVERIFIED",
            "Windows reports no external time source.",
            source,
            stratum,
            last_sync,
        )

    if leap_indicator != 0 or not 1 <= stratum <= 15:
        return result(
            "UNVERIFIED",
            "Windows reports a leap warning or an invalid/untrusted stratum.",
            source,
            stratum,
            last_sync,
        )

    if last_sync is None:
        return result(
            "UNVERIFIED",
            "Last successful synchronization time could not be safely parsed.",
            source,
            stratum,
        )

    age = checked_at - last_sync

    if age < timedelta(minutes=-2) or age > timedelta(hours=24):
        return result(
            "UNVERIFIED",
            "Last successful synchronization is in the future or older than 24 hours.",
            source,
            stratum,
            last_sync,
        )

    return result(
        "VERIFIED",
        "Windows Time Service is running and reports a recent successful sync with a valid external source and stratum.",
        source,
        stratum,
        last_sync,
    )
