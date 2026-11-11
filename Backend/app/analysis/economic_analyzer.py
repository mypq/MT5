المسار: "Backend/app/analysis/economic_analyzer.py"

"""
GOLD BOT - Advanced Economic Analyzer

Features:
- Economic observation validation
- Actual vs Forecast comparison
- Previous value comparison
- Previous value revision detection
- Economic surprise calculation
- Timestamp and freshness validation
- Economic calendar monitoring
- Event release status tracking
- Data quality classification

Important:
This module does not fetch live economic data.
A trusted provider must supply timestamped observations.

A positive or negative economic surprise does not
automatically determine gold direction.

This module performs analysis only.
It never generates or executes trading orders.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite
from typing import Any


SUPPORTED_INDICATORS = {
    "FED_RATE",
    "CPI",
    "CORE_CPI",
    "PCE",
    "CORE_PCE",
    "NONFARM_PAYROLLS",
    "UNEMPLOYMENT_RATE",
    "US_10Y_YIELD",
    "US_2Y_YIELD",
    "DXY",
}

SUPPORTED_EVENT_TYPES = {
    "FED_DECISION",
    "CPI_RELEASE",
    "PCE_RELEASE",
    "NFP_RELEASE",
    "UNEMPLOYMENT_RELEASE",
    "FED_SPEECH",
    "TREASURY_AUCTION",
    "OTHER",
}

MAX_DATA_AGE_HOURS = 24
MAX_FUTURE_TIMESTAMP_MINUTES = 5


class EconomicAnalysisError(ValueError):
    """Raised when economic observations are invalid."""


def _parse_timestamp(
    value: Any,
    field: str,
) -> datetime:
    if isinstance(value, datetime):
        timestamp = value

    elif isinstance(value, str):
        try:
            normalized = value.strip().replace(
                "Z", "+00:00"
            )
            timestamp = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise EconomicAnalysisError(
                f"Invalid ISO-8601 timestamp: {field}."
            ) from exc

    else:
        raise EconomicAnalysisError(
            f"{field} must be an ISO-8601 string or datetime."
        )

    if timestamp.tzinfo is None:
        raise EconomicAnalysisError(
            f"{field} must include a timezone."
        )

    return timestamp.astimezone(timezone.utc)


def _read_number(
    value: Any,
    field: str,
    allow_none: bool = False,
) -> float | None:
    if value is None and allow_none:
        return None

    if isinstance(value, bool):
        raise EconomicAnalysisError(
            f"{field} cannot be boolean."
        )

    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EconomicAnalysisError(
            f"Invalid numeric value: {field}."
        ) from exc

    if not isfinite(result):
        raise EconomicAnalysisError(
            f"{field} must be finite."
        )

    return result


def _validate_sequence(
    value: Any,
    field: str,
) -> Sequence:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes)
    ):
        raise EconomicAnalysisError(
            f"{field} must be a sequence."
        )

    return value


def _validate_observations(
    observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    items = _validate_sequence(
        observations,
        "observations",
    )

    validated = []

    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise EconomicAnalysisError(
                f"Invalid observation at index {index}."
            )

        indicator = item.get("indicator")

        if (
            not isinstance(indicator, str)
            or indicator not in SUPPORTED_INDICATORS
        ):
            raise EconomicAnalysisError(
                f"Unsupported indicator at index {index}."
            )

        source = item.get("source")

        if not isinstance(source, str) or not source.strip():
            raise EconomicAnalysisError(
                f"Observation {index} requires a source."
            )

        actual_value = item.get(
            "actual",
            item.get("value"),
        )

        actual = _read_number(
            actual_value,
            "actual",
        )

        forecast = _read_number(
            item.get("forecast"),
            "forecast",
            allow_none=True,
        )

        previous = _read_number(
            item.get("previous"),
            "previous",
            allow_none=True,
        )

        revised_previous = _read_number(
            item.get("revised_previous"),
            "revised_previous",
            allow_none=True,
        )

        timestamp = _parse_timestamp(
            item.get("timestamp"),
            "timestamp",
        )

        verified = item.get("verified")

        if not isinstance(verified, bool):
            raise EconomicAnalysisError(
                "verified must be a boolean."
            )

        unit = item.get("unit")

        if unit is not None and (
            not isinstance(unit, str) or not unit.strip()
        ):
            raise EconomicAnalysisError(
                "unit must be a non-empty string or None."
            )

        revision_detected = (
            previous is not None
            and revised_previous is not None
            and previous != revised_previous
        )

        validated.append({
            "indicator": indicator,
            "actual": actual,
            "forecast": forecast,
            "previous": previous,
            "revised_previous": revised_previous,
            "revision_detected": revision_detected,
            "timestamp": timestamp,
            "source": source.strip(),
            "verified": verified,
            "unit": unit.strip() if unit else None,
        })

    return validated


def _validate_events(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    items = _validate_sequence(
        events,
        "events",
    )

    validated = []

    for index, event in enumerate(items):
        if not isinstance(event, Mapping):
            raise EconomicAnalysisError(
                f"Invalid event at index {index}."
            )

        event_type = event.get("event_type")

        if (
            not isinstance(event_type, str)
            or event_type not in SUPPORTED_EVENT_TYPES
        ):
            raise EconomicAnalysisError(
                f"Unsupported event type at index {index}."
            )

        title = event.get("title")
        source = event.get("source")

        if not isinstance(title, str) or not title.strip():
            raise EconomicAnalysisError(
                f"Event {index} requires a title."
            )

        if not isinstance(source, str) or not source.strip():
            raise EconomicAnalysisError(
                f"Event {index} requires a source."
            )

        scheduled_at = _parse_timestamp(
            event.get("scheduled_at"),
            "scheduled_at",
        )

        released_at_value = event.get("released_at")

        released_at = None

        if released_at_value is not None:
            released_at = _parse_timestamp(
                released_at_value,
                "released_at",
            )

        validated.append({
            "event_type": event_type,
            "title": title.strip(),
            "source": source.strip(),
            "scheduled_at": scheduled_at,
            "released_at": released_at,
        })

    return validated


def _economic_interpretation(
    indicator: str,
) -> str:
    interpretations = {
        "FED_RATE": (
            "INTEREST_RATE_POLICY_EXPECTATIONS_AFFECT_GOLD"
        ),
        "CPI": (
            "INFLATION_SURPRISE_MAY_CHANGE_RATE_EXPECTATIONS"
        ),
        "CORE_CPI": (
            "CORE_INFLATION_SURPRISE_MAY_CHANGE_RATE_EXPECTATIONS"
        ),
        "PCE": (
            "PCE_INFLATION_REQUIRES_POLICY_CONTEXT"
        ),
        "CORE_PCE": (
            "CORE_PCE_IS_AN_IMPORTANT_FED_INFLATION_MEASURE"
        ),
        "NONFARM_PAYROLLS": (
            "EMPLOYMENT_SURPRISE_MAY_CHANGE_RATE_EXPECTATIONS"
        ),
        "UNEMPLOYMENT_RATE": (
            "UNEMPLOYMENT_CHANGES_REQUIRE_LABOR_MARKET_CONTEXT"
        ),
        "US_10Y_YIELD": (
            "LONG_TERM_YIELDS_CAN_INFLUENCE_GOLD"
        ),
        "US_2Y_YIELD": (
            "SHORT_TERM_YIELDS_CAN_INFLUENCE_RATE_EXPECTATIONS"
        ),
        "DXY": (
            "DOLLAR_STRENGTH_CAN_INFLUENCE_GOLD"
        ),
    }

    return interpretations.get(
        indicator,
        "NO_AUTOMATIC_DIRECTION",
    )


def _calculate_surprise(
    actual: float,
    forecast: float | None,
) -> dict[str, Any]:
    if forecast is None:
        return {
            "available": False,
            "difference": None,
            "percentage_difference": None,
            "classification": "NO_FORECAST",
        }

    difference = actual - forecast

    if difference > 0:
        classification = "ABOVE_FORECAST"
    elif difference < 0:
        classification = "BELOW_FORECAST"
    else:
        classification = "IN_LINE_WITH_FORECAST"

    if forecast != 0:
        percentage_difference = (
            difference / abs(forecast)
        ) * 100.0
    else:
        percentage_difference = None

    return {
        "available": True,
        "actual": actual,
        "forecast": forecast,
        "difference": difference,
        "percentage_difference": percentage_difference,
        "classification": classification,
    }


def _calculate_previous_comparison(
    actual: float,
    previous: float | None,
    revised_previous: float | None,
) -> dict[str, Any]:
    if previous is None:
        return {
            "available": False,
            "previous": None,
            "revised_previous": revised_previous,
            "change_from_previous": None,
            "revision_detected": False,
            "revision_amount": None,
        }

    comparison_value = (
        revised_previous
        if revised_previous is not None
        else previous
    )

    revision_detected = (
        revised_previous is not None
        and revised_previous != previous
    )

    revision_amount = (
        revised_previous - previous
        if revision_detected
        else 0.0
    )

    return {
        "available": True,
        "previous": previous,
        "revised_previous": revised_previous,
        "comparison_baseline": comparison_value,
        "change_from_previous": (
            actual - comparison_value
        ),
        "revision_detected": revision_detected,
        "revision_amount": revision_amount,
    }


def _classify_observation_quality(
    timestamp: datetime,
    verified: bool,
    current_time: datetime,
) -> tuple[str, float]:
    age_hours = (
        current_time - timestamp
    ).total_seconds() / 3600.0

    if age_hours < -(
        MAX_FUTURE_TIMESTAMP_MINUTES / 60.0
    ):
        return "FUTURE_TIMESTAMP", age_hours

    if age_hours > MAX_DATA_AGE_HOURS:
        return "STALE", age_hours

    if not verified:
        return "UNVERIFIED", age_hours

    return "CURRENT", age_hours


def _analyze_observation(
    item: dict[str, Any],
    current_time: datetime,
) -> dict[str, Any]:
    status, age_hours = _classify_observation_quality(
        item["timestamp"],
        item["verified"],
        current_time,
    )

    surprise = _calculate_surprise(
        item["actual"],
        item["forecast"],
    )

    previous_comparison = _calculate_previous_comparison(
        item["actual"],
        item["previous"],
        item["revised_previous"],
    )

    return {
        "indicator": item["indicator"],
        "actual": item["actual"],
        "value": item["actual"],
        "forecast": item["forecast"],
        "previous": item["previous"],
        "revised_previous": item["revised_previous"],
        "unit": item["unit"],
        "source": item["source"],
        "verified": item["verified"],
        "timestamp": item["timestamp"].isoformat(),
        "age_hours": age_hours,
        "status": status,
        "surprise": surprise,
        "previous_comparison": previous_comparison,
        "revision_detected": item["revision_detected"],
        "gold_context": _economic_interpretation(
            item["indicator"]
        ),
    }


def _analyze_event(
    event: dict[str, Any],
    current_time: datetime,
) -> dict[str, Any]:
    scheduled_at = event["scheduled_at"]
    released_at = event["released_at"]

    seconds_until = (
        scheduled_at - current_time
    ).total_seconds()

    if released_at is not None:
        if released_at > current_time:
            status = "INVALID_FUTURE_RELEASE_TIMESTAMP"
        else:
            status = "RELEASED"

    elif seconds_until > 0:
        status = "UPCOMING"

    else:
        status = "AWAITING_RELEASE_CONFIRMATION"

    return {
        "event_type": event["event_type"],
        "title": event["title"],
        "source": event["source"],
        "scheduled_at": scheduled_at.isoformat(),
        "released_at": (
            released_at.isoformat()
            if released_at is not None
            else None
        ),
        "seconds_until": seconds_until,
        "status": status,
    }


def analyze_economics(
    observations: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    if now is None:
        current_time = datetime.now(timezone.utc)

    else:
        if not isinstance(now, datetime):
            raise EconomicAnalysisError(
                "now must be a datetime object."
            )

        if now.tzinfo is None:
            raise EconomicAnalysisError(
                "now must include a timezone."
            )

        current_time = now.astimezone(timezone.utc)

    data = _validate_observations(observations)
    calendar = _validate_events(events)

    analyzed_observations = [
        _analyze_observation(item, current_time)
        for item in data
    ]

    analyzed_events = [
        _analyze_event(event, current_time)
        for event in calendar
    ]

    analyzed_events.sort(
        key=lambda item: item["seconds_until"]
    )

    current_data = [
        item
        for item in analyzed_observations
        if item["status"] == "CURRENT"
    ]

    stale_data = [
        item
        for item in analyzed_observations
        if item["status"] == "STALE"
    ]

    unverified_data = [
        item
        for item in analyzed_observations
        if item["status"] == "UNVERIFIED"
    ]

    future_data = [
        item
        for item in analyzed_observations
        if item["status"] == "FUTURE_TIMESTAMP"
    ]

    upcoming_events = [
        item
        for item in analyzed_events
        if item["status"] == "UPCOMING"
    ]

    released_events = [
        item
        for item in analyzed_events
        if item["status"] == "RELEASED"
    ]

    pending_events = [
        item
        for item in analyzed_events
        if item["status"] == "AWAITING_RELEASE_CONFIRMATION"
    ]

    surprise_count = sum(
        1
        for item in current_data
        if item["surprise"]["available"]
    )

    revision_count = sum(
        1
        for item in analyzed_observations
        if item["revision_detected"]
    )

    return {
        "module": "economic_analysis",
        "data_available": bool(
            analyzed_observations or analyzed_events
        ),
        "observation_count": len(
            analyzed_observations
        ),
        "verified_current_observation_count": len(
            current_data
        ),
        "observations": analyzed_observations,
        "events": analyzed_events,
        "upcoming_events": upcoming_events,
        "released_events": released_events,
        "pending_events": pending_events,
        "data_quality": {
            "has_verified_current_data": bool(
                current_data
            ),
            "has_unverified_data": bool(
                unverified_data
            ),
            "has_stale_data": bool(stale_data),
            "has_future_timestamp_data": bool(
                future_data
            ),
            "current_observation_count": len(
                current_data
            ),
            "stale_observation_count": len(
                stale_data
            ),
            "unverified_observation_count": len(
                unverified_data
            ),
            "future_timestamp_observation_count": len(
                future_data
            ),
            "observations_with_forecast": surprise_count,
            "previous_value_revision_count": revision_count,
            "max_data_age_hours": MAX_DATA_AGE_HOURS,
        },
        "parameters": {
            "max_data_age_hours": MAX_DATA_AGE_HOURS,
            "max_future_timestamp_minutes": (
                MAX_FUTURE_TIMESTAMP_MINUTES
            ),
        },
        "limitations": [
            "This module does not fetch live economic data.",
            "A source label does not independently prove authenticity.",
            "Forecast quality depends on the provider and collection time.",
            "Economic surprises require expectations and market positioning context.",
            "A positive surprise is not automatically bullish or bearish for gold.",
            "Previous value revisions can materially change interpretation.",
            "Event scheduling does not prove that an event was actually released.",
            "Release confirmation requires a trusted provider timestamp.",
            "Percentage surprise is unavailable when forecast equals zero.",
            "This module does not generate or execute trading orders.",
        ],
    }
