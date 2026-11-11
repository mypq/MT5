المسار: "Backend/app/analysis/supply_demand_analyzer.py"

"""
GOLD BOT - Supply and Demand Analyzer

Detects potential supply and demand zones using:
- Base candle sequences
- Strong directional departures
- Zone boundaries
- Retests
- Zone invalidation

Input candles must be chronological: oldest to newest.
Only closed candles should be supplied.

This module performs analysis only.
It never generates or executes trading orders.
"""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any


MINIMUM_CANDLES = 30

BASE_MAX_CANDLES = 3
DEPARTURE_LOOKAHEAD = 4

ATR_PERIOD = 14
MINIMUM_DEPARTURE_ATR_MULTIPLE = 1.5

MAXIMUM_RETESTS_FOR_FRESH_ZONE = 0


class SupplyDemandAnalysisError(ValueError):
    """Raised when supply/demand data is invalid."""


def _read_price(candle: Mapping[str, Any], field: str) -> float:
    value = candle.get(field)

    if isinstance(value, bool):
        raise SupplyDemandAnalysisError(
            f"Invalid {field} value."
        )

    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SupplyDemandAnalysisError(
            f"Missing or invalid {field}."
        ) from exc

    if not isfinite(price) or price <= 0:
        raise SupplyDemandAnalysisError(
            f"{field} must be finite and positive."
        )

    return price


def _validate_candles(
    candles: Sequence[Mapping[str, Any]],
) -> list[dict[str, float]]:
    if not isinstance(candles, Sequence) or isinstance(
        candles, (str, bytes)
    ):
        raise SupplyDemandAnalysisError(
            "Invalid candle collection."
        )

    if len(candles) < MINIMUM_CANDLES:
        raise SupplyDemandAnalysisError(
            f"At least {MINIMUM_CANDLES} candles are required."
        )

    validated = []

    for index, candle in enumerate(candles):
        if not isinstance(candle, Mapping):
            raise SupplyDemandAnalysisError(
                f"Invalid candle at index {index}."
            )

        open_price = _read_price(candle, "open")
        high = _read_price(candle, "high")
        low = _read_price(candle, "low")
        close = _read_price(candle, "close")

        if high < low:
            raise SupplyDemandAnalysisError(
                f"High cannot be below low at index {index}."
            )

        if high < max(open_price, close):
            raise SupplyDemandAnalysisError(
                f"Invalid candle high at index {index}."
            )

        if low > min(open_price, close):
            raise SupplyDemandAnalysisError(
                f"Invalid candle low at index {index}."
            )

        validated.append({
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
        })

    return validated


def _calculate_atr(
    candles: Sequence[dict[str, float]],
    period: int = ATR_PERIOD,
) -> list[float]:
    if (
        isinstance(period, bool)
        or not isinstance(period, int)
        or period < 1
    ):
        raise SupplyDemandAnalysisError(
            "ATR period must be a positive integer."
        )

    if len(candles) < period:
        raise SupplyDemandAnalysisError(
            "Not enough candles for ATR calculation."
        )

    true_ranges = []

    for index, candle in enumerate(candles):
        if index == 0:
            true_range = (
                candle["high"] - candle["low"]
            )
        else:
            previous_close = candles[index - 1]["close"]

            true_range = max(
                candle["high"] - candle["low"],
                abs(candle["high"] - previous_close),
                abs(candle["low"] - previous_close),
            )

        true_ranges.append(true_range)

    current_atr = sum(
        true_ranges[:period]
    ) / period

    atr_values = [current_atr] * (period - 1)
    atr_values.append(current_atr)

    for value in true_ranges[period:]:
        current_atr = (
            current_atr * (period - 1) + value
        ) / period

        atr_values.append(current_atr)

    return atr_values


def _is_base_candle(
    candle: dict[str, float],
    atr: float,
) -> bool:
    if not isfinite(atr) or atr <= 0:
        return False

    candle_range = candle["high"] - candle["low"]

    return candle_range <= atr * 0.8


def _find_bases(
    candles: Sequence[dict[str, float]],
    atr_values: Sequence[float],
) -> list[dict[str, Any]]:
    bases = []
    index = ATR_PERIOD

    while index < len(candles) - 1:
        if not _is_base_candle(
            candles[index],
            atr_values[index],
        ):
            index += 1
            continue

        start = index
        end = index

        while (
            end + 1 < len(candles) - 1
            and end - start + 1 < BASE_MAX_CANDLES
            and _is_base_candle(
                candles[end + 1],
                atr_values[end + 1],
            )
        ):
            end += 1

        bases.append({
            "start_index": start,
            "end_index": end,
            "high": max(
                candles[item]["high"]
                for item in range(start, end + 1)
            ),
            "low": min(
                candles[item]["low"]
                for item in range(start, end + 1)
            ),
        })

        index = end + 1

    return bases


def _find_departure(
    candles: Sequence[dict[str, float]],
    base: dict[str, Any],
    atr_values: Sequence[float],
) -> dict[str, Any] | None:
    departure_start = base["end_index"] + 1

    departure_end = min(
        departure_start + DEPARTURE_LOOKAHEAD,
        len(candles),
    )

    if departure_start >= len(candles):
        return None

    reference_atr = atr_values[base["end_index"]]

    if reference_atr <= 0:
        return None

    best_candidate = None

    for index in range(departure_start, departure_end):
        candle = candles[index]

        if candle["close"] > base["high"]:
            direction = "DEMAND"
            distance = candle["close"] - base["high"]

        elif candle["close"] < base["low"]:
            direction = "SUPPLY"
            distance = base["low"] - candle["close"]

        else:
            continue

        strength = distance / reference_atr

        if strength < MINIMUM_DEPARTURE_ATR_MULTIPLE:
            continue

        candidate = {
            "direction": direction,
            "departure_index": index,
            "departure_strength_atr": strength,
        }

        if (
            best_candidate is None
            or candidate["departure_strength_atr"]
            > best_candidate["departure_strength_atr"]
        ):
            best_candidate = candidate

    return best_candidate


def _evaluate_zone(
    candles: Sequence[dict[str, float]],
    base: dict[str, Any],
    departure: dict[str, Any],
) -> dict[str, Any]:
    direction = departure["direction"]

    zone = {
        "zone_type": direction,
        "zone_high": base["high"],
        "zone_low": base["low"],
        "base_start_index": base["start_index"],
        "base_end_index": base["end_index"],
        "departure_index": departure["departure_index"],
        "departure_strength_atr": departure[
            "departure_strength_atr"
        ],
        "retest_count": 0,
        "status": "FRESH",
        "invalidation_index": None,
    }

    was_inside_zone = False

    for index in range(
        departure["departure_index"] + 1,
        len(candles),
    ):
        candle = candles[index]

        intersects_zone = (
            candle["low"] <= zone["zone_high"]
            and candle["high"] >= zone["zone_low"]
        )

        if direction == "DEMAND":
            invalidated = (
                candle["close"] < zone["zone_low"]
            )
        else:
            invalidated = (
                candle["close"] > zone["zone_high"]
            )

        if invalidated:
            zone["status"] = "INVALIDATED"
            zone["invalidation_index"] = index
            break

        # Count a retest only when price enters the zone
        # after being outside it.
        if intersects_zone and not was_inside_zone:
            zone["retest_count"] += 1

            if (
                zone["retest_count"]
                > MAXIMUM_RETESTS_FOR_FRESH_ZONE
            ):
                zone["status"] = "TESTED"

        was_inside_zone = intersects_zone

    return zone


def analyze_supply_demand(
    candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    data = _validate_candles(candles)

    atr_values = _calculate_atr(data)

    bases = _find_bases(data, atr_values)

    zones = []

    for base in bases:
        departure = _find_departure(
            data,
            base,
            atr_values,
        )

        if departure is None:
            continue

        zone = _evaluate_zone(
            data,
            base,
            departure,
        )

        zones.append(zone)

    latest_close = data[-1]["close"]

    for zone in zones:
        midpoint = (
            zone["zone_high"] + zone["zone_low"]
        ) / 2.0

        distance = abs(latest_close - midpoint)

        zone["distance_from_price"] = distance

        zone["relative_distance_percent"] = (
            distance / latest_close
        ) * 100.0

    active_zones = [
        zone
        for zone in zones
        if zone["status"] != "INVALIDATED"
    ]

    active_zones.sort(
        key=lambda zone: zone["distance_from_price"]
    )

    return {
        "module": "supply_demand",
        "data_available": True,
        "candles_analyzed": len(data),
        "detected_base_count": len(bases),
        "detected_zone_count": len(zones),
        "zones": zones,
        "active_zones": active_zones,
        "nearest_zones": active_zones[:5],
        "parameters": {
            "base_max_candles": BASE_MAX_CANDLES,
            "departure_lookahead": DEPARTURE_LOOKAHEAD,
            "minimum_departure_atr_multiple": (
                MINIMUM_DEPARTURE_ATR_MULTIPLE
            ),
            "atr_period": ATR_PERIOD,
        },
        "limitations": [
            "Zones are algorithmic estimates, not verified institutional orders.",
            "A strong departure does not prove institutional activity.",
            "Zone quality depends on timeframe and market conditions.",
            "Retests count transitions into a zone, not every candle inside it.",
            "Fixed thresholds require historical validation.",
            "This module does not generate trading orders.",
        ],
    }
