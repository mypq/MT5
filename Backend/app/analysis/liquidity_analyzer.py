المسار: "Backend/app/analysis/liquidity_analyzer.py"

"""
GOLD BOT - Liquidity Analyzer

Identifies potential liquidity pools around:
- Equal highs
- Equal lows
- Confirmed swing highs and lows
- Potential liquidity sweeps

Input candles must be chronological: oldest to newest.
Only closed candles should be supplied.

This module performs analysis only.
It never generates or executes trading orders.
"""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any


MINIMUM_CANDLES = 30
SWING_STRENGTH = 3

# Initial tolerance: 0.10% of the average compared price.
# This is price-relative, not a broker tick-size threshold.
EQUAL_LEVEL_TOLERANCE = 0.001


class LiquidityAnalysisError(ValueError):
    """Raised when liquidity analysis data is invalid."""


def _read_price(candle: Mapping[str, Any], field: str) -> float:
    value = candle.get(field)

    if isinstance(value, bool):
        raise LiquidityAnalysisError(f"Invalid {field} value.")

    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LiquidityAnalysisError(
            f"Missing or invalid {field}."
        ) from exc

    if not isfinite(price) or price <= 0:
        raise LiquidityAnalysisError(
            f"{field} must be finite and positive."
        )

    return price


def _validate_candles(
    candles: Sequence[Mapping[str, Any]],
) -> list[dict[str, float]]:
    if not isinstance(candles, Sequence) or isinstance(
        candles, (str, bytes)
    ):
        raise LiquidityAnalysisError("Invalid candle collection.")

    if len(candles) < MINIMUM_CANDLES:
        raise LiquidityAnalysisError(
            f"At least {MINIMUM_CANDLES} candles are required."
        )

    validated = []

    for index, candle in enumerate(candles):
        if not isinstance(candle, Mapping):
            raise LiquidityAnalysisError(
                f"Invalid candle at index {index}."
            )

        open_price = _read_price(candle, "open")
        high = _read_price(candle, "high")
        low = _read_price(candle, "low")
        close = _read_price(candle, "close")

        if high < low:
            raise LiquidityAnalysisError(
                f"High cannot be below low at index {index}."
            )

        if high < max(open_price, close):
            raise LiquidityAnalysisError(
                f"Invalid candle high at index {index}."
            )

        if low > min(open_price, close):
            raise LiquidityAnalysisError(
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
        raise LiquidityAnalysisError(
            "Swing strength must be an integer."
        )

    if strength < 1:
        raise LiquidityAnalysisError(
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

        confirmation_index = index + strength

        if is_swing_high:
            swings.append({
                "type": "HIGH",
                "index": index,
                "price": current["high"],
                "confirmation_index": confirmation_index,
                "confirmed": True,
            })

        if is_swing_low:
            swings.append({
                "type": "LOW",
                "index": index,
                "price": current["low"],
                "confirmation_index": confirmation_index,
                "confirmed": True,
            })

    return sorted(
        swings,
        key=lambda item: (item["index"], item["type"]),
    )


def _is_equal_price(first: float, second: float) -> bool:
    average = (first + second) / 2.0

    if average <= 0 or not isfinite(average):
        return False

    return (
        abs(first - second) / average
        <= EQUAL_LEVEL_TOLERANCE
    )


def _find_equal_level_pools(
    swings: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    pools = []

    for first_index in range(len(swings)):
        first = swings[first_index]

        for second_index in range(first_index + 1, len(swings)):
            second = swings[second_index]

            if first["type"] != second["type"]:
                continue

            if not _is_equal_price(
                first["price"],
                second["price"],
            ):
                continue

            average_price = (
                first["price"] + second["price"]
            ) / 2.0

            pools.append({
                "pool_type": (
                    "BUY_SIDE_LIQUIDITY"
                    if first["type"] == "HIGH"
                    else "SELL_SIDE_LIQUIDITY"
                ),
                "level_type": first["type"],
                "price": average_price,
                "touch_indices": [
                    first["index"],
                    second["index"],
                ],
                "touch_count": 2,
                "confirmation_index": max(
                    first["confirmation_index"],
                    second["confirmation_index"],
                ),
                "status": "POTENTIAL",
            })

    return pools


def _detect_sweeps(
    candles: Sequence[dict[str, float]],
    swings: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    sweeps = []

    for swing in swings:
        level = swing["price"]

        # Do not evaluate a sweep before the swing is confirmed.
        first_eligible_index = (
            swing["confirmation_index"] + 1
        )

        for index in range(first_eligible_index, len(candles)):
            candle = candles[index]

            if swing["type"] == "HIGH":
                swept = (
                    candle["high"] > level
                    and candle["close"] < level
                )
                direction = "BUY_SIDE_LIQUIDITY_SWEPT"
                extreme = candle["high"]

            else:
                swept = (
                    candle["low"] < level
                    and candle["close"] > level
                )
                direction = "SELL_SIDE_LIQUIDITY_SWEPT"
                extreme = candle["low"]

            if swept:
                sweeps.append({
                    "event": "POTENTIAL_LIQUIDITY_SWEEP",
                    "direction": direction,
                    "level_price": level,
                    "swing_index": swing["index"],
                    "confirmation_index": swing["confirmation_index"],
                    "sweep_index": index,
                    "sweep_extreme": extreme,
                    "close": candle["close"],
                })

                # Record only the first sweep of this swing level.
                break

    return sorted(
        sweeps,
        key=lambda item: (
            item["sweep_index"],
            item["swing_index"],
        ),
    )


def analyze_liquidity(
    candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    data = _validate_candles(candles)

    swings = _find_swings(data)

    pools = _find_equal_level_pools(swings)
    sweeps = _detect_sweeps(data, swings)

    latest_close = data[-1]["close"]

    for pool in pools:
        pool["distance_from_current_price"] = abs(
            pool["price"] - latest_close
        )

        pool["relative_distance_percent"] = (
            abs(pool["price"] - latest_close)
            / latest_close
        ) * 100.0

    pools.sort(
        key=lambda item: (
            item["distance_from_current_price"],
            -item["touch_count"],
        )
    )

    confirmed_pool_count = sum(
        1
        for pool in pools
        if pool["confirmation_index"] < len(data)
    )

    return {
        "module": "liquidity",
        "data_available": True,
        "candles_analyzed": len(data),
        "swing_count": len(swings),
        "liquidity_pools": pools,
        "potential_sweeps": sweeps,
        "nearest_liquidity_pools": pools[:5],
        "confirmed_pool_count": confirmed_pool_count,
        "latest_sweep": sweeps[-1] if sweeps else None,
        "parameters": {
            "swing_strength": SWING_STRENGTH,
            "equal_level_tolerance_percent": (
                EQUAL_LEVEL_TOLERANCE * 100
            ),
        },
        "limitations": [
            "Price levels are potential liquidity areas, not confirmed orders.",
            "Equal highs and lows do not prove resting stop orders exist.",
            "A wick sweep does not guarantee a reversal.",
            "Swing points require future candles for confirmation.",
            "Sweeps are evaluated only after swing confirmation.",
            "Equal-level pools are pair-based and may overlap.",
            "This module does not generate trading orders.",
        ],
    }
