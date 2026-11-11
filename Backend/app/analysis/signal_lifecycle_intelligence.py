"""
GOLD BOT - Signal Lifecycle Intelligence

Tracks analytical signals from creation to termination.

Lifecycle:
NEW
CONFIRMED
ACTIVE
TARGET_HIT
STOP_HIT
EXPIRED
CANCELLED
INVALIDATED

This module tracks analytical signals only.
It never places, modifies, or closes broker orders.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite
from typing import Any
from uuid import uuid4


class SignalLifecycleError(ValueError):
    """Raised when signal lifecycle input is invalid."""


VALID_DIRECTIONS = {"BUY", "SELL"}

VALID_STATUSES = {
    "NEW",
    "CONFIRMED",
    "ACTIVE",
    "TARGET_HIT",
    "STOP_HIT",
    "EXPIRED",
    "CANCELLED",
    "INVALIDATED",
}

TERMINAL_STATUSES = {
    "TARGET_HIT",
    "STOP_HIT",
    "EXPIRED",
    "CANCELLED",
    "INVALIDATED",
}

ALLOWED_TRANSITIONS = {
    "NEW": {
        "CONFIRMED",
        "CANCELLED",
        "EXPIRED",
        "INVALIDATED",
    },
    "CONFIRMED": {
        "ACTIVE",
        "CANCELLED",
        "EXPIRED",
        "INVALIDATED",
    },
    "ACTIVE": {
        "TARGET_HIT",
        "STOP_HIT",
        "EXPIRED",
        "CANCELLED",
        "INVALIDATED",
    },
}

MAXIMUM_REFERENCE_FUTURE_SECONDS = 60
MAXIMUM_HISTORY_ENTRIES = 10000


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise SignalLifecycleError(
            f"{field} must be numeric."
        )

    try:
        result = float(value)
    except (TypeError, ValueError):
        raise SignalLifecycleError(
            f"{field} must be numeric."
        ) from None

    if not isfinite(result):
        raise SignalLifecycleError(
            f"{field} must be finite."
        )

    return result


def _positive(value: Any, field: str) -> float:
    result = _number(value, field)

    if result <= 0:
        raise SignalLifecycleError(
            f"{field} must be positive."
        )

    return result


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise SignalLifecycleError(
            f"{field} must be ISO-8601."
        )

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        raise SignalLifecycleError(
            f"{field} is invalid."
        ) from None

    if parsed.tzinfo is None:
        raise SignalLifecycleError(
            f"{field} must include a timezone."
        )

    return parsed.astimezone(timezone.utc)


def _validate_event_time(
    event_time: datetime,
    field: str,
) -> None:
    now = datetime.now(timezone.utc)

    if (
        event_time - now
    ).total_seconds() > MAXIMUM_REFERENCE_FUTURE_SECONDS:
        raise SignalLifecycleError(
            f"{field} is too far in the future."
        )


def _validate_signal_identity(
    signal: Mapping[str, Any],
) -> None:
    signal_id = signal.get("signal_id")

    if not isinstance(signal_id, str) or not signal_id.strip():
        raise SignalLifecycleError(
            "Signal ID is missing or invalid."
        )

    symbol = signal.get("symbol")

    if not isinstance(symbol, str) or not symbol.strip():
        raise SignalLifecycleError(
            "Signal symbol is missing or invalid."
        )

    direction = signal.get("direction")

    if direction not in VALID_DIRECTIONS:
        raise SignalLifecycleError(
            "Signal direction is invalid."
        )


def _validate_history(
    history: Any,
) -> list[dict[str, Any]]:
    if not isinstance(history, list):
        raise SignalLifecycleError(
            "Signal history must be a list."
        )

    if not 1 <= len(history) <= MAXIMUM_HISTORY_ENTRIES:
        raise SignalLifecycleError(
            "Invalid signal history length."
        )

    validated = []
    previous_time = None

    for index, entry in enumerate(history):
        if not isinstance(entry, Mapping):
            raise SignalLifecycleError(
                f"History entry {index} is invalid."
            )

        status = entry.get("status")

        if status not in VALID_STATUSES:
            raise SignalLifecycleError(
                f"History entry {index} has invalid status."
            )

        timestamp = _timestamp(
            entry.get("timestamp"),
            f"history[{index}].timestamp",
        )

        if (
            previous_time is not None
            and timestamp < previous_time
        ):
            raise SignalLifecycleError(
                "Signal history timestamps cannot move backwards."
            )

        reason = entry.get("reason")

        if not isinstance(reason, str) or not reason.strip():
            raise SignalLifecycleError(
                f"History entry {index} has no reason."
            )

        previous_time = timestamp
        validated.append(dict(entry))

    return validated


def _validate_signal_state(
    signal: Mapping[str, Any],
) -> tuple[datetime, datetime, list[dict[str, Any]]]:
    _validate_signal_identity(signal)

    status = signal.get("status")

    if status not in VALID_STATUSES:
        raise SignalLifecycleError(
            "Signal has an invalid status."
        )

    created = _timestamp(
        signal.get("created_at"),
        "created_at",
    )

    expires = _timestamp(
        signal.get("expires_at"),
        "expires_at",
    )

    last_updated = _timestamp(
        signal.get("last_updated_at"),
        "last_updated_at",
    )

    if expires <= created:
        raise SignalLifecycleError(
            "expires_at must be later than created_at."
        )

    if last_updated < created:
        raise SignalLifecycleError(
            "last_updated_at cannot precede created_at."
        )

    if last_updated > expires and status != "EXPIRED":
        raise SignalLifecycleError(
            "A signal updated after expiry must be EXPIRED."
        )

    history = _validate_history(
        signal.get("history")
    )

    if history[-1]["status"] != status:
        raise SignalLifecycleError(
            "Latest history status does not match signal status."
        )

    history_time = _timestamp(
        history[-1]["timestamp"],
        "history[-1].timestamp",
    )

    if history_time != last_updated:
        raise SignalLifecycleError(
            "Latest history timestamp does not match last_updated_at."
        )

    return created, expires, history


def create_signal(
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    created_at: str,
    expires_at: str,
    confidence: float,
    source: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(symbol, str) or not symbol.strip():
        raise SignalLifecycleError(
            "symbol cannot be empty."
        )

    if not isinstance(direction, str):
        raise SignalLifecycleError(
            "direction must be a string."
        )

    direction = direction.strip().upper()

    if direction not in VALID_DIRECTIONS:
        raise SignalLifecycleError(
            "direction must be BUY or SELL."
        )

    entry = _positive(entry_price, "entry_price")
    stop = _positive(stop_loss, "stop_loss")
    target = _positive(take_profit, "take_profit")

    if direction == "BUY":
        if not stop < entry < target:
            raise SignalLifecycleError(
                "BUY requires stop < entry < target."
            )
    else:
        if not target < entry < stop:
            raise SignalLifecycleError(
                "SELL requires target < entry < stop."
            )

    confidence = _number(
        confidence,
        "confidence",
    )

    if not 0 <= confidence <= 100:
        raise SignalLifecycleError(
            "confidence must be between 0 and 100."
        )

    if not isinstance(source, str) or not source.strip():
        raise SignalLifecycleError(
            "source cannot be empty."
        )

    created = _timestamp(created_at, "created_at")
    expires = _timestamp(expires_at, "expires_at")

    _validate_event_time(created, "created_at")

    if expires <= created:
        raise SignalLifecycleError(
            "expires_at must be later than created_at."
        )

    if expires <= datetime.now(timezone.utc):
        raise SignalLifecycleError(
            "A new signal cannot already be expired."
        )

    if metadata is not None and not isinstance(metadata, Mapping):
        raise SignalLifecycleError(
            "metadata must be a mapping."
        )

    signal_id = str(uuid4())

    return {
        "signal_id": signal_id,
        "symbol": symbol.strip().upper(),
        "direction": direction,
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit": target,
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
        "confidence": confidence,
        "source": source.strip(),
        "metadata": dict(metadata or {}),
        "status": "NEW",
        "current_price": None,
        "last_updated_at": created.isoformat(),
        "history": [
            {
                "status": "NEW",
                "timestamp": created.isoformat(),
                "reason": "SIGNAL_CREATED",
            }
        ],
        "execution_authorized": False,
    }


def transition_signal(
    signal: Mapping[str, Any],
    new_status: str,
    timestamp: str,
    reason: str,
) -> dict[str, Any]:
    if not isinstance(signal, Mapping):
        raise SignalLifecycleError(
            "signal must be a mapping."
        )

    updated = dict(signal)

    _, expires, history = _validate_signal_state(updated)

    current_status = updated["status"]

    if current_status in TERMINAL_STATUSES:
        raise SignalLifecycleError(
            "Terminal signals cannot be modified."
        )

    if not isinstance(new_status, str):
        raise SignalLifecycleError(
            "Target status must be a string."
        )

    new_status = new_status.strip().upper()

    if new_status not in VALID_STATUSES:
        raise SignalLifecycleError(
            "Invalid target status."
        )

    if new_status not in ALLOWED_TRANSITIONS.get(
        current_status,
        set(),
    ):
        raise SignalLifecycleError(
            f"Invalid transition: {current_status} -> {new_status}."
        )

    event_time = _timestamp(timestamp, "timestamp")
    _validate_event_time(event_time, "timestamp")

    previous_time = _timestamp(
        updated["last_updated_at"],
        "last_updated_at",
    )

    if event_time < previous_time:
        raise SignalLifecycleError(
            "Lifecycle timestamps cannot move backwards."
        )

    if event_time >= expires and new_status != "EXPIRED":
        raise SignalLifecycleError(
            "Expired signals can only transition to EXPIRED."
        )

    if not isinstance(reason, str) or not reason.strip():
        raise SignalLifecycleError(
            "Transition reason is required."
        )

    updated["status"] = new_status
    updated["last_updated_at"] = event_time.isoformat()

    history.append({
        "status": new_status,
        "timestamp": event_time.isoformat(),
        "reason": reason.strip(),
    })

    updated["history"] = history
    updated["execution_authorized"] = False

    return updated


def update_signal_price(
    signal: Mapping[str, Any],
    current_price: float,
    timestamp: str,
) -> dict[str, Any]:
    if not isinstance(signal, Mapping):
        raise SignalLifecycleError(
            "signal must be a mapping."
        )

    updated = dict(signal)

    _, expires, history = _validate_signal_state(updated)

    if updated["status"] != "ACTIVE":
        raise SignalLifecycleError(
            "Only ACTIVE signals can receive price updates."
        )

    price = _positive(
        current_price,
        "current_price",
    )

    event_time = _timestamp(timestamp, "timestamp")
    _validate_event_time(event_time, "timestamp")

    last_updated = _timestamp(
        updated["last_updated_at"],
        "last_updated_at",
    )

    if event_time < last_updated:
        raise SignalLifecycleError(
            "Price update timestamp cannot move backwards."
        )

    if event_time >= expires:
        return transition_signal(
            updated,
            "EXPIRED",
            event_time.isoformat(),
            "SIGNAL_EXPIRATION_REACHED",
        )

    direction = updated["direction"]

    stop = _positive(
        updated["stop_loss"],
        "stop_loss",
    )

    target = _positive(
        updated["take_profit"],
        "take_profit",
    )

    previous_price = updated.get("current_price")

    if previous_price is not None:
        previous_price = _positive(
            previous_price,
            "current_price",
        )

    updated["current_price"] = price

    if direction == "BUY":
        stop_hit = price <= stop
        target_hit = price >= target
    else:
        stop_hit = price >= stop
        target_hit = price <= target

    if stop_hit:
        return transition_signal(
            updated,
            "STOP_HIT",
            event_time.isoformat(),
            "PRICE_REACHED_STOP_LOSS",
        )

    if target_hit:
        return transition_signal(
            updated,
            "TARGET_HIT",
            event_time.isoformat(),
            "PRICE_REACHED_TAKE_PROFIT",
        )

    updated["last_updated_at"] = event_time.isoformat()

    history.append({
        "status": "ACTIVE",
        "timestamp": event_time.isoformat(),
        "reason": "PRICE_UPDATED",
        "current_price": price,
        "previous_price": previous_price,
    })

    updated["history"] = history
    updated["execution_authorized"] = False

    return updated


def detect_signal_conflicts(
    signals: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if (
        isinstance(signals, (str, bytes))
        or not isinstance(signals, Sequence)
    ):
        raise SignalLifecycleError(
            "signals must be a sequence."
        )

    grouped = {}

    for signal in signals:
        if not isinstance(signal, Mapping):
            raise SignalLifecycleError(
                "Each signal must be a mapping."
            )

        symbol = signal.get("symbol")
        status = signal.get("status")
        direction = signal.get("direction")

        if not isinstance(symbol, str) or not symbol.strip():
            continue

        if status not in {"NEW", "CONFIRMED", "ACTIVE"}:
            continue

        if direction not in VALID_DIRECTIONS:
            continue

        grouped.setdefault(
            symbol.strip().upper(),
            [],
        ).append(signal)

    conflicts = []

    for symbol, items in grouped.items():
        bullish = [
            item for item in items
            if item["direction"] == "BUY"
        ]

        bearish = [
            item for item in items
            if item["direction"] == "SELL"
        ]

        if bullish and bearish:
            conflicts.append({
                "symbol": symbol,
                "conflict": True,
                "bullish_signal_ids": [
                    item.get("signal_id")
                    for item in bullish
                ],
                "bearish_signal_ids": [
                    item.get("signal_id")
                    for item in bearish
                ],
                "reason": "OPPOSING_ACTIVE_ANALYTICAL_SIGNALS",
            })

    return conflicts


def summarize_signal(
    signal: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(signal, Mapping):
        raise SignalLifecycleError(
            "signal must be a mapping."
        )

    status = signal.get("status")

    if status not in VALID_STATUSES:
        raise SignalLifecycleError(
            "Invalid signal status."
        )

    _validate_signal_identity(signal)

    return {
        "signal_id": signal.get("signal_id"),
        "symbol": signal.get("symbol"),
        "direction": signal.get("direction"),
        "status": status,
        "entry_price": signal.get("entry_price"),
        "current_price": signal.get("current_price"),
        "stop_loss": signal.get("stop_loss"),
        "take_profit": signal.get("take_profit"),
        "confidence": signal.get("confidence"),
        "created_at": signal.get("created_at"),
        "expires_at": signal.get("expires_at"),
        "last_updated_at": signal.get("last_updated_at"),
        "terminal": status in TERMINAL_STATUSES,
        "execution_authorized": False,
    }
