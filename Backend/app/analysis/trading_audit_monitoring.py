"""
GOLD BOT - Trading Audit & Monitoring

Structured audit events, monitoring summaries,
system health observations and security event tracking.

This module:
- Does not execute trades.
- Does not connect directly to MT5.
- Does not replace persistent audit storage.
- Does not claim that an event is independently verified.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite
from typing import Any
from uuid import uuid4


VALID_SEVERITIES = {
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
}

VALID_EVENT_TYPES = {
    "ANALYSIS",
    "SIGNAL_CREATED",
    "SIGNAL_UPDATED",
    "SIGNAL_CANCELLED",
    "SIGNAL_EXPIRED",
    "TRADE_REQUESTED",
    "TRADE_ACCEPTED",
    "TRADE_REJECTED",
    "TRADE_CLOSED",
    "RISK_REJECTED",
    "DATA_QUALITY",
    "CONNECTION",
    "SECURITY",
    "CONFIGURATION",
    "SYSTEM",
    "ERROR",
}

SENSITIVE_KEYWORDS = {
    "password",
    "secret",
    "token",
    "api_key",
    "access_token",
    "refresh_token",
    "authorization",
    "private_key",
    "jwt",
    "credential",
}

MAXIMUM_DETAIL_DEPTH = 8
MAXIMUM_COLLECTION_ITEMS = 1000
MAXIMUM_TEXT_LENGTH = 1000


class AuditError(ValueError):
    """Raised when an audit event is invalid."""


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AuditError(
            "timestamp must be a non-empty ISO-8601 string."
        )

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        raise AuditError(
            "Invalid timestamp."
        ) from None

    if parsed.tzinfo is None:
        raise AuditError(
            "timestamp must include timezone information."
        )

    return parsed.astimezone(timezone.utc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize(
    value: Any,
    depth: int = 0,
    seen: set[int] | None = None,
) -> Any:
    if depth > MAXIMUM_DETAIL_DEPTH:
        return "[MAX_DEPTH]"

    if seen is None:
        seen = set()

    if isinstance(value, Mapping):
        object_id = id(value)

        if object_id in seen:
            return "[CIRCULAR_REFERENCE]"

        seen.add(object_id)

        result = {}

        try:
            for key, item in list(value.items())[
                :MAXIMUM_COLLECTION_ITEMS
            ]:
                key_text = str(key)[:200]

                normalized = (
                    key_text.lower()
                    .replace("-", "_")
                    .replace(" ", "_")
                )

                if any(
                    keyword in normalized
                    for keyword in SENSITIVE_KEYWORDS
                ):
                    result[key_text] = "[REDACTED]"
                    continue

                result[key_text] = _sanitize(
                    item,
                    depth + 1,
                    seen,
                )
        finally:
            seen.discard(object_id)

        return result

    if isinstance(value, (list, tuple)):
        object_id = id(value)

        if object_id in seen:
            return "[CIRCULAR_REFERENCE]"

        seen.add(object_id)

        try:
            return [
                _sanitize(item, depth + 1, seen)
                for item in value[:MAXIMUM_COLLECTION_ITEMS]
            ]
        finally:
            seen.discard(object_id)

    if isinstance(value, float):
        if not isfinite(value):
            return "[NON_FINITE_NUMBER]"
        return value

    if isinstance(value, str):
        return value[:MAXIMUM_TEXT_LENGTH]

    if value is None or isinstance(value, (int, bool)):
        return value

    return str(value)[:MAXIMUM_TEXT_LENGTH]


def create_audit_event(
    event_type: str,
    severity: str,
    component: str,
    action: str,
    outcome: str,
    timestamp: str | None = None,
    actor: str = "SYSTEM",
    correlation_id: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(event_type, str):
        raise AuditError("event_type must be a string.")

    if not isinstance(severity, str):
        raise AuditError("severity must be a string.")

    event_type = event_type.strip().upper()
    severity = severity.strip().upper()

    if event_type not in VALID_EVENT_TYPES:
        raise AuditError("Unsupported event_type.")

    if severity not in VALID_SEVERITIES:
        raise AuditError("Unsupported severity.")

    for name, value in {
        "component": component,
        "action": action,
        "outcome": outcome,
        "actor": actor,
    }.items():
        if not isinstance(value, str) or not value.strip():
            raise AuditError(
                f"{name} cannot be empty."
            )

    if details is not None and not isinstance(details, Mapping):
        raise AuditError("details must be a mapping.")

    event_time = _parse_timestamp(
        _utc_now() if timestamp is None else timestamp
    )

    if correlation_id is not None:
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
        ):
            raise AuditError(
                "correlation_id must be a non-empty string."
            )

    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "severity": severity,
        "component": component.strip()[:200],
        "action": action.strip()[:200],
        "outcome": outcome.strip()[:500],
        "timestamp": event_time.isoformat(),
        "actor": actor.strip()[:200],
        "correlation_id": (
            correlation_id.strip()[:200]
            if correlation_id is not None
            else None
        ),
        "details": _sanitize(details or {}),
        "schema_version": 1,
    }


class AuditMonitor:
    """Bounded in-memory audit monitor."""

    def __init__(self, max_events: int = 10000):
        if (
            isinstance(max_events, bool)
            or not isinstance(max_events, int)
            or max_events < 1
        ):
            raise AuditError(
                "max_events must be a positive integer."
            )

        self.max_events = max_events
        self._events: list[dict[str, Any]] = []
        self._event_ids: set[str] = set()

    def record(
        self,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(event, Mapping):
            raise AuditError("event must be a mapping.")

        required_strings = (
            "event_id",
            "event_type",
            "severity",
            "component",
            "action",
            "outcome",
            "actor",
        )

        for field in required_strings:
            value = event.get(field)

            if not isinstance(value, str) or not value.strip():
                raise AuditError(
                    f"{field} is required."
                )

        event_id = event["event_id"]

        if len(event_id) > 200:
            raise AuditError("event_id is too long.")

        if event_id in self._event_ids:
            raise AuditError("Duplicate event_id.")

        event_type = event["event_type"].strip().upper()
        severity = event["severity"].strip().upper()

        if event_type not in VALID_EVENT_TYPES:
            raise AuditError("Invalid event type.")

        if severity not in VALID_SEVERITIES:
            raise AuditError("Invalid event severity.")

        timestamp = _parse_timestamp(
            event.get("timestamp")
        )

        details = event.get("details", {})

        if not isinstance(details, Mapping):
            raise AuditError("details must be a mapping.")

        correlation_id = event.get("correlation_id")

        if correlation_id is not None and (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
        ):
            raise AuditError("Invalid correlation_id.")

        stored = _sanitize(dict(event))

        stored["event_id"] = event_id
        stored["event_type"] = event_type
        stored["severity"] = severity
        stored["timestamp"] = timestamp.isoformat()
        stored["details"] = _sanitize(details)

        self._events.append(stored)
        self._event_ids.add(event_id)

        while len(self._events) > self.max_events:
            removed = self._events.pop(0)
            self._event_ids.discard(removed["event_id"])

        return dict(stored)

    def record_action(
        self,
        event_type: str,
        severity: str,
        component: str,
        action: str,
        outcome: str,
        details: Mapping[str, Any] | None = None,
        actor: str = "SYSTEM",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        event = create_audit_event(
            event_type=event_type,
            severity=severity,
            component=component,
            action=action,
            outcome=outcome,
            actor=actor,
            correlation_id=correlation_id,
            details=details,
        )

        return self.record(event)

    def query(
        self,
        event_type: str | None = None,
        severity: str | None = None,
        component: str | None = None,
        correlation_id: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
        ):
            raise AuditError("limit must be positive.")

        if event_type is not None:
            if not isinstance(event_type, str):
                raise AuditError("Invalid event_type filter.")
            event_type = event_type.strip().upper()
            if event_type not in VALID_EVENT_TYPES:
                raise AuditError("Unknown event_type filter.")

        if severity is not None:
            if not isinstance(severity, str):
                raise AuditError("Invalid severity filter.")
            severity = severity.strip().upper()
            if severity not in VALID_SEVERITIES:
                raise AuditError("Unknown severity filter.")

        for name, value in {
            "component": component,
            "correlation_id": correlation_id,
        }.items():
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise AuditError(
                    f"Invalid {name} filter."
                )

        start = (
            _parse_timestamp(start_time)
            if start_time is not None
            else None
        )

        end = (
            _parse_timestamp(end_time)
            if end_time is not None
            else None
        )

        if start and end and start > end:
            raise AuditError(
                "start_time cannot exceed end_time."
            )

        results = []

        for event in reversed(self._events):
            if (
                event_type
                and event["event_type"] != event_type
            ):
                continue

            if severity and event["severity"] != severity:
                continue

            if component and event["component"] != component:
                continue

            if (
                correlation_id
                and event.get("correlation_id") != correlation_id
            ):
                continue

            event_time = _parse_timestamp(event["timestamp"])

            if start and event_time < start:
                continue

            if end and event_time > end:
                continue

            results.append(dict(event))

            if len(results) >= limit:
                break

        return results

    def monitoring_summary(self) -> dict[str, Any]:
        severity_counts = Counter()
        type_counts = Counter()
        component_counts = Counter()

        for event in self._events:
            severity_counts[event["severity"]] += 1
            type_counts[event["event_type"]] += 1
            component_counts[event["component"]] += 1

        return {
            "total_events": len(self._events),
            "severity_counts": dict(severity_counts),
            "event_type_counts": dict(type_counts),
            "component_counts": dict(component_counts),
            "critical_events": severity_counts["CRITICAL"],
            "error_events": severity_counts["ERROR"],
            "warning_events": severity_counts["WARNING"],
            "monitoring_status": (
                "ATTENTION_REQUIRED"
                if (
                    severity_counts["CRITICAL"]
                    or severity_counts["ERROR"]
                )
                else "NO_RECORDED_ERRORS"
            ),
            "storage": "IN_MEMORY_ONLY",
            "durable": False,
        }

    def recent_critical_events(
        self,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self.query(
            severity="CRITICAL",
            limit=limit,
        )

    def export_snapshot(self) -> dict[str, Any]:
        return {
            "exported_at": _utc_now(),
            "event_count": len(self._events),
            "events": [
                dict(event)
                for event in self._events
            ],
            "storage": "IN_MEMORY_ONLY",
            "integrity_protected": False,
        }


def evaluate_component_health(
    component: str,
    checks: Sequence[Mapping[str, Any]],
    observed_at: str | None = None,
) -> dict[str, Any]:
    if not isinstance(component, str) or not component.strip():
        raise AuditError("component cannot be empty.")

    if (
        isinstance(checks, (str, bytes))
        or not isinstance(checks, Sequence)
        or not checks
    ):
        raise AuditError(
            "At least one health check is required."
        )

    results = []

    for check in checks:
        if not isinstance(check, Mapping):
            raise AuditError(
                "Each health check must be a mapping."
            )

        name = check.get("name")
        passed = check.get("passed")
        message = check.get("message", "")

        if not isinstance(name, str) or not name.strip():
            raise AuditError(
                "Health check name is required."
            )

        if not isinstance(passed, bool):
            raise AuditError(
                "Health check passed must be boolean."
            )

        if not isinstance(message, str):
            raise AuditError(
                "Health check message must be a string."
            )

        results.append({
            "name": name.strip()[:200],
            "passed": passed,
            "message": message[:MAXIMUM_TEXT_LENGTH],
        })

    failed = [
        item for item in results
        if not item["passed"]
    ]

    return {
        "component": component.strip(),
        "status": "HEALTHY" if not failed else "DEGRADED",
        "observed_at": _parse_timestamp(
            _utc_now() if observed_at is None else observed_at
        ).isoformat(),
        "total_checks": len(results),
        "passed_checks": len(results) - len(failed),
        "failed_checks": len(failed),
        "checks": results,
    }


def detect_monitoring_alerts(
    summary: Mapping[str, Any],
    maximum_errors: int = 0,
    maximum_critical: int = 0,
) -> list[dict[str, Any]]:
    if not isinstance(summary, Mapping):
        raise AuditError("summary must be a mapping.")

    for name, value in {
        "maximum_errors": maximum_errors,
        "maximum_critical": maximum_critical,
    }.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise AuditError(
                f"{name} must be a non-negative integer."
            )

    errors = summary.get("error_events", 0)
    critical = summary.get("critical_events", 0)

    for name, value in {
        "error_events": errors,
        "critical_events": critical,
    }.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise AuditError(
                f"Invalid {name} value."
            )

    alerts = []

    if errors > maximum_errors:
        alerts.append({
            "alert": "ERROR_THRESHOLD_EXCEEDED",
            "severity": "ERROR",
            "observed": errors,
            "threshold": maximum_errors,
        })

    if critical > maximum_critical:
        alerts.append({
            "alert": "CRITICAL_THRESHOLD_EXCEEDED",
            "severity": "CRITICAL",
            "observed": critical,
            "threshold": maximum_critical,
        })

    return alerts
