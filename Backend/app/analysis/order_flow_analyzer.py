"""
GOLD BOT - Order Flow & Footprint Intelligence

Analyzes genuine bid/ask volume at individual price levels.

Features:

- Bid and ask volume
- Delta per price level
- Candle delta
- Cumulative delta
- Buy and sell imbalance detection
- Stacked imbalance detection
- Potential absorption detection

IMPORTANT:
This module requires actual bid/ask volume data.
It does not manufacture order flow from OHLC candles.
It does not execute trades.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite
from typing import Any

SUPPORTED_SYMBOL = "XAUUSD"

MINIMUM_CANDLES = 3
MAXIMUM_CANDLES = 5000
MAXIMUM_LEVELS_PER_CANDLE = 10000

DEFAULT_IMBALANCE_RATIO = 3.0
DEFAULT_MINIMUM_SIDE_VOLUME = 10.0
DEFAULT_STACKED_LEVELS = 3

class OrderFlowAnalysisError(ValueError):
"""Raised when order flow input is invalid."""

def _number(value: Any, field: str) -> float:

if isinstance(value, bool):
    raise OrderFlowAnalysisError(
        f"{field} must be numeric."
    )

try:
    result = float(value)
except (TypeError, ValueError):
    raise OrderFlowAnalysisError(
        f"{field} must be numeric."
    )

if not isfinite(result):
    raise OrderFlowAnalysisError(
        f"{field} must be finite."
    )

return result

def _timestamp(value: Any, field: str) -> datetime:

if not isinstance(value, str):
    raise OrderFlowAnalysisError(
        f"{field} must be an ISO-8601 timestamp."
    )

try:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
except ValueError:
    raise OrderFlowAnalysisError(
        f"{field} is not a valid ISO-8601 timestamp."
    )

if parsed.tzinfo is None:
    raise OrderFlowAnalysisError(
        f"{field} must include a timezone."
    )

return parsed.astimezone(timezone.utc)

def _validate_levels(
levels: Any,
candle_index: int,
) -> list[dict[str, float]]:

if not isinstance(levels, Sequence) or isinstance(
    levels,
    (str, bytes),
):
    raise OrderFlowAnalysisError(
        f"candle[{candle_index}].levels must be a sequence."
    )

if not levels:
    raise OrderFlowAnalysisError(
        f"candle[{candle_index}] has no footprint levels."
    )

if len(levels) > MAXIMUM_LEVELS_PER_CANDLE:
    raise OrderFlowAnalysisError(
        f"candle[{candle_index}] exceeds the maximum level count."
    )

validated = []
seen_prices = set()

for level_index, level in enumerate(levels):

    if not isinstance(level, Mapping):
        raise OrderFlowAnalysisError(
            f"Invalid level at candle {candle_index}, level {level_index}."
        )

    price = _number(
        level.get("price"),
        f"candle[{candle_index}].levels[{level_index}].price",
    )

    bid = _number(
        level.get("bid_volume"),
        f"candle[{candle_index}].levels[{level_index}].bid_volume",
    )

    ask = _number(
        level.get("ask_volume"),
        f"candle[{candle_index}].levels[{level_index}].ask_volume",
    )

    if price <= 0:
        raise OrderFlowAnalysisError(
            "Price levels must be positive."
        )

    if bid < 0 or ask < 0:
        raise OrderFlowAnalysisError(
            "Bid and ask volumes cannot be negative."
        )

    if price in seen_prices:
        raise OrderFlowAnalysisError(
            f"Duplicate price level in candle {candle_index}."
        )

    seen_prices.add(price)

    validated.append({
        "price": price,
        "bid_volume": bid,
        "ask_volume": ask,
    })

validated.sort(
    key=lambda item: item["price"]
)

return validated

def _calculate_level_imbalances(
levels: Sequence[Mapping[str, float]],
imbalance_ratio: float,
minimum_side_volume: float,
) -> list[dict[str, Any]]:

results = []

for index, level in enumerate(levels):

    bid = level["bid_volume"]
    ask = level["ask_volume"]

    delta = ask - bid

    total = ask + bid

    if total > 0:
        delta_percent = (
            delta / total
        ) * 100.0
    else:
        delta_percent = 0.0

    buy_imbalance = (
        ask >= minimum_side_volume
        and ask >= max(bid, 1e-12) * imbalance_ratio
    )

    sell_imbalance = (
        bid >= minimum_side_volume
        and bid >= max(ask, 1e-12) * imbalance_ratio
    )

    if buy_imbalance:
        imbalance = "BUY"

    elif sell_imbalance:
        imbalance = "SELL"

    else:
        imbalance = "NONE"

    results.append({
        "price": level["price"],
        "bid_volume": bid,
        "ask_volume": ask,
        "delta": delta,
        "total_volume": total,
        "delta_percent": delta_percent,
        "imbalance": imbalance,
    })

return results

def _detect_stacked_imbalances(
levels: Sequence[Mapping[str, Any]],
minimum_stacked_levels: int,
) -> list[dict[str, Any]]:

stacks = []

current_direction = None
current_levels = []

def finish_stack():

    nonlocal current_direction
    nonlocal current_levels

    if (
        current_direction is not None
        and len(current_levels) >= minimum_stacked_levels
    ):

        stacks.append({
            "direction": current_direction,
            "levels_count": len(current_levels),
            "price_low": current_levels[0]["price"],
            "price_high": current_levels[-1]["price"],
            "levels": [
                item["price"]
                for item in current_levels
            ],
        })

    current_direction = None
    current_levels = []

for level in levels:

    direction = (
        level["imbalance"]
        if level["imbalance"] in ("BUY", "SELL")
        else None
    )

    if direction is None:
        finish_stack()
        continue

    if direction != current_direction:
        finish_stack()

        current_direction = direction
        current_levels = [level]

    else:
        current_levels.append(level)

finish_stack()

return stacks

def _analyze_candle(
candle: Mapping[str, Any],
candle_index: int,
imbalance_ratio: float,
minimum_side_volume: float,
minimum_stacked_levels: int,
) -> dict[str, Any]:

timestamp = _timestamp(
    candle.get("timestamp"),
    f"candles[{candle_index}].timestamp",
)

open_price = _number(
    candle.get("open"),
    f"candles[{candle_index}].open",
)

high = _number(
    candle.get("high"),
    f"candles[{candle_index}].high",
)

low = _number(
    candle.get("low"),
    f"candles[{candle_index}].low",
)

close = _number(
    candle.get("close"),
    f"candles[{candle_index}].close",
)

if min(open_price, high, low, close) <= 0:
    raise OrderFlowAnalysisError(
        f"Prices must be positive in candle {candle_index}."
    )

if high < low:
    raise OrderFlowAnalysisError(
        f"Invalid high/low range in candle {candle_index}."
    )

if not low <= open_price <= high:
    raise OrderFlowAnalysisError(
        f"Open is outside the candle range at index {candle_index}."
    )

if not low <= close <= high:
    raise OrderFlowAnalysisError(
        f"Close is outside the candle range at index {candle_index}."
    )

levels = _validate_levels(
    candle.get("levels"),
    candle_index,
)

for level in levels:

    if not low <= level["price"] <= high:
        raise OrderFlowAnalysisError(
            f"Footprint level is outside candle range at index {candle_index}."
        )

analyzed_levels = _calculate_level_imbalances(
    levels,
    imbalance_ratio,
    minimum_side_volume,
)

total_bid = sum(
    item["bid_volume"]
    for item in analyzed_levels
)

total_ask = sum(
    item["ask_volume"]
    for item in analyzed_levels
)

total_volume = total_bid + total_ask

delta = total_ask - total_bid

delta_percent = (
    delta / total_volume * 100.0
    if total_volume > 0
    else 0.0
)

stacks = _detect_stacked_imbalances(
    analyzed_levels,
    minimum_stacked_levels,
)

buy_stacks = sum(
    1 for stack in stacks
    if stack["direction"] == "BUY"
)

sell_stacks = sum(
    1 for stack in stacks
    if stack["direction"] == "SELL"
)

return {
    "timestamp": timestamp.isoformat(),
    "open": open_price,
    "high": high,
    "low": low,
    "close": close,
    "bid_volume": total_bid,
    "ask_volume": total_ask,
    "total_volume": total_volume,
    "delta": delta,
    "delta_percent": delta_percent,
    "direction": (
        "BUY_PRESSURE"
        if delta > 0
        else "SELL_PRESSURE"
        if delta < 0
        else "BALANCED"
    ),
    "buy_imbalance_levels": sum(
        1 for item in analyzed_levels
        if item["imbalance"] == "BUY"
    ),
    "sell_imbalance_levels": sum(
        1 for item in analyzed_levels
        if item["imbalance"] == "SELL"
    ),
    "stacked_imbalances": stacks,
    "buy_stacked_count": buy_stacks,
    "sell_stacked_count": sell_stacks,
    "footprint": analyzed_levels,
}

def _detect_absorption(
candles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:

events = []

for index, candle in enumerate(candles):

    candle_range = candle["high"] - candle["low"]

    if candle_range <= 0:
        continue

    close_position = (
        candle["close"] - candle["low"]
    ) / candle_range

    delta = candle["delta"]

    delta_percent = candle["delta_percent"]

    # Strong positive delta but a close near the low:
    # possible aggressive buying absorbed by sellers.
    if (
        delta_percent >= 30.0
        and close_position <= 0.35
    ):

        events.append({
            "timestamp": candle["timestamp"],
            "type": "POTENTIAL_BUY_ABSORPTION",
            "price": candle["close"],
            "delta_percent": delta_percent,
            "explanation": (
                "Positive aggressive volume was present, "
                "but price closed relatively near the candle low."
            ),
        })

    # Strong negative delta but a close near the high:
    # possible aggressive selling absorbed by buyers.
    elif (
        delta_percent <= -30.0
        and close_position >= 0.65
    ):

        events.append({
            "timestamp": candle["timestamp"],
            "type": "POTENTIAL_SELL_ABSORPTION",
            "price": candle["close"],
            "delta_percent": delta_percent,
            "explanation": (
                "Negative aggressive volume was present, "
                "but price closed relatively near the candle high."
            ),
        })

return events

def analyze_order_flow(
candles: Sequence[Mapping[str, Any]],
imbalance_ratio: float = DEFAULT_IMBALANCE_RATIO,
minimum_side_volume: float = DEFAULT_MINIMUM_SIDE_VOLUME,
minimum_stacked_levels: int = DEFAULT_STACKED_LEVELS,
) -> dict[str, Any]:

if isinstance(candles, (str, bytes)) or not isinstance(
    candles,
    Sequence,
):
    raise OrderFlowAnalysisError(
        "Candles must be a sequence."
    )

if not MINIMUM_CANDLES <= len(candles) <= MAXIMUM_CANDLES:
    raise OrderFlowAnalysisError(
        f"Candles must contain between {MINIMUM_CANDLES} "
        f"and {MAXIMUM_CANDLES} observations."
    )

if (
    isinstance(imbalance_ratio, bool)
    or not isinstance(imbalance_ratio, (int, float))
    or not isfinite(imbalance_ratio)
    or imbalance_ratio <= 1.0
):
    raise OrderFlowAnalysisError(
        "imbalance_ratio must be greater than 1."
    )

if (
    isinstance(minimum_side_volume, bool)
    or not isinstance(minimum_side_volume, (int, float))
    or not isfinite(minimum_side_volume)
    or minimum_side_volume < 0
):
    raise OrderFlowAnalysisError(
        "minimum_side_volume must be non-negative."
    )

if (
    isinstance(minimum_stacked_levels, bool)
    or not isinstance(minimum_stacked_levels, int)
    or not 2 <= minimum_stacked_levels <= 20
):
    raise OrderFlowAnalysisError(
        "minimum_stacked_levels must be between 2 and 20."
    )

results = []

previous_timestamp = None

cumulative_delta = 0.0

for index, candle in enumerate(candles):

    if not isinstance(candle, Mapping):
        raise OrderFlowAnalysisError(
            f"Invalid candle at index {index}."
        )

    result = _analyze_candle(
        candle,
        index,
        float(imbalance_ratio),
        float(minimum_side_volume),
        minimum_stacked_levels,
    )

    current_timestamp = datetime.fromisoformat(
        result["timestamp"]
    )

    if (
        previous_timestamp is not None
        and current_timestamp <= previous_timestamp
    ):
        raise OrderFlowAnalysisError(
            "Candle timestamps must be strictly increasing and unique."
        )

    previous_timestamp = current_timestamp

    cumulative_delta += result["delta"]

    result["cumulative_delta"] = cumulative_delta

    results.append(result)

total_bid = sum(
    candle["bid_volume"]
    for candle in results
)

total_ask = sum(
    candle["ask_volume"]
    for candle in results
)

total_volume = total_bid + total_ask

total_delta = total_ask - total_bid

absorption_events = _detect_absorption(
    results
)

return {
    "module": "order_flow_footprint_intelligence",
    "symbol": SUPPORTED_SYMBOL,
    "data_available": True,
    "data_type": "BID_ASK_VOLUME_BY_PRICE",
    "summary": {
        "candles_analyzed": len(results),
        "total_bid_volume": total_bid,
        "total_ask_volume": total_ask,
        "total_volume": total_volume,
        "total_delta": total_delta,
        "delta_percent": (
            total_delta / total_volume * 100.0
            if total_volume > 0
            else 0.0
        ),
        "final_cumulative_delta": cumulative_delta,
        "overall_pressure": (
            "BUY_PRESSURE"
            if total_delta > 0
            else "SELL_PRESSURE"
            if total_delta < 0
            else "BALANCED"
        ),
    },
    "settings": {
        "imbalance_ratio": imbalance_ratio,
        "minimum_side_volume": minimum_side_volume,
        "minimum_stacked_levels": minimum_stacked_levels,
    },
    "candles": results,
    "absorption_events": absorption_events,
    "limitations": [
        "Requires genuine bid and ask volume separated by price level.",
        "Broker tick volume alone is not sufficient for true footprint analysis.",
        "Absorption events are potential interpretations, not confirmed hidden orders.",
        "Imbalance thresholds require instrument and timeframe calibration.",
        "Cumulative delta resets at the beginning of each supplied dataset.",
        "This module does not fetch market data or execute orders.",
    ],
}
