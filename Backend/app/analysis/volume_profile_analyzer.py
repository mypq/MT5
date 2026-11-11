"""
GOLD BOT - Volume Profile Intelligence

Builds an approximate price-volume distribution from OHLCV candles.

Calculates:
- Point of Control (POC)
- Value Area High (VAH)
- Value Area Low (VAL)
- High Volume Nodes (HVN)
- Low Volume Nodes (LVN)
- Volume concentration
- Previous versus current value-area migration

IMPORTANT:
Candle volume is allocated uniformly across the candle's price range.
This is an approximation, not true exchange trade-by-price data.
This module does not fetch data or execute trades.
"""

from collections.abc import Mapping, Sequence
from math import floor, isfinite
from typing import Any


SUPPORTED_SYMBOL = "XAUUSD"

DEFAULT_BIN_COUNT = 100
VALUE_AREA_PERCENT = 70.0

MINIMUM_OBSERVATIONS = 20
MAXIMUM_OBSERVATIONS = 100_000
MINIMUM_TOTAL_VOLUME = 1e-12

NODE_WINDOW = 2
HVN_MINIMUM_RATIO = 1.50
LVN_MAXIMUM_RATIO = 0.50

ALLOWED_VOLUME_SOURCES = {
    "UNKNOWN",
    "TICK",
    "BROKER",
    "EXCHANGE",
}


class VolumeProfileAnalysisError(ValueError):
    """Raised when volume profile input data is invalid."""


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise VolumeProfileAnalysisError(
            f"{field} must be numeric."
        )

    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VolumeProfileAnalysisError(
            f"{field} must be numeric."
        ) from exc

    if not isfinite(result):
        raise VolumeProfileAnalysisError(
            f"{field} must be finite."
        )

    return result


def _validate_observations(
    observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, float]]:

    if (
        not isinstance(observations, Sequence)
        or isinstance(observations, (str, bytes))
    ):
        raise VolumeProfileAnalysisError(
            "Observations must be a sequence."
        )

    if not MINIMUM_OBSERVATIONS <= len(observations) <= MAXIMUM_OBSERVATIONS:
        raise VolumeProfileAnalysisError(
            "Observation count is outside the supported range."
        )

    validated = []

    for index, observation in enumerate(observations):
        if not isinstance(observation, Mapping):
            raise VolumeProfileAnalysisError(
                f"Invalid observation at index {index}."
            )

        low = _number(
            observation.get("low"),
            f"observations[{index}].low",
        )

        high = _number(
            observation.get("high"),
            f"observations[{index}].high",
        )

        close = _number(
            observation.get("close"),
            f"observations[{index}].close",
        )

        volume = _number(
            observation.get("volume"),
            f"observations[{index}].volume",
        )

        if low <= 0 or high <= 0 or close <= 0:
            raise VolumeProfileAnalysisError(
                f"Prices must be positive at index {index}."
            )

        if high < low:
            raise VolumeProfileAnalysisError(
                f"High must be greater than or equal to low at index {index}."
            )

        if not low <= close <= high:
            raise VolumeProfileAnalysisError(
                f"Close must be inside the candle range at index {index}."
            )

        if volume < 0:
            raise VolumeProfileAnalysisError(
                f"Volume cannot be negative at index {index}."
            )

        validated.append({
            "low": low,
            "high": high,
            "close": close,
            "volume": volume,
        })

    total_volume = sum(
        item["volume"] for item in validated
    )

    if (
        not isfinite(total_volume)
        or total_volume <= MINIMUM_TOTAL_VOLUME
    ):
        raise VolumeProfileAnalysisError(
            "Total volume must be finite and greater than zero."
        )

    return validated


def _build_profile(
    observations: Sequence[Mapping[str, float]],
    bin_count: int,
) -> dict[str, Any]:

    minimum_price = min(
        item["low"] for item in observations
    )

    maximum_price = max(
        item["high"] for item in observations
    )

    if maximum_price <= minimum_price:
        raise VolumeProfileAnalysisError(
            "Price range must be greater than zero."
        )

    bin_width = (
        maximum_price - minimum_price
    ) / bin_count

    bins = [
        {
            "index": index,
            "price_low": minimum_price + index * bin_width,
            "price_high": minimum_price + (index + 1) * bin_width,
            "volume": 0.0,
        }
        for index in range(bin_count)
    ]

    for observation in observations:
        candle_low = observation["low"]
        candle_high = observation["high"]
        candle_volume = observation["volume"]

        first_index = max(
            0,
            min(
                bin_count - 1,
                floor((candle_low - minimum_price) / bin_width),
            ),
        )

        last_index = max(
            0,
            min(
                bin_count - 1,
                floor((candle_high - minimum_price) / bin_width),
            ),
        )

        touched_bins = []

        for index in range(first_index, last_index + 1):
            profile_bin = bins[index]

            overlap = max(
                0.0,
                min(candle_high, profile_bin["price_high"])
                - max(candle_low, profile_bin["price_low"]),
            )

            if overlap > 0:
                touched_bins.append({
                    "index": index,
                    "weight": overlap,
                })

        total_weight = sum(
            item["weight"] for item in touched_bins
        )

        if total_weight <= 0:
            nearest_index = max(
                0,
                min(
                    bin_count - 1,
                    floor(
                        (observation["close"] - minimum_price)
                        / bin_width
                    ),
                ),
            )

            bins[nearest_index]["volume"] += candle_volume
            continue

        for item in touched_bins:
            bins[item["index"]]["volume"] += (
                candle_volume
                * item["weight"]
                / total_weight
            )

    expected_volume = sum(
        item["volume"] for item in observations
    )

    allocated_volume = sum(
        item["volume"] for item in bins
    )

    tolerance = max(1e-9, expected_volume * 1e-9)

    if abs(expected_volume - allocated_volume) > tolerance:
        raise VolumeProfileAnalysisError(
            "Volume allocation conservation check failed."
        )

    return {
        "bins": bins,
        "bin_width": bin_width,
        "minimum_price": minimum_price,
        "maximum_price": maximum_price,
        "source_volume": expected_volume,
        "allocated_volume": allocated_volume,
    }


def _calculate_value_area(
    bins: Sequence[Mapping[str, float]],
    value_area_percent: float,
) -> dict[str, Any]:

    total_volume = sum(
        item["volume"] for item in bins
    )

    if total_volume <= MINIMUM_TOTAL_VOLUME:
        raise VolumeProfileAnalysisError(
            "Cannot calculate value area without volume."
        )

    poc_index = max(
        range(len(bins)),
        key=lambda index: bins[index]["volume"],
    )

    target_volume = (
        total_volume * value_area_percent / 100.0
    )

    selected_indices = {poc_index}
    accumulated_volume = bins[poc_index]["volume"]

    lower_index = poc_index
    upper_index = poc_index

    while accumulated_volume < target_volume:
        lower_candidate = lower_index - 1
        upper_candidate = upper_index + 1

        lower_volume = (
            bins[lower_candidate]["volume"]
            if lower_candidate >= 0
            else -1.0
        )

        upper_volume = (
            bins[upper_candidate]["volume"]
            if upper_candidate < len(bins)
            else -1.0
        )

        if lower_volume < 0 and upper_volume < 0:
            break

        if upper_volume > lower_volume:
            upper_index = upper_candidate
            selected_indices.add(upper_index)
            accumulated_volume += upper_volume
        else:
            lower_index = lower_candidate
            selected_indices.add(lower_index)
            accumulated_volume += lower_volume

    return {
        "poc_index": poc_index,
        "poc": (
            bins[poc_index]["price_low"]
            + bins[poc_index]["price_high"]
        ) / 2.0,
        "vah": bins[upper_index]["price_high"],
        "val": bins[lower_index]["price_low"],
        "accumulated_volume": accumulated_volume,
        "actual_value_area_percent": (
            accumulated_volume / total_volume
        ) * 100.0,
        "selected_indices": sorted(selected_indices),
    }


def _detect_nodes(
    bins: Sequence[Mapping[str, float]],
) -> dict[str, list[dict[str, float]]]:

    average_volume = (
        sum(item["volume"] for item in bins)
        / len(bins)
    )

    hvn = []
    lvn = []

    for index, item in enumerate(bins):
        start = max(0, index - NODE_WINDOW)
        end = min(len(bins), index + NODE_WINDOW + 1)

        neighboring_volumes = [
            bins[position]["volume"]
            for position in range(start, end)
            if position != index
        ]

        if not neighboring_volumes:
            continue

        neighborhood_average = (
            sum(neighboring_volumes)
            / len(neighboring_volumes)
        )

        midpoint = (
            item["price_low"] + item["price_high"]
        ) / 2.0

        if (
            neighborhood_average > 0
            and item["volume"] >= neighborhood_average * HVN_MINIMUM_RATIO
            and item["volume"] > average_volume
        ):
            hvn.append({
                "price": midpoint,
                "volume": item["volume"],
            })

        elif (
            neighborhood_average > 0
            and item["volume"] <= neighborhood_average * LVN_MAXIMUM_RATIO
            and item["volume"] < average_volume
        ):
            lvn.append({
                "price": midpoint,
                "volume": item["volume"],
            })

    return {
        "hvn": hvn,
        "lvn": lvn,
    }


def _analyze_one_profile(
    observations: Sequence[Mapping[str, float]],
    bin_count: int,
    value_area_percent: float,
) -> dict[str, Any]:

    profile = _build_profile(
        observations,
        bin_count,
    )

    bins = profile["bins"]

    value_area = _calculate_value_area(
        bins,
        value_area_percent,
    )

    nodes = _detect_nodes(bins)

    return {
        "poc": value_area["poc"],
        "vah": value_area["vah"],
        "val": value_area["val"],
        "total_volume": profile["allocated_volume"],
        "source_volume": profile["source_volume"],
        "bin_width": profile["bin_width"],
        "actual_value_area_percent": (
            value_area["actual_value_area_percent"]
        ),
        "hvn": nodes["hvn"],
        "lvn": nodes["lvn"],
        "distribution": [
            {
                "price_low": item["price_low"],
                "price_high": item["price_high"],
                "volume": item["volume"],
            }
            for item in bins
        ],
    }


def _classify_value_area_migration(
    current: Mapping[str, float],
    previous: Mapping[str, float],
) -> str:

    current_val = current["val"]
    current_vah = current["vah"]

    previous_val = previous["val"]
    previous_vah = previous["vah"]

    if current_val > previous_vah:
        return "HIGHER"

    if current_vah < previous_val:
        return "LOWER"

    if (
        current_val > previous_val
        and current_vah > previous_vah
    ):
        return "SHIFTING_HIGHER"

    if (
        current_val < previous_val
        and current_vah < previous_vah
    ):
        return "SHIFTING_LOWER"

    return "OVERLAPPING_OR_MIXED"


def analyze_volume_profile(
    observations: Sequence[Mapping[str, Any]],
    bin_count: int = DEFAULT_BIN_COUNT,
    value_area_percent: float = VALUE_AREA_PERCENT,
    previous_observations: Sequence[Mapping[str, Any]] | None = None,
    volume_source: str = "UNKNOWN",
) -> dict[str, Any]:

    if (
        isinstance(bin_count, bool)
        or not isinstance(bin_count, int)
        or not 10 <= bin_count <= 500
    ):
        raise VolumeProfileAnalysisError(
            "bin_count must be an integer between 10 and 500."
        )

    if (
        isinstance(value_area_percent, bool)
        or not isinstance(value_area_percent, (int, float))
        or not isfinite(value_area_percent)
        or not 50 <= value_area_percent <= 90
    ):
        raise VolumeProfileAnalysisError(
            "value_area_percent must be between 50 and 90."
        )

    if (
        not isinstance(volume_source, str)
        or volume_source.strip().upper() not in ALLOWED_VOLUME_SOURCES
    ):
        raise VolumeProfileAnalysisError(
            "volume_source must be UNKNOWN, TICK, BROKER, or EXCHANGE."
        )

    current_data = _validate_observations(observations)

    current_profile = _analyze_one_profile(
        current_data,
        bin_count,
        float(value_area_percent),
    )

    previous_profile = None

    if previous_observations is not None:
        previous_data = _validate_observations(
            previous_observations
        )

        previous_profile = _analyze_one_profile(
            previous_data,
            bin_count,
            float(value_area_percent),
        )

    migration = None

    if previous_profile is not None:
        migration = {
            "poc_change": (
                current_profile["poc"]
                - previous_profile["poc"]
            ),
            "vah_change": (
                current_profile["vah"]
                - previous_profile["vah"]
            ),
            "val_change": (
                current_profile["val"]
                - previous_profile["val"]
            ),
            "value_area_migration": (
                _classify_value_area_migration(
                    current_profile,
                    previous_profile,
                )
            ),
        }

    normalized_source = volume_source.strip().upper()

    return {
        "module": "volume_profile_intelligence",
        "symbol": SUPPORTED_SYMBOL,
        "data_available": True,
        "volume_source": normalized_source,
        "volume_interpretation": (
            "Exchange-provided volume; source authenticity must be verified upstream."
            if normalized_source == "EXCHANGE"
            else "Tick volume or broker-provided volume; not verified exchange volume."
        ),
        "settings": {
            "bin_count": bin_count,
            "value_area_percent_target": float(value_area_percent),
        },
        "current_profile": current_profile,
        "previous_profile": previous_profile,
        "migration": migration,
        "limitations": [
            "Volume is allocated uniformly by candle price-range overlap.",
            "This is not true trade-by-price volume.",
            "Exchange volume requires a verified exchange data provider.",
            "HVN and LVN are statistical nodes, not guaranteed support or resistance.",
            "Profile results depend on observation period and bin count.",
            "The input candles must represent the intended profile period.",
            "This module does not fetch data or execute trading orders.",
        ],
    }
