"""
GOLD BOT - Liquidity Sweep Intelligence

Detects estimated liquidity pools and sweep events
from historical OHLC candles.

IMPORTANT:
- OHLC data cannot reveal actual resting stop orders.
- Liquidity zones are structural estimates.
- Sweep candidates do not prove institutional activity.
- This module never executes trades.
"""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any
from uuid import uuid4


class LiquiditySweepError(ValueError):
    """Raised when liquidity sweep input is invalid."""


def _number(
    value: Any,
    field: str,
    allow_zero: bool = False,
) -> float:
    if isinstance(value, bool):
        raise LiquiditySweepError(
            f"{field} must be numeric."
        )

    try:
        result = float(value)
    except (TypeError, ValueError):
        raise LiquiditySweepError(
            f"{field} must be numeric."
        ) from None

    if not isfinite(result):
        raise LiquiditySweepError(
            f"{field} must be finite."
        )

    if allow_zero:
        if result < 0:
            raise LiquiditySweepError(
                f"{field} cannot be negative."
            )
    elif result <= 0:
        raise LiquiditySweepError(
            f"{field} must be positive."
        )

    return result


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

        open_price = _number(
            candle.get("open"),
            f"candles[{index}].open",
        )

        high = _number(
            candle.get("high"),
            f"candles[{index}].high",
        )

        low = _number(
            candle.get("low"),
            f"candles[{index}].low",
        )

        close = _number(
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


def _validate_zone(
    zone: Any,
    field: str,
) -> dict[str, Any]:
    if not isinstance(zone, Mapping):
        raise LiquiditySweepError(
            f"{field} must be a mapping."
        )

    level = _number(
        zone.get("level"),
        f"{field}.level",
    )

    last_index = zone.get("last_index", -1)

    if (
        isinstance(last_index, bool)
        or not isinstance(last_index, int)
        or last_index < -1
    ):
        raise LiquiditySweepError(
            f"{field}.last_index is invalid."
        )

    zone_id = zone.get("zone_id")

    if zone_id is not None:
        if not isinstance(zone_id, str) or not zone_id.strip():
            raise LiquiditySweepError(
                f"{field}.zone_id is invalid."
            )

    return {
        **dict(zone),
        "level": level,
        "last_index": last_index,
    }


def _validate_zone_collections(
    zones: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(zones, Mapping):
        raise LiquiditySweepError(
            "zones must be a mapping."
        )

    buy_zones = zones.get("buy_side_zones", [])
    sell_zones = zones.get("sell_side_zones", [])

    if not isinstance(buy_zones, list):
        raise LiquiditySweepError(
            "buy_side_zones must be a list."
        )

    if not isinstance(sell_zones, list):
        raise LiquiditySweepError(
            "sell_side_zones must be a list."
        )

    validated_buy = [
        _validate_zone(zone, f"buy_side_zones[{index}]")
        for index, zone in enumerate(buy_zones)
    ]

    validated_sell = [
        _validate_zone(zone, f"sell_side_zones[{index}]")
        for index, zone in enumerate(sell_zones)
    ]

    return validated_buy, validated_sell


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
                "members": [(price, index)]
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
            item[0] for item in members
        ]

        indices = [
            item[1] for item in members
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
    data = _validate_candles(candles)

    tolerance = _number(
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
        "institutional_orders_verified": False,
    }


def detect_sweep_events(
    candles: Sequence[Mapping[str, Any]],
    zones: Mapping[str, Any],
    minimum_penetration: float = 0.0,
    rejection_threshold: float = 0.60,
) -> list[dict[str, Any]]:
    data = _validate_candles(candles)

    penetration = _number(
        minimum_penetration,
        "minimum_penetration",
        allow_zero=True,
    )

    threshold = _number(
        rejection_threshold,
        "rejection_threshold",
        allow_zero=True,
    )

    if threshold > 1:
        raise LiquiditySweepError(
            "rejection_threshold must be between 0 and 1."
        )

    buy_zones, sell_zones = _validate_zone_collections(
        zones
    )

    events = []

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
            if index <= zone["last_index"]:
                continue

            level = zone["level"]

            if candle["high"] <= level:
                continue

            penetration_size = (
                candle["high"] - level
            )

            if penetration_size < penetration:
                continue

            rejection = candle["close"] < level

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
                    rejection and strength >= threshold
                ),
                "close_position": close_position,
                "interpretation": (
                    "POTENTIAL_BUY_SIDE_SWEEP"
                    if rejection
                    else "BREAKOUT_ABOVE_BUY_SIDE_LIQUIDITY"
                ),
            })

        for zone in sell_zones:
            if index <= zone["last_index"]:
                continue

            level = zone["level"]

            if candle["low"] >= level:
                continue

            penetration_size = (
                level - candle["low"]
            )

            if penetration_size < penetration:
                continue

            rejection = candle["close"] > level

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
                    rejection and strength >= threshold
                ),
                "close_position": close_position,
                "interpretation": (
                    "POTENTIAL_SELL_SIDE_SWEEP"
                    if rejection
                    else "BREAKOUT_BELOW_SELL_SIDE_LIQUIDITY"
                ),
            })

    return events


def update_zone_statuses(
    zones: Mapping[str, Any],
    sweep_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    buy_zones, sell_zones = _validate_zone_collections(
        zones
    )

    if (
        isinstance(sweep_events, (str, bytes))
        or not isinstance(sweep_events, Sequence)
    ):
        raise LiquiditySweepError(
            "sweep_events must be a sequence."
        )

    event_map = {}

    for event_index, event in enumerate(sweep_events):
        if not isinstance(event, Mapping):
            raise LiquiditySweepError(
                f"sweep_events[{event_index}] must be a mapping."
            )

        zone_id = event.get("zone_id")

        if not isinstance(zone_id, str) or not zone_id.strip():
            raise LiquiditySweepError(
                f"sweep_events[{event_index}] has invalid zone_id."
            )

        index = event.get("index")

        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
        ):
            raise LiquiditySweepError(
                f"sweep_events[{event_index}] has invalid index."
            )

        if not isinstance(event.get("sweep_candidate"), bool):
            raise LiquiditySweepError(
                f"sweep_events[{event_index}] has invalid sweep_candidate."
            )

        event_map.setdefault(zone_id, []).append(dict(event))

    for collection in (buy_zones, sell_zones):
        for zone in collection:
            zone_id = zone.get("zone_id")

            if not zone_id:
                continue

            matching = event_map.get(zone_id, [])

            if not matching:
                continue

            latest = max(
                matching,
                key=lambda item: item["index"],
            )

            zone["status"] = (
                "SWEPT"
                if latest["sweep_candidate"]
                else "BROKEN"
            )

            zone["last_event_index"] = latest["index"]

    return {
        "buy_side_zones": buy_zones,
        "sell_side_zones": sell_zones,
        "zone_count": len(buy_zones) + len(sell_zones),
    }


def summarize_liquidity_activity(
    zones: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    buy_zones, sell_zones = _validate_zone_collections(
        zones
    )

    if (
        isinstance(events, (str, bytes))
        or not isinstance(events, Sequence)
    ):
        raise LiquiditySweepError(
            "events must be a sequence."
        )

    validated_events = []

    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise LiquiditySweepError(
                f"events[{index}] must be a mapping."
            )

        side = event.get("side")

        if side not in ("BUY_SIDE", "SELL_SIDE"):
            raise LiquiditySweepError(
                f"events[{index}] has invalid side."
            )

        if not isinstance(event.get("sweep_candidate"), bool):
            raise LiquiditySweepError(
                f"events[{index}] has invalid sweep_candidate."
            )

        if not isinstance(event.get("strong_rejection"), bool):
            raise LiquiditySweepError(
                f"events[{index}] has invalid strong_rejection."
            )

        validated_events.append(event)

    buy_sweeps = [
        event for event in validated_events
        if (
            event["side"] == "BUY_SIDE"
            and event["sweep_candidate"]
        )
    ]

    sell_sweeps = [
        event for event in validated_events
        if (
            event["side"] == "SELL_SIDE"
            and event["sweep_candidate"]
        )
    ]

    return {
        "total_zones": len(buy_zones) + len(sell_zones),
        "buy_side_zones": len(buy_zones),
        "sell_side_zones": len(sell_zones),
        "total_sweep_candidates": (
            len(buy_sweeps) + len(sell_sweeps)
        ),
        "buy_side_sweep_candidates": len(buy_sweeps),
        "sell_side_sweep_candidates": len(sell_sweeps),
        "strong_rejections": sum(
            1 for event in validated_events
            if event["strong_rejection"]
        ),
        "institutional_activity_confirmed": False,
        "execution_authorized": False,
    }
