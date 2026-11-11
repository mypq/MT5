"""
GOLD BOT - Price Action Analyzer

Detects common candlestick patterns using OHLC data.

Patterns are analytical observations, not standalone trading signals.
Candles must be ordered from oldest to newest.
The caller must supply closed candles only.
"""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

class PriceActionError(ValueError):
"""Raised when candle data is invalid."""

def _number(candle: Mapping[str, Any], field: str) -> float:
value = candle.get(field)

if isinstance(value, bool):
    raise PriceActionError(f"Invalid {field} value.")

try:
    result = float(value)
except (TypeError, ValueError):
    raise PriceActionError(f"Missing or invalid {field} value.")

if not isfinite(result) or result <= 0:
    raise PriceActionError(
        f"{field} must be a finite positive price."
    )

return result

def _validate_candle(
candle: Mapping[str, Any],
index: int,
) -> dict[str, float]:

if not isinstance(candle, Mapping):
    raise PriceActionError(
        f"Candle {index} must be an object."
    )

open_price = _number(candle, "open")
high = _number(candle, "high")
low = _number(candle, "low")
close = _number(candle, "close")

if high < low:
    raise PriceActionError(
        f"Candle {index}: high is below low."
    )

if high < max(open_price, close):
    raise PriceActionError(
        f"Candle {index}: inconsistent high."
    )

if low > min(open_price, close):
    raise PriceActionError(
        f"Candle {index}: inconsistent low."
    )

return {
    "open": open_price,
    "high": high,
    "low": low,
    "close": close,
}

def _measure(candle: dict[str, float]) -> dict[str, float]:
open_price = candle["open"]
close = candle["close"]
high = candle["high"]
low = candle["low"]

body = abs(close - open_price)
total_range = high - low

upper_wick = high - max(open_price, close)
lower_wick = min(open_price, close) - low

if total_range <= 0:
    return {
        "body": body,
        "range": 0.0,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "body_ratio": 0.0,
        "upper_wick_ratio": 0.0,
        "lower_wick_ratio": 0.0,
    }

return {
    "body": body,
    "range": total_range,
    "upper_wick": upper_wick,
    "lower_wick": lower_wick,
    "body_ratio": body / total_range,
    "upper_wick_ratio": upper_wick / total_range,
    "lower_wick_ratio": lower_wick / total_range,
}

def analyze_price_action(
candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
"""
Detect patterns in the most recent two closed candles.

Required input:
    At least two chronological OHLC candles.

Returns:
    Detected patterns, candle measurements and limitations.
"""

if isinstance(candles, (str, bytes)):
    raise PriceActionError("Candles must be a sequence.")

if len(candles) < 2:
    raise PriceActionError(
        "At least two closed candles are required."
    )

previous = _validate_candle(candles[-2], len(candles) - 2)
current = _validate_candle(candles[-1], len(candles) - 1)

previous_metrics = _measure(previous)
current_metrics = _measure(current)

previous_bullish = previous["close"] > previous["open"]
previous_bearish = previous["close"] < previous["open"]

current_bullish = current["close"] > current["open"]
current_bearish = current["close"] < current["open"]

patterns = []

# Bullish engulfing: current real body covers previous real body.
if (
    previous_bearish
    and current_bullish
    and current["open"] <= previous["close"]
    and current["close"] >= previous["open"]
):
    patterns.append({
        "name": "BULLISH_ENGULFING",
        "direction": "BULLISH",
        "description": "The bullish candle body engulfs the previous bearish body.",
    })

# Bearish engulfing.
if (
    previous_bullish
    and current_bearish
    and current["open"] >= previous["close"]
    and current["close"] <= previous["open"]
):
    patterns.append({
        "name": "BEARISH_ENGULFING",
        "direction": "BEARISH",
        "description": "The bearish candle body engulfs the previous bullish body.",
    })

body = current_metrics["body"]
candle_range = current_metrics["range"]
upper_wick = current_metrics["upper_wick"]
lower_wick = current_metrics["lower_wick"]

# Doji: small real body relative to the full candle range.
if (
    candle_range > 0
    and current_metrics["body_ratio"] <= 0.10
):
    patterns.append({
        "name": "DOJI",
        "direction": "NEUTRAL",
        "description": "The candle has a very small real body, indicating indecision.",
    })

# Hammer-like candle. Context is needed to confirm a reversal.
if (
    candle_range > 0
    and lower_wick >= max(body * 2.0, candle_range * 0.50)
    and upper_wick <= candle_range * 0.20
):
    patterns.append({
        "name": "HAMMER_SHAPE",
        "direction": "POTENTIALLY_BULLISH",
        "description": "Long lower wick with a small upper wick. Downtrend context is required.",
    })

# Shooting-star-like candle.
if (
    candle_range > 0
    and upper_wick >= max(body * 2.0, candle_range * 0.50)
    and lower_wick <= candle_range * 0.20
):
    patterns.append({
        "name": "SHOOTING_STAR_SHAPE",
        "direction": "POTENTIALLY_BEARISH",
        "description": "Long upper wick with a small lower wick. Uptrend context is required.",
    })

# Strong directional candle.
if (
    candle_range > 0
    and current_metrics["body_ratio"] >= 0.80
    and upper_wick / candle_range <= 0.10
    and lower_wick / candle_range <= 0.10
):
    patterns.append({
        "name": (
            "STRONG_BULLISH_CANDLE"
            if current_bullish
            else "STRONG_BEARISH_CANDLE"
            if current_bearish
            else "NONE"
        ),
        "direction": (
            "BULLISH"
            if current_bullish
            else "BEARISH"
            if current_bearish
            else "NEUTRAL"
        ),
        "description": "Large real body with very small wicks.",
    })

patterns = [
    pattern for pattern in patterns
    if pattern["name"] != "NONE"
]

if not patterns:
    overall_direction = "NEUTRAL"
else:
    directions = {
        pattern["direction"]
        for pattern in patterns
    }

    if directions == {"BULLISH"}:
        overall_direction = "BULLISH"
    elif directions == {"BEARISH"}:
        overall_direction = "BEARISH"
    else:
        overall_direction = "MIXED"

return {
    "module": "price_action",
    "data_available": True,
    "candles_analyzed": 2,
    "direction": overall_direction,
    "patterns": patterns,
    "latest_candle": {
        "open": current["open"],
        "high": current["high"],
        "low": current["low"],
        "close": current["close"],
        "body_ratio": current_metrics["body_ratio"],
        "upper_wick_ratio": current_metrics["upper_wick_ratio"],
        "lower_wick_ratio": current_metrics["lower_wick_ratio"],
    },
    "previous_candle": {
        "open": previous["open"],
        "high": previous["high"],
        "low": previous["low"],
        "close": previous["close"],
    },
    "limitations": [
        "Candlestick patterns require market context.",
        "A hammer shape is not proof of a bullish reversal.",
        "A shooting star shape is not proof of a bearish reversal.",
        "This module does not generate trading orders.",
    ],
}
