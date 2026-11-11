"""
GOLD BOT - Market Structure Analyzer

Detects:
- Confirmed swing highs and lows
- Higher highs (HH) and higher lows (HL)
- Lower highs (LH) and lower lows (LL)
- Break of Structure (BOS)
- Change of Character (CHoCH)

Input candles must be chronological: oldest to newest.
Only closed candles should be supplied.

A swing is confirmed only after the required candles
on its right side have closed.

This module performs analysis only.
It never generates or executes trading orders.
"""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any


MINIMUM_CANDLES = 30
SWING_STRENGTH = 3


class MarketStructureError(ValueError):
    """Raised when market structure data is invalid."""


def _read_price(candle: Mapping[str, Any], field: str) -> float:
    value = candle.get(field)

    if isinstance(value, bool):
        raise MarketStructureError(f"Invalid {field} value.")

    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarketStructureError(
            f"Missing or invalid {field}."
        ) from exc

    if not isfinite(price) or price <= 0:
        raise MarketStructureError(
            f"{field} must be finite and positive."
        )

    return price


def _validate_candles(
    candles: Sequence[Mapping[str, Any]],
) -> list[dict[str, float]]:
    if not isinstance(candles, Sequence) or isinstance(
        candles, (str, bytes)
    ):
        raise MarketStructureError("Invalid candle collection.")

    if len(candles) < MINIMUM_CANDLES:
        raise MarketStructureError(
            f"At least {MINIMUM_CANDLES} candles are required."
        )

    validated = []

    for index, candle in enumerate(candles):
        if not isinstance(candle, Mapping):
            raise MarketStructureError(
                f"Invalid candle at index {index}."
            )

        open_price = _read_price(candle, "open")
        high = _read_price(candle, "high")
        low = _read_price(candle, "low")
        close = _read_price(candle, "close")

        if high < low:
            raise MarketStructureError(
                f"High cannot be below low at index {index}."
            )

        if high < max(open_price, close):
            raise MarketStructureError(
                f"Invalid candle high at index {index}."
            )

        if low > min(open_price, close):
            raise MarketStructureError(
                f"Invalid candle low at index {index}."
            )

        validated.append({
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
        })

    return validated


def _validate_strength(strength: int) -> None:
    if isinstance(strength, bool) or not isinstance(strength, int):
        raise MarketStructureError(
            "Swing strength must be an integer."
        )

    if strength < 1:
        raise MarketStructureError(
            "Swing strength must be positive."
        )


def _find_swings(
    candles: Sequence[dict[str, float]],
    strength: int = SWING_STRENGTH,
) -> list[dict[str, Any]]:
    _validate_strength(strength)

    swings = []

    for index in range(strength, len(candles) - strength):
        current = candles[index]

        left = candles[index - strength:index]
        right = candles[index + 1:index + strength + 1]

        is_swing_high = all(
            current["high"] > item["high"]
            for item in left + right
        )

        is_swing_low = all(
            current["low"] < item["low"]
            for item in left + right
        )

        if is_swing_high:
            swings.append({
                "type": "HIGH",
                "index": index,
                "price": current["high"],
                "confirmation_index": index + strength,
                "confirmed": True,
            })

        if is_swing_low:
            swings.append({
                "type": "LOW",
                "index": index,
                "price": current["low"],
                "confirmation_index": index + strength,
                "confirmed": True,
            })

    return sorted(
        swings,
        key=lambda item: (
            item["index"],
            item["type"],
        ),
    )


def _classify_swings(
    swings: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous_high = None
    previous_low = None
    classified = []

    for swing in swings:
        item = dict(swing)

        if item["type"] == "HIGH":
            if previous_high is None:
                item["classification"] = "INITIAL_HIGH"
            elif item["price"] > previous_high:
                item["classification"] = "HH"
            elif item["price"] < previous_high:
                item["classification"] = "LH"
            else:
                item["classification"] = "EQUAL_HIGH"

            previous_high = item["price"]

        else:
            if previous_low is None:
                item["classification"] = "INITIAL_LOW"
            elif item["price"] > previous_low:
                item["classification"] = "HL"
            elif item["price"] < previous_low:
                item["classification"] = "LL"
            else:
                item["classification"] = "EQUAL_LOW"

            previous_low = item["price"]

        classified.append(item)

    return classified


def _detect_structure_breaks(
    candles: Sequence[dict[str, float]],
    swings: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    events = []
    broken_levels = set()
    current_trend = "UNKNOWN"

    # Process candles chronologically.
    for index, candle in enumerate(candles):
        # A swing cannot be broken before it is confirmed.
        available_swings = [
            swing for swing in swings
            if swing["confirmation_index"] < index
        ]

        candidates = []

        for swing in available_swings:
            key = (swing["type"], swing["index"])

            if key in broken_levels:
                continue

            if swing["type"] == "HIGH":
                if candle["close"] > swing["price"]:
                    candidates.append(swing)

            elif swing["type"] == "LOW":
                if candle["close"] < swing["price"]:
                    candidates.append(swing)

        if not candidates:
            continue

        # Select the nearest previously confirmed level
        # for each direction, avoiding duplicate break events.
        bullish = [
            swing for swing in candidates
            if swing["type"] == "HIGH"
        ]

        bearish = [
            swing for swing in candidates
            if swing["type"] == "LOW"
        ]

        selected = []

        if bullish:
            selected.append(
                max(bullish, key=lambda item: item["index"])
            )

        if bearish:
            selected.append(
                max(bearish, key=lambda item: item["index"])
            )

        for swing in selected:
            direction = (
                "BULLISH"
                if swing["type"] == "HIGH"
                else "BEARISH"
            )

            event_type = (
                "BOS"
                if current_trend in ("UNKNOWN", direction)
                else "CHOCH"
            )

            events.append({
                "event": event_type,
                "direction": direction,
                "level_type": swing["type"],
                "level_price": swing["price"],
                "level_index": swing["index"],
                "confirmation_index": swing["confirmation_index"],
                "break_index": index,
                "break_close": candle["close"],
                "confirmation": "CANDLE_CLOSE",
            })

            broken_levels.add(
                (swing["type"], swing["index"])
            )

            current_trend = direction

    return events


def analyze_market_structure(
    candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    data = _validate_candles(candles)

    raw_swings = _find_swings(data)
    classified_swings = _classify_swings(raw_swings)

    events = _detect_structure_breaks(
        data,
        raw_swings,
    )

    recent_highs = [
        item for item in classified_swings
        if item["type"] == "HIGH"
    ]

    recent_lows = [
        item for item in classified_swings
        if item["type"] == "LOW"
    ]

    latest_high = recent_highs[-1] if recent_highs else None
    latest_low = recent_lows[-1] if recent_lows else None

    latest_event = events[-1] if events else None

    if latest_event is None:
        structure_trend = "UNKNOWN"
    else:
        structure_trend = latest_event["direction"]

    return {
        "module": "market_structure",
        "data_available": True,
        "candles_analyzed": len(data),
        "swing_count": len(classified_swings),
        "swings": classified_swings,
        "latest_swing_high": latest_high,
        "latest_swing_low": latest_low,
        "structure_trend": structure_trend,
        "structure_events": events,
        "latest_structure_event": latest_event,
        "limitations": [
            "Swing points require future candles for confirmation.",
            "Only confirmed swing points are included.",
            "BOS and CHoCH require a candle close beyond a level.",
            "Equal highs and lows are classified separately.",
            "This is structural analysis, not a trade instruction.",
        ],
    }
