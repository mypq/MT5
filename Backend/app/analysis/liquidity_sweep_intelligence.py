"""
GOLD BOT - Liquidity Sweep Intelligence

Detects potential liquidity pools and sweep events
from historical OHLC candles.

Features:

- Equal highs and equal lows
- Buy-side and sell-side liquidity zones
- Sweep and rejection detection
- Breakout continuation candidates
- Zone consumption tracking

IMPORTANT:

- OHLC data cannot reveal actual resting stop orders.
- Detected liquidity zones are price-structure estimates.
- A sweep candidate is not proof of institutional activity.
- No trading orders are executed.
  """

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any
from uuid import uuid4

class LiquiditySweepError(ValueError):
"""Raised when liquidity sweep input is invalid."""

def _positive(
value: Any,
field: str,
) -> float:

if isinstance(value, bool):
    raise LiquiditySweepError(
        f"{field} must be numeric."
    )

try:
    number = float(value)
except (TypeError, ValueError):
    raise LiquiditySweepError(
        f"{field} must be numeric."
    )

if not isfinite(number) or number <= 0:
    raise LiquiditySweepError(
        f"{field} must be positive and finite."
    )

return number

def _validate_candles(
candles: Sequence[Mapping[str, Any]],
) -> list[dict[str, float]]:

if (
    isinstance(candles, (str, bytes))
    or not isinstance(candles, Sequence)
):
    raise LiquiditySweepError(
        "candles must be a sequence."
    )

if len(candles) < 3:
    raise LiquiditySweepError(
        "At least three candles are required."
    )

result = []

for index, candle in enumerate(candles):

    if not isinstance(candle, Mapping):
        raise LiquiditySweepError(
            f"Invalid candle at index {index}."
        )

    open_price = _positive(
        candle.get("open"),
        f"candles[{index}].open",
    )

    high = _positive(
        candle.get("high"),
        f"candles[{index}].high",
    )

    low = _positive(
        candle.get("low"),
        f"candles[{index}].low",
    )

    close = _positive(
        candle.get("close"),
        f"candles[{index}].close",
    )

    if low > high:
        raise LiquiditySweepError(
            f"Invalid high/low at index {index}."
        )

    if not low <= open_price <= high:
        raise LiquiditySweepError(
            f"Open outside candle range at index {index}."
        )

    if not low <= close <= high:
        raise LiquiditySweepError(
            f"Close outside candle range at index {index}."
        )

    result.append({
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
    })

return result

def _cluster_levels(
levels: Sequence[tuple[float, int]],
tolerance: float,
minimum_touches: int,
) -> list[dict[str, Any]]:

ordered = sorted(
    levels,
    key=lambda item: item[0],
)

clusters = []

for price, index in ordered:

    matching = None

    for cluster in clusters:

        center = sum(
            item[0]
            for item in cluster["members"]
        ) / len(cluster["members"])

        if abs(price - center) <= tolerance:

            matching = cluster
            break

    if matching is None:

        clusters.append({
            "members": [
                (price, index)
            ]
        })

    else:

        matching["members"].append(
            (price, index)
        )

results = []

for cluster in clusters:

    members = cluster["members"]

    if len(members) < minimum_touches:
        continue

    prices = [
        item[0]
        for item in members
    ]

    indices = [
        item[1]
        for item in members
    ]

    results.append({
        "level": sum(prices) / len(prices),
        "touch_count": len(members),
        "first_index": min(indices),
        "last_index": max(indices),
        "price_range": [
            min(prices),
            max(prices),
        ],
    })

return results

def detect_liquidity_zones(
candles: Sequence[Mapping[str, Any]],
tolerance: float,
minimum_touches: int = 2,
) -> dict[str, Any]:

data = _validate_candles(
    candles
)

tolerance = _positive(
    tolerance,
    "tolerance",
)

if (
    isinstance(minimum_touches, bool)
    or not isinstance(minimum_touches, int)
    or minimum_touches < 2
):
    raise LiquiditySweepError(
        "minimum_touches must be an integer >= 2."
    )

highs = [
    (candle["high"], index)
    for index, candle in enumerate(data)
]

lows = [
    (candle["low"], index)
    for index, candle in enumerate(data)
]

buy_side = _cluster_levels(
    highs,
    tolerance,
    minimum_touches,
)

sell_side = _cluster_levels(
    lows,
    tolerance,
    minimum_touches,
)

for zone in buy_side:

    zone["zone_id"] = str(uuid4())
    zone["side"] = "BUY_SIDE_LIQUIDITY"
    zone["status"] = "UNTESTED"

for zone in sell_side:

    zone["zone_id"] = str(uuid4())
    zone["side"] = "SELL_SIDE_LIQUIDITY"
    zone["status"] = "UNTESTED"

return {
    "buy_side_zones": buy_side,
    "sell_side_zones": sell_side,
    "zone_count": len(buy_side) + len(sell_side),
    "tolerance": tolerance,
    "minimum_touches": minimum_touches,
    "method": "OHLC_PRICE_CLUSTERING",
}

def detect_sweep_events(
candles: Sequence[Mapping[str, Any]],
zones: Mapping[str, Any],
minimum_penetration: float = 0.0,
rejection_threshold: float = 0.60,
) -> list[dict[str, Any]]:

data = _validate_candles(
    candles
)

if not isinstance(zones, Mapping):
    raise LiquiditySweepError(
        "zones must be a mapping."
    )

penetration = _number_or_zero(
    minimum_penetration,
    "minimum_penetration",
)

threshold = _number_or_zero(
    rejection_threshold,
    "rejection_threshold",
)

if threshold > 1:
    raise LiquiditySweepError(
        "rejection_threshold must be between 0 and 1."
    )

events = []

buy_zones = zones.get(
    "buy_side_zones",
    [],
)

sell_zones = zones.get(
    "sell_side_zones",
    [],
)

if not isinstance(buy_zones, list) or not isinstance(sell_zones, list):
    raise LiquiditySweepError(
        "Liquidity zone lists are invalid."
    )

for index, candle in enumerate(data):

    candle_range = (
        candle["high"] - candle["low"]
    )

    if candle_range <= 0:
        continue

    close_position = (
        candle["close"] - candle["low"]
    ) / candle_range

    for zone in buy_zones:

        level = _positive(
            zone.get("level"),
            "buy_side_zone.level",
        )

        if index <= zone.get("last_index", -1):
            continue

        if candle["high"] <= level:
            continue

        penetration_size = (
            candle["high"] - level
        )

        if penetration_size < penetration:
            continue

        rejection = (
            candle["close"] < level
        )

        strength = (
            (level - candle["close"]) / candle_range
            if rejection
            else 0.0
        )

        events.append({
            "event_id": str(uuid4()),
            "zone_id": zone.get("zone_id"),
            "index": index,
            "side": "BUY_SIDE",
            "level": level,
            "penetration": penetration_size,
            "sweep_candidate": rejection,
            "rejection_strength": strength,
            "strong_rejection": (
                rejection
                and strength >= threshold
            ),
            "close_position": close_position,
            "interpretation": (
                "POTENTIAL_BUY_SIDE_SWEEP"
                if rejection
                else "BREAKOUT_ABOVE_BUY_SIDE_LIQUIDITY"
            ),
        })

    for zone in sell_zones:

        level = _positive(
            zone.get("level"),
            "sell_side_zone.level",
        )

        if index <= zone.get("last_index", -1):
            continue

        if candle["low"] >= level:
            continue

        penetration_size = (
            level - candle["low"]
        )

        if penetration_size < penetration:
            continue

        rejection = (
            candle["close"] > level
        )

        strength = (
            (candle["close"] - level) / candle_range
            if rejection
            else 0.0
        )

        events.append({
            "event_id": str(uuid4()),
            "zone_id": zone.get("zone_id"),
            "index": index,
            "side": "SELL_SIDE",
            "level": level,
            "penetration": penetration_size,
            "sweep_candidate": rejection,
            "rejection_strength": strength,
            "strong_rejection": (
                rejection
                and strength >= threshold
            ),
            "close_position": close_position,
            "interpretation": (
                "POTENTIAL_SELL_SIDE_SWEEP"
                if rejection
                else "BREAKOUT_BELOW_SELL_SIDE_LIQUIDITY"
            ),
        })

return events

def _number_or_zero(
value: Any,
field: str,
) -> float:

if isinstance(value, bool):
    raise LiquiditySweepError(
        f"{field} must be numeric."
    )

try:
    number = float(value)
except (TypeError, ValueError):
    raise LiquiditySweepError(
        f"{field} must be numeric."
    )

if not isfinite(number) or number < 0:
    raise LiquiditySweepError(
        f"{field} must be finite and non-negative."
    )

return number

def update_zone_statuses(
zones: Mapping[str, Any],
sweep_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:

if not isinstance(zones, Mapping):
    raise LiquiditySweepError(
        "zones must be a mapping."
    )

if (
    isinstance(sweep_events, (str, bytes))
    or not isinstance(sweep_events, Sequence)
):
    raise LiquiditySweepError(
        "sweep_events must be a sequence."
    )

updated = {
    "buy_side_zones": [
        dict(item)
        for item in zones.get(
            "buy_side_zones",
            []
        )
    ],
    "sell_side_zones": [
        dict(item)
        for item in zones.get(
            "sell_side_zones",
            []
        )
    ],
}

event_map = {}

for event in sweep_events:

    if not isinstance(event, Mapping):
        raise LiquiditySweepError(
            "Each event must be a mapping."
        )

    zone_id = event.get("zone_id")

    if not zone_id:
        continue

    event_map.setdefault(
        zone_id,
        [],
    ).append(event)

for collection in (
    updated["buy_side_zones"],
    updated["sell_side_zones"],
):

    for zone in collection:

        zone_id = zone.get(
            "zone_id"
        )

        matching = event_map.get(
            zone_id,
            [],
        )

        if not matching:
            continue

        latest = max(
            matching,
            key=lambda item: item["index"],
        )

        zone["status"] = (
            "SWEPT"
            if latest.get("sweep_candidate")
            else "BROKEN"
        )

        zone["last_event_index"] = latest["index"]

return updated

def summarize_liquidity_activity(
zones: Mapping[str, Any],
events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:

if not isinstance(zones, Mapping):
    raise LiquiditySweepError(
        "zones must be a mapping."
    )

if (
    isinstance(events, (str, bytes))
    or not isinstance(events, Sequence)
):
    raise LiquiditySweepError(
        "events must be a sequence."
    )

all_zones = (
    list(zones.get("buy_side_zones", []))
    + list(zones.get("sell_side_zones", []))
)

buy_sweeps = [
    event for event in events
    if (
        event.get("side") == "BUY_SIDE"
        and event.get("sweep_candidate") is True
    )
]

sell_sweeps = [
    event for event in events
    if (
        event.get("side") == "SELL_SIDE"
        and event.get("sweep_candidate") is True
    )
]

return {
    "total_zones": len(all_zones),
    "buy_side_zones": len(
        zones.get("buy_side_zones", [])
    ),
    "sell_side_zones": len(
        zones.get("sell_side_zones", [])
    ),
    "total_sweep_candidates": (
        len(buy_sweeps) + len(sell_sweeps)
    ),
    "buy_side_sweep_candidates": len(buy_sweeps),
    "sell_side_sweep_candidates": len(sell_sweeps),
    "strong_rejections": sum(
        1 for event in events
        if event.get("strong_rejection") is True
    ),
    "institutional_activity_confirmed": False,
    "execution_authorized": False,
      }
