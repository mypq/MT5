"""
GOLD BOT - Macro & Event Intelligence

Economic event assessment for XAUUSD.

Capabilities:

- Economic calendar event validation.
- Event impact classification.
- Forecast versus actual analysis.
- Surprise magnitude calculation.
- USD and gold sensitivity assessment.
- Upcoming event risk windows.
- Stale and incomplete event detection.

IMPORTANT:

- This module does not fetch economic calendar data.
- A trusted calendar adapter must provide verified observations.
- Economic relationships are contextual, not guaranteed.
- Event direction is not a standalone trading signal.
  """

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite
from typing import Any

class MacroEventError(ValueError):
"""Raised when economic event input is invalid."""

SUPPORTED_IMPACTS = {
"LOW",
"MEDIUM",
"HIGH",
"CRITICAL",
}

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

DEFAULT_WINDOWS = {
"before_minutes": 30,
"after_minutes": 30,
}

MAXIMUM_EVENTS = 1000

def _number(
value: Any,
field: str,
) -> float:

if isinstance(value, bool):
    raise MacroEventError(
        f"{field} must be numeric."
    )

try:
    result = float(value)
except (TypeError, ValueError):
    raise MacroEventError(
        f"{field} must be numeric."
    )

if not isfinite(result):
    raise MacroEventError(
        f"{field} must be finite."
    )

return result

def _timestamp(
value: Any,
field: str,
) -> datetime:

if not isinstance(value, str):
    raise MacroEventError(
        f"{field} must be ISO-8601."
    )

try:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
except ValueError:
    raise MacroEventError(
        f"{field} is invalid."
    )

if parsed.tzinfo is None:
    raise MacroEventError(
        f"{field} must include a timezone."
    )

return parsed.astimezone(
    timezone.utc
)

def _validate_event(
event: Mapping[str, Any],
index: int,
reference: datetime,
) -> dict[str, Any]:

if not isinstance(event, Mapping):
    raise MacroEventError(
        f"Event {index} must be a mapping."
    )

name = event.get("name")

if not isinstance(name, str) or not name.strip():
    raise MacroEventError(
        f"Event {index} has an invalid name."
    )

currency = event.get(
    "currency",
    "",
)

if not isinstance(currency, str):
    raise MacroEventError(
        f"Event {index} currency is invalid."
    )

currency = currency.strip().upper()

impact = str(
    event.get("impact", "")
).strip().upper()

if impact not in SUPPORTED_IMPACTS:
    raise MacroEventError(
        f"Event {index} has an unsupported impact."
    )

category = str(
    event.get("category", "OTHER")
).strip().upper()

if category not in EVENT_CATEGORIES:
    raise MacroEventError(
        f"Event {index} has an unsupported category."
    )

event_time = _timestamp(
    event.get("event_time"),
    f"events[{index}].event_time",
)

source_timestamp = _timestamp(
    event.get("source_timestamp"),
    f"events[{index}].source_timestamp",
)

actual = event.get("actual")
forecast = event.get("forecast")
previous = event.get("previous")

if actual is not None:
    actual = _number(
        actual,
        f"events[{index}].actual",
    )

if forecast is not None:
    forecast = _number(
        forecast,
        f"events[{index}].forecast",
    )

if previous is not None:
    previous = _number(
        previous,
        f"events[{index}].previous",
    )

verified = (
    event.get("source_verified") is True
)

published = (
    event.get("published") is True
)

revision = event.get(
    "revision",
    False,
)

if not isinstance(revision, bool):
    raise MacroEventError(
        f"Event {index} revision must be boolean."
    )

source_age = (
    reference - source_timestamp
).total_seconds()

if source_age < 0:
    raise MacroEventError(
        f"Event {index} source timestamp is in the future."
    )

maximum_source_age = _number(
    event.get("max_source_age_seconds", 86400),
    f"events[{index}].max_source_age_seconds",
)

if maximum_source_age <= 0:
    raise MacroEventError(
        "max_source_age_seconds must be positive."
    )

source_fresh = (
    source_age <= maximum_source_age
)

if published and actual is None:
    raise MacroEventError(
        f"Published event {index} is missing actual value."
    )

surprise = None

if actual is not None and forecast is not None:

    surprise = actual - forecast

return {
    "name": name.strip(),
    "currency": currency,
    "impact": impact,
    "category": category,
    "event_time": event_time,
    "source_timestamp": source_timestamp,
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

def _impact_score(
impact: str,
) -> int:

return {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}[impact]

def _event_interpretation(
event: Mapping[str, Any],
) -> str:

if not event["eligible"]:
    return "UNVERIFIED_OR_STALE_EVENT"

if not event["published"]:
    return "UPCOMING_EVENT"

if event["surprise"] is None:
    return "ACTUAL_FORECAST_COMPARISON_UNAVAILABLE"

surprise = event["surprise"]

if surprise > 0:
    return "ACTUAL_ABOVE_FORECAST"

if surprise < 0:
    return "ACTUAL_BELOW_FORECAST"

return "ACTUAL_MATCHED_FORECAST"

def _gold_context(
event: Mapping[str, Any],
) -> str:

category = event["category"]

currency = event["currency"]

surprise = event["surprise"]

if category == "INTEREST_RATE":

    return (
        "HIGHER_RATE_EXPECTATIONS_CAN_PRESSURE_GOLD; "
        "CONTEXT_DEPENDS_ON_REAL_YIELDS_AND_FED_GUIDANCE"
        if surprise is not None and surprise > 0
        else "LOWER_RATE_EXPECTATIONS_CAN_SUPPORT_GOLD; "
        "VERIFY_BOND_YIELDS_AND_POLICY_CONTEXT"
    )

if category == "INFLATION":

    return (
        "INFLATION_SURPRISE_REQUIRES_REAL_YIELD_AND_FED_CONTEXT"
    )

if category == "EMPLOYMENT":

    return (
        "EMPLOYMENT_SURPRISE_REQUIRES_WAGE_AND_UNEMPLOYMENT_CONTEXT"
    )

if category in {
    "CENTRAL_BANK",
    "MONETARY_POLICY",
}:

    return (
        "CENTRAL_BANK_COMMUNICATION_REQUIRES_POLICY_CONTEXT"
    )

if category == "GDP":

    return (
        "GROWTH_SURPRISE_REQUIRES_REAL_YIELD_AND_USD_CONTEXT"
    )

if category == "BOND_YIELDS":

    return (
        "YIELD_CHANGES_ARE_DIRECTLY_RELEVANT_TO_GOLD"
    )

return (
    f"MACRO_EVENT_REQUIRES_CONTEXTUAL_ANALYSIS_FOR_{currency or 'UNKNOWN_CURRENCY'}"
)

def analyze_macro_events(
events: Sequence[Mapping[str, Any]],
reference_time: str,
before_minutes: int = 30,
after_minutes: int = 30,
require_usd: bool = True,
) -> dict[str, Any]:

if (
    isinstance(events, (str, bytes))
    or not isinstance(events, Sequence)
):
    raise MacroEventError(
        "events must be a sequence."
    )

if len(events) > MAXIMUM_EVENTS:
    raise MacroEventError(
        "Too many economic events."
    )

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
    raise MacroEventError(
        "require_usd must be boolean."
    )

reference = _timestamp(
    reference_time,
    "reference_time",
)

validated = [
    _validate_event(
        event,
        index,
        reference,
    )
    for index, event in enumerate(events)
]

validated.sort(
    key=lambda item: item["event_time"]
)

relevant = [
    item for item in validated
    if (
        not require_usd
        or item["currency"] == "USD"
    )
]

active_events = []
upcoming_events = []
past_events = []
warnings = []

for event in relevant:

    minutes = event["minutes_until_event"]

    if (
        -after_minutes
        <= minutes
        <= before_minutes
    ):

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

highest_impact = max(
    (
        _impact_score(event["impact"])
        for event in active_events
        if event["eligible"]
    ),
    default=0,
)

if highest_impact >= 4:
    event_risk = "CRITICAL"

elif highest_impact == 3:
    event_risk = "HIGH"

elif highest_impact == 2:
    event_risk = "MEDIUM"

elif highest_impact == 1:
    event_risk = "LOW"

else:
    event_risk = "NONE"

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

return {
    "module": "macro_event_intelligence",
    "reference_time": reference.isoformat(),
    "symbol_context": "XAUUSD",
    "events_analyzed": len(relevant),
    "active_event_risk": event_risk,
    "active_events": [
        {
            **event,
            "interpretation": _event_interpretation(event),
            "gold_context": _gold_context(event),
        }
        for event in active_events
    ],
    "upcoming_events": [
        {
            **event,
            "interpretation": _event_interpretation(event),
            "gold_context": _gold_context(event),
        }
        for event in upcoming_events
    ],
    "past_published_surprises": [
        {
            **event,
            "interpretation": _event_interpretation(event),
            "gold_context": _gold_context(event),
        }
        for event in published_surprises
    ],
    "next_verified_event": (
        {
            "name": next_verified_event["name"],
            "event_time": next_verified_event["event_time"].isoformat(),
            "impact": next_verified_event["impact"],
            "minutes_until_event": (
                next_verified_event["minutes_until_event"]
            ),
        }
        if next_verified_event
        else None
    ),
    "warnings": warnings,
    "trade_context": (
        "HIGH_IMPACT_EVENT_WINDOW_ACTIVE"
        if event_risk in {"HIGH", "CRITICAL"}
        else "NO_VERIFIED_HIGH_IMPACT_EVENT_IN_ACTIVE_WINDOW"
    ),
    "execution_authorized": False,
    "limitations": [
        "No live economic calendar connection is implemented here.",
        "Source verification must be performed by a trusted external adapter.",
        "A verified source does not guarantee that the observation is correct.",
        "Economic surprise direction is not universally bullish or bearish for gold.",
        "Inflation, employment and central bank events require contextual interpretation.",
        "Event risk windows are configurable approximations.",
        "Event revisions must be preserved by the upstream data provider.",
        "This module does not fetch news or place orders.",
    ],
}
