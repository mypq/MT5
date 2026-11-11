"""
GOLD BOT - Economic Analyzer

Analyzes externally supplied, timestamped economic observations.

Supported categories:

- Federal Reserve interest rate decisions
- CPI and PCE inflation
- Nonfarm payrolls and unemployment
- US Treasury yields
- US Dollar Index
- Federal Reserve communications
- Economic calendar events

Important:
This module does not fetch economic data.
A trusted, timestamped data provider must supply observations.

Missing, stale, or unverified information must never be
treated as a confirmed economic fact.

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

class EconomicAnalysisError(ValueError):
"""Raised when economic observations are invalid."""

def _parse_timestamp(value: Any, field: str) -> datetime:
if not isinstance(value, str):
raise EconomicAnalysisError(
f"{field} must be an ISO-8601 string."
)

try:
    timestamp = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
except ValueError:
    raise EconomicAnalysisError(
        f"Invalid timestamp: {field}."
    )

if timestamp.tzinfo is None:
    raise EconomicAnalysisError(
        f"{field} must include a timezone."
    )

return timestamp.astimezone(timezone.utc)

def _read_number(value: Any, field: str) -> float:
if isinstance(value, bool):
raise EconomicAnalysisError(
f"{field} cannot be boolean."
)

try:
    result = float(value)
except (TypeError, ValueError):
    raise EconomicAnalysisError(
        f"Invalid numeric value: {field}."
    )

if not isfinite(result):
    raise EconomicAnalysisError(
        f"{field} must be finite."
    )

return result

def _validate_observations(
observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:

if isinstance(observations, (str, bytes)):
    raise EconomicAnalysisError(
        "Observations must be a sequence."
    )

validated = []

for index, item in enumerate(observations):
    if not isinstance(item, Mapping):
        raise EconomicAnalysisError(
            f"Invalid observation at index {index}."
        )

    indicator = item.get("indicator")

    if indicator not in SUPPORTED_INDICATORS:
        raise EconomicAnalysisError(
            f"Unsupported indicator at index {index}."
        )

    source = item.get("source")
    if not isinstance(source, str) or not source.strip():
        raise EconomicAnalysisError(
            "Every observation requires a source."
        )

    value = _read_number(
        item.get("value"),
        "value",
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

    validated.append({
        "indicator": indicator,
        "value": value,
        "timestamp": timestamp,
        "source": source.strip(),
        "verified": verified,
        "unit": item.get("unit"),
    })

return validated

def _validate_events(
events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:

if isinstance(events, (str, bytes)):
    raise EconomicAnalysisError(
        "Events must be a sequence."
    )

validated = []

for index, event in enumerate(events):
    if not isinstance(event, Mapping):
        raise EconomicAnalysisError(
            f"Invalid event at index {index}."
        )

    event_type = event.get("event_type")

    if event_type not in SUPPORTED_EVENT_TYPES:
        raise EconomicAnalysisError(
            f"Unsupported event type at index {index}."
        )

    title = event.get("title")
    source = event.get("source")

    if not isinstance(title, str) or not title.strip():
        raise EconomicAnalysisError(
            "Every event requires a title."
        )

    if not isinstance(source, str) or not source.strip():
        raise EconomicAnalysisError(
            "Every event requires a source."
        )

    scheduled_at = _parse_timestamp(
        event.get("scheduled_at"),
        "scheduled_at",
    )

    validated.append({
        "event_type": event_type,
        "title": title.strip(),
        "source": source.strip(),
        "scheduled_at": scheduled_at,
    })

return validated

def _economic_interpretation(
indicator: str,
value: float,
) -> str:

# These are broad directional tendencies, not deterministic rules.
if indicator in {
    "FED_RATE",
    "US_10Y_YIELD",
    "US_2Y_YIELD",
}:
    return "HIGHER_RATES_OR_YIELDS_CAN_PRESSURE_GOLD"

if indicator in {
    "CPI",
    "CORE_CPI",
    "PCE",
    "CORE_PCE",
}:
    return "INFLATION_REQUIRES_EXPECTATIONS_AND_POLICY_CONTEXT"

if indicator == "NONFARM_PAYROLLS":
    return "LABOR_DATA_REQUIRES_EXPECTATIONS_AND_REVISION_CONTEXT"

if indicator == "UNEMPLOYMENT_RATE":
    return "LABOR_WEAKNESS_MAY_CHANGE_RATE_EXPECTATIONS"

if indicator == "DXY":
    return "STRONGER_DOLLAR_CAN_PRESSURE_GOLD"

return "NO_AUTOMATIC_DIRECTION"

def analyze_economics(
observations: Sequence[Mapping[str, Any]],
events: Sequence[Mapping[str, Any]],
now: datetime | None = None,
) -> dict[str, Any]:

if now is None:
    current_time = datetime.now(timezone.utc)
else:
    if now.tzinfo is None:
        raise EconomicAnalysisError(
            "now must include a timezone."
        )

    current_time = now.astimezone(timezone.utc)

data = _validate_observations(observations)
calendar = _validate_events(events)

analyzed_observations = []

for item in data:
    age_hours = (
        current_time - item["timestamp"]
    ).total_seconds() / 3600.0

    if age_hours < 0:
        status = "FUTURE_TIMESTAMP"
    elif age_hours > MAX_DATA_AGE_HOURS:
        status = "STALE"
    elif not item["verified"]:
        status = "UNVERIFIED"
    else:
        status = "CURRENT"

    analyzed_observations.append({
        "indicator": item["indicator"],
        "value": item["value"],
        "unit": item["unit"],
        "source": item["source"],
        "timestamp": item["timestamp"].isoformat(),
        "age_hours": age_hours,
        "status": status,
        "gold_context": _economic_interpretation(
            item["indicator"],
            item["value"],
        ),
    })

analyzed_events = []

for event in calendar:
    seconds_until = (
        event["scheduled_at"] - current_time
    ).total_seconds()

    if seconds_until > 0:
        status = "UPCOMING"
    elif seconds_until >= -3600:
        status = "RECENT_OR_IN_PROGRESS"
    else:
        status = "PAST"

    analyzed_events.append({
        "event_type": event["event_type"],
        "title": event["title"],
        "source": event["source"],
        "scheduled_at": event["scheduled_at"].isoformat(),
        "seconds_until": seconds_until,
        "status": status,
    })

analyzed_events.sort(
    key=lambda item: item["seconds_until"]
)

current_data = [
    item for item in analyzed_observations
    if item["status"] == "CURRENT"
]

upcoming_events = [
    item for item in analyzed_events
    if item["status"] == "UPCOMING"
]

return {
    "module": "economic_analysis",
    "data_available": bool(
        analyzed_observations or analyzed_events
    ),
    "observation_count": len(analyzed_observations),
    "verified_current_observation_count": len(current_data),
    "observations": analyzed_observations,
    "events": analyzed_events,
    "upcoming_events": upcoming_events,
    "data_quality": {
        "has_verified_current_data": bool(current_data),
        "has_unverified_data": any(
            item["status"] == "UNVERIFIED"
            for item in analyzed_observations
        ),
        "has_stale_data": any(
            item["status"] == "STALE"
            for item in analyzed_observations
        ),
        "max_data_age_hours": MAX_DATA_AGE_HOURS,
    },
    "limitations": [
        "This module does not fetch live economic data.",
        "A source label alone does not prove data authenticity.",
        "Economic effects depend on market expectations and prior positioning.",
        "Values must be normalized by the future data adapter.",
        "Economic observations do not independently authorize trades.",
    ],
}
