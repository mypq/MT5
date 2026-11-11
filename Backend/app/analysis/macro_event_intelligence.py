"""
GOLD BOT - Macro & Event Intelligence

Validated economic calendar analysis for XAUUSD.

This module does not fetch calendar data and never executes trades.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite
from typing import Any


class MacroEventError(ValueError):
    """Raised when economic event input is invalid."""


SUPPORTED_IMPACTS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

EVENT_CATEGORIES = {
    "INTEREST_RATE",
    "INFLATION",
    "EMPLOYMENT",
    "GDP",
    "CENTRAL_BANK",
    "MONETARY_POLICY",
    "BOND_YIELDS",
    "OTHER",
}

MAXIMUM_EVENTS = 1000
MAXIMUM_REFERENCE_FUTURE_SECONDS = 60
DEFAULT_MAXIMUM_SOURCE_AGE_SECONDS = 86400


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise MacroEventError(f"{field} must be numeric.")

    try:
        result = float(value)
    except (TypeError, ValueError):
        raise MacroEventError(f"{field} must be numeric.") from None

    if not isfinite(result):
        raise MacroEventError(f"{field} must be finite.")

    return result


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise MacroEventError(f"{field} must be ISO-8601.")

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise MacroEventError(f"{field} is invalid.") from None

    if parsed.tzinfo is None:
        raise MacroEventError(f"{field} must include a timezone.")

    return parsed.astimezone(timezone.utc)


def _validate_event(
    event: Mapping[str, Any],
    index: int,
    reference: datetime,
) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise MacroEventError(f"Event {index} must be a mapping.")

    name = event.get("name")

    if not isinstance(name, str) or not name.strip():
        raise MacroEventError(f"Event {index} has an invalid name.")

    currency = event.get("currency")

    if not isinstance(currency, str) or not currency.strip():
        raise MacroEventError(f"Event {index} currency is required.")

    currency = currency.strip().upper()

    impact = event.get("impact")

    if not isinstance(impact, str):
        raise MacroEventError(f"Event {index} impact is invalid.")

    impact = impact.strip().upper()

    if impact not in SUPPORTED_IMPACTS:
        raise MacroEventError(f"Event {index} has unsupported impact.")

    category = event.get("category", "OTHER")

    if not isinstance(category, str):
        raise MacroEventError(f"Event {index} category is invalid.")

    category = category.strip().upper()

    if category not in EVENT_CATEGORIES:
        raise MacroEventError(f"Event {index} has unsupported category.")

    event_time = _timestamp(
        event.get("event_time"),
        f"events[{index}].event_time",
    )

    source_timestamp = _timestamp(
        event.get("source_timestamp"),
        f"events[{index}].source_timestamp",
    )

    now = datetime.now(timezone.utc)

    if source_timestamp > now:
        raise MacroEventError(
            f"Event {index} source timestamp is in the future."
        )

    source_age = (reference - source_timestamp).total_seconds()

    if source_age < 0:
        raise MacroEventError(
            f"Event {index} source timestamp is later than reference_time."
        )

    maximum_source_age = _number(
        event.get(
            "max_source_age_seconds",
            DEFAULT_MAXIMUM_SOURCE_AGE_SECONDS,
        ),
        f"events[{index}].max_source_age_seconds",
    )

    if maximum_source_age <= 0:
        raise MacroEventError(
            "max_source_age_seconds must be positive."
        )

    source_fresh = source_age <= maximum_source_age

    verified = event.get("source_verified") is True
    published = event.get("published") is True

    revision = event.get("revision", False)

    if not isinstance(revision, bool):
        raise MacroEventError(
            f"Event {index} revision must be boolean."
        )

    actual = event.get("actual")
    forecast = event.get("forecast")
    previous = event.get("previous")

    if actual is not None:
        actual = _number(actual, f"events[{index}].actual")

    if forecast is not None:
        forecast = _number(forecast, f"events[{index}].forecast")

    if previous is not None:
        previous = _number(previous, f"events[{index}].previous")

    if published and actual is None:
        raise MacroEventError(
            f"Published event {index} is missing actual value."
        )

    if not published and actual is not None:
        raise MacroEventError(
            f"Unpublished event {index} cannot contain an actual value."
        )

    surprise = (
        actual - forecast
        if actual is not None and forecast is not None
        else None
    )

    return {
        "name": name.strip(),
        "currency": currency,
        "impact": impact,
        "category": category,
        "event_time": event_time,
        "source_timestamp": source_timestamp,
        "source_age_seconds": round(source_age, 2),
        "actual": actual,
        "forecast": forecast,
        "previous": previous,
        "surprise": surprise,
        "published": published,
        "revision": revision,
        "source_verified": verified,
        "source_fresh": source_fresh,
        "eligible": verified and source_fresh,
        "minutes_until_event": (
            event_time - reference
        ).total_seconds() / 60.0,
    }


def _impact_score(impact: str) -> int:
    return {
        "LOW": 1,
        "MEDIUM": 2,
        "HIGH": 3,
        "CRITICAL": 4,
    }[impact]


def _event_interpretation(event: Mapping[str, Any]) -> str:
    if not event["eligible"]:
        return "UNVERIFIED_OR_STALE_EVENT"

    if not event["published"]:
        return "UPCOMING_EVENT"

    if event["surprise"] is None:
        return "ACTUAL_FORECAST_COMPARISON_UNAVAILABLE"

    if event["surprise"] > 0:
        return "ACTUAL_ABOVE_FORECAST"

    if event["surprise"] < 0:
        return "ACTUAL_BELOW_FORECAST"

    return "ACTUAL_MATCHED_FORECAST"


def _gold_context(event: Mapping[str, Any]) -> str:
    category = event["category"]

    if category == "INTEREST_RATE":
        return (
            "RATE_SURPRISE_REQUIRES_REAL_YIELD_AND_CENTRAL_BANK_CONTEXT"
        )

    if category == "INFLATION":
        return (
            "INFLATION_SURPRISE_REQUIRES_REAL_YIELD_AND_FED_CONTEXT"
        )

    if category == "EMPLOYMENT":
        return (
            "EMPLOYMENT_SURPRISE_REQUIRES_WAGE_AND_UNEMPLOYMENT_CONTEXT"
        )

    if category in {"CENTRAL_BANK", "MONETARY_POLICY"}:
        return (
            "CENTRAL_BANK_COMMUNICATION_REQUIRES_POLICY_CONTEXT"
        )

    if category == "GDP":
        return (
            "GROWTH_SURPRISE_REQUIRES_REAL_YIELD_AND_USD_CONTEXT"
        )

    if category == "BOND_YIELDS":
        return (
            "YIELD_CHANGES_ARE_RELEVANT_TO_GOLD_BUT_REQUIRE_CONTEXT"
        )

    return "MACRO_EVENT_REQUIRES_CONTEXTUAL_ANALYSIS"


def analyze_macro_events(
    events: Sequence[Mapping[str, Any]],
    reference_time: str,
    before_minutes: int = 30,
    after_minutes: int = 30,
    require_usd: bool = True,
) -> dict[str, Any]:
    """
    Analyze economic events relevant to XAUUSD.

    Each event requires:
    name, currency, impact, category, event_time,
    source_timestamp, source_verified, published.

    Published events require actual.
    All timestamps must be timezone-aware ISO-8601.
    """

    if (
        isinstance(events, (str, bytes))
        or not isinstance(events, Sequence)
    ):
        raise MacroEventError("events must be a sequence.")

    if len(events) > MAXIMUM_EVENTS:
        raise MacroEventError("Too many economic events.")

    if (
        isinstance(before_minutes, bool)
        or not isinstance(before_minutes, int)
        or before_minutes < 0
    ):
        raise MacroEventError(
            "before_minutes must be a non-negative integer."
        )

    if (
        isinstance(after_minutes, bool)
        or not isinstance(after_minutes, int)
        or after_minutes < 0
    ):
        raise MacroEventError(
            "after_minutes must be a non-negative integer."
        )

    if not isinstance(require_usd, bool):
        raise MacroEventError("require_usd must be boolean.")

    reference = _timestamp(reference_time, "reference_time")
    now = datetime.now(timezone.utc)

    if (
        reference - now
    ).total_seconds() > MAXIMUM_REFERENCE_FUTURE_SECONDS:
        raise MacroEventError(
            "reference_time is too far in the future."
        )

    validated = [
        _validate_event(event, index, reference)
        for index, event in enumerate(events)
    ]

    validated.sort(key=lambda item: item["event_time"])

    relevant = [
        event for event in validated
        if not require_usd or event["currency"] == "USD"
    ]

    active_events = []
    upcoming_events = []
    past_events = []
    warnings = []

    for event in relevant:
        minutes = event["minutes_until_event"]

        if -after_minutes <= minutes <= before_minutes:
            active_events.append(event)
        elif minutes > before_minutes:
            upcoming_events.append(event)
        else:
            past_events.append(event)

        if not event["eligible"]:
            warnings.append({
                "event": event["name"],
                "warning": "UNVERIFIED_OR_STALE_SOURCE",
            })

    eligible_active = [
        event for event in active_events
        if event["eligible"]
    ]

    highest_impact = max(
        (
            _impact_score(event["impact"])
            for event in eligible_active
        ),
        default=0,
    )

    impact_names = {
        0: "NONE",
        1: "LOW",
        2: "MEDIUM",
        3: "HIGH",
        4: "CRITICAL",
    }

    event_risk = impact_names[highest_impact]

    next_verified_event = next(
        (
            event for event in upcoming_events
            if event["eligible"]
        ),
        None,
    )

    published_surprises = [
        event for event in past_events
        if (
            event["eligible"]
            and event["published"]
            and event["surprise"] is not None
        )
    ]

    def present_event(event: Mapping[str, Any]) -> dict[str, Any]:
        return {
            **event,
            "event_time": event["event_time"].isoformat(),
            "source_timestamp": event["source_timestamp"].isoformat(),
            "interpretation": _event_interpretation(event),
            "gold_context": _gold_context(event),
        }

    if event_risk in {"HIGH", "CRITICAL"}:
        trade_context = "HIGH_IMPACT_EVENT_WINDOW_ACTIVE"
    elif event_risk == "MEDIUM":
        trade_context = "MEDIUM_IMPACT_EVENT_WINDOW_ACTIVE"
    elif event_risk == "LOW":
        trade_context = "LOW_IMPACT_EVENT_WINDOW_ACTIVE"
    else:
        trade_context = "NO_VERIFIED_EVENT_IN_ACTIVE_WINDOW"

    return {
        "module": "macro_event_intelligence",
        "symbol_context": "XAUUSD",
        "reference_time": reference.isoformat(),
        "events_received": len(events),
        "events_analyzed": len(relevant),
        "active_event_risk": event_risk,
        "active_events": [
            present_event(event)
            for event in active_events
        ],
        "upcoming_events": [
            present_event(event)
            for event in upcoming_events
        ],
        "past_published_surprises": [
            present_event(event)
            for event in published_surprises
        ],
        "next_verified_event": (
            {
                "name": next_verified_event["name"],
                "event_time": next_verified_event["event_time"].isoformat(),
                "impact": next_verified_event["impact"],
                "minutes_until_event": round(
                    next_verified_event["minutes_until_event"],
                    2,
                ),
            }
            if next_verified_event
            else None
        ),
        "warnings": warnings,
        "trade_context": trade_context,
        "execution_authorized": False,
        "trading_signal_allowed": False,
        "limitations": [
            "This module does not fetch live economic calendar data.",
            "Source verification must come from a trusted calendar adapter.",
            "Source verification does not guarantee the underlying event data is correct.",
            "Economic surprise direction is not universally bullish or bearish for gold.",
            "Inflation, employment, GDP and central bank events require contextual analysis.",
            "Surprise values are raw differences and may not be comparable across different units.",
            "Event windows are configurable approximations.",
            "Revisions must be preserved and identified by the upstream provider.",
            "This module does not fetch news or place orders.",
        ],
    }
