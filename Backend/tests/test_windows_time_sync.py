
from datetime import datetime, timezone

from app.core.windows_time_sync import inspect_windows_time_sync


NOW = datetime(
    2026, 11, 12, 12, 0,
    tzinfo=timezone.utc,
)


def runner_for(service, status):
    outputs = {
        ("sc.exe", "query", "w32time"): service,
        ("w32tm", "/query", "/status"): status,
    }

    def run(command):
        return outputs[tuple(command)]

    return run


GOOD_SERVICE = (
    "SERVICE_NAME: w32time\n"
    "STATE              : 4  RUNNING"
)

GOOD_STATUS = """Leap Indicator: 0(no warning)
Stratum: 3 (secondary reference - syncd by (S)NTP)
Source: time.windows.com
Last Successful Sync Time: 11/12/2026 11:30:00 AM
"""


def test_non_windows_host_is_unverified():
    result = inspect_windows_time_sync(
        platform_name="Linux",
        now_utc=NOW,
    )

    assert result["status"] == "UNVERIFIED"


def test_recent_external_windows_sync_is_verified():
    result = inspect_windows_time_sync(
        platform_name="Windows",
        command_runner=runner_for(
            GOOD_SERVICE,
            GOOD_STATUS,
        ),
        now_utc=NOW,
    )

    assert result["status"] == "VERIFIED"
    assert result["source"] == "time.windows.com"
    assert result["stratum"] == 3


def test_stopped_windows_time_service_is_unverified():
    result = inspect_windows_time_sync(
        platform_name="Windows",
        command_runner=runner_for(
            "STATE : 1 STOPPED",
            GOOD_STATUS,
        ),
        now_utc=NOW,
    )

    assert result["status"] == "UNVERIFIED"


def test_local_cmos_clock_is_unverified():
    status = GOOD_STATUS.replace(
        "time.windows.com",
        "Local CMOS Clock",
    )

    result = inspect_windows_time_sync(
        platform_name="Windows",
        command_runner=runner_for(
            GOOD_SERVICE,
            status,
        ),
        now_utc=NOW,
    )

    assert result["status"] == "UNVERIFIED"


def test_old_sync_is_unverified():
    status = GOOD_STATUS.replace(
        "11/12/2026 11:30:00 AM",
        "11/10/2026 11:30:00 AM",
    )

    result = inspect_windows_time_sync(
        platform_name="Windows",
        command_runner=runner_for(
            GOOD_SERVICE,
            status,
        ),
        now_utc=NOW,
    )

    assert result["status"] == "UNVERIFIED"


def test_unsupported_locale_is_unverified():
    result = inspect_windows_time_sync(
        platform_name="Windows",
        command_runner=runner_for(
            GOOD_SERVICE,
            "مصدر الوقت: server\nحالة المزامنة: good",
        ),
        now_utc=NOW,
    )

    assert result["status"] == "UNVERIFIED"


def test_command_failure_is_unverified():
    def fail(_command):
        raise OSError("not available")

    result = inspect_windows_time_sync(
        platform_name="Windows",
        command_runner=fail,
        now_utc=NOW,
    )

    assert result["status"] == "UNVERIFIED"
