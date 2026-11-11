"""
GOLD BOT - Volume and Order Flow Analyzer

Analyzes:

- Tick volume
- Real volume when supplied
- Relative volume
- Abnormal volume
- Volume and price relationship
- Estimated directional candle pressure

Important:
Tick volume is not the same as traded contract volume.
Candle-based pressure is an estimate, not true order flow.

Input candles must be chronological: oldest to newest.
Only closed candles should be supplied.

This module performs analysis only.
It never generates or executes trading orders.
"""

from collections.abc import Mapping, Sequence
from math import isfinite
from statistics import mean
from typing import Any

MINIMUM_CANDLES = 30
VOLUME_AVERAGE_PERIOD = 20

RELATIVE_VOLUME_HIGH = 1.5
RELATIVE_VOLUME_LOW = 0.7

ABNORMAL_VOLUME_ZSCORE = 2.5

class VolumeAnalysisError(ValueError):
"""Raised when volume or candle data is invalid."""

def _read_positive_number(
candle: Mapping[str, Any],
field: str,
allow_zero: bool = False,
) -> float:

value = candle.get(field)

if isinstance(value, bool):
    raise VolumeAnalysisError(f"Invalid {field} value.")

try:
    result = float(value)
except (TypeError, ValueError):
    raise VolumeAnalysisError(
        f"Missing or invalid {field}."
    )

if not isfinite(result):
    raise VolumeAnalysisError(
        f"{field} must be finite."
    )

if allow_zero:
    if result < 0:
        raise VolumeAnalysisError(
            f"{field} cannot be negative."
        )
elif result <= 0:
    raise VolumeAnalysisError(
        f"{field} must be positive."
    )

return result

def _validate_candles(
candles: Sequence[Mapping[str, Any]],
) -> list[dict[str, float]]:

if isinstance(candles, (str, bytes)):
    raise VolumeAnalysisError("Invalid candle collection.")

if len(candles) < MINIMUM_CANDLES:
    raise VolumeAnalysisError(
        f"At least {MINIMUM_CANDLES} candles are required."
    )

validated = []

for index, candle in enumerate(candles):
    if not isinstance(candle, Mapping):
        raise VolumeAnalysisError(
            f"Invalid candle at index {index}."
        )

    open_price = _read_positive_number(candle, "open")
    high = _read_positive_number(candle, "high")
    low = _read_positive_number(candle, "low")
    close = _read_positive_number(candle, "close")

    tick_volume = _read_positive_number(
        candle,
        "tick_volume",
        allow_zero=True,
    )

    real_volume_value = candle.get("real_volume")
    real_volume = None

    if real_volume_value is not None:
        real_volume = _read_positive_number(
            candle,
            "real_volume",
            allow_zero=True,
        )

    if high < low:
        raise VolumeAnalysisError("High cannot be below low.")

    if high < max(open_price, close):
        raise VolumeAnalysisError("Invalid candle high.")

    if low > min(open_price, close):
        raise VolumeAnalysisError("Invalid candle low.")

    validated.append({
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": tick_volume,
        "real_volume": real_volume,
    })

return validated

def _volume_statistics(
volumes: Sequence[float],
) -> dict[str, float]:

if len(volumes) < VOLUME_AVERAGE_PERIOD + 1:
    raise VolumeAnalysisError(
        "Insufficient volume history."
    )

historical = list(volumes[-VOLUME_AVERAGE_PERIOD - 1:-1])
current = volumes[-1]

average_volume = mean(historical)

if average_volume <= 0:
    relative_volume = 0.0
else:
    relative_volume = current / average_volume

variance = mean(
    (value - average_volume) ** 2
    for value in historical
)

standard_deviation = variance ** 0.5

if standard_deviation == 0:
    z_score = 0.0
else:
    z_score = (
        current - average_volume
    ) / standard_deviation

if relative_volume >= RELATIVE_VOLUME_HIGH:
    classification = "HIGH"
elif relative_volume <= RELATIVE_VOLUME_LOW:
    classification = "LOW"
else:
    classification = "NORMAL"

return {
    "current": current,
    "average": average_volume,
    "relative_volume": relative_volume,
    "z_score": z_score,
    "classification": classification,
}

def _analyze_candle_pressure(
candle: dict[str, float],
) -> dict[str, Any]:

candle_range = candle["high"] - candle["low"]

if candle_range <= 0:
    close_location = 0.5
else:
    close_location = (
        candle["close"] - candle["low"]
    ) / candle_range

if candle["close"] > candle["open"]:
    candle_direction = "BULLISH"
elif candle["close"] < candle["open"]:
    candle_direction = "BEARISH"
else:
    candle_direction = "NEUTRAL"

if close_location >= 0.7:
    close_pressure = "UPPER_RANGE"
elif close_location <= 0.3:
    close_pressure = "LOWER_RANGE"
else:
    close_pressure = "MIDDLE_RANGE"

estimated_buy_pressure = close_location * 100.0
estimated_sell_pressure = (
    100.0 - estimated_buy_pressure
)

return {
    "candle_direction": candle_direction,
    "close_location_percent": close_location * 100.0,
    "close_pressure": close_pressure,
    "estimated_buy_pressure_percent": estimated_buy_pressure,
    "estimated_sell_pressure_percent": estimated_sell_pressure,
    "interpretation": (
        "CANDLE_BASED_ESTIMATE_NOT_TRUE_ORDER_FLOW"
    ),
}

def _analyze_volume_price_relationship(
candles: Sequence[dict[str, float]],
statistics: dict[str, float],
) -> list[str]:

observations = []

current = candles[-1]
previous = candles[-2]

price_change = current["close"] - previous["close"]

if statistics["classification"] == "HIGH":
    if price_change > 0:
        observations.append(
            "HIGH_VOLUME_WITH_RISING_PRICE"
        )
    elif price_change < 0:
        observations.append(
            "HIGH_VOLUME_WITH_FALLING_PRICE"
        )
    else:
        observations.append(
            "HIGH_VOLUME_WITH_FLAT_PRICE"
        )

elif statistics["classification"] == "LOW":
    observations.append("LOW_ACTIVITY_RELATIVE_TO_RECENT_HISTORY")

if (
    statistics["z_score"] >= ABNORMAL_VOLUME_ZSCORE
):
    observations.append("STATISTICALLY_ABNORMAL_VOLUME")

if (
    current["close"] > current["open"]
    and current["tick_volume"] > previous["tick_volume"]
):
    observations.append(
        "BULLISH_CANDLE_WITH_INCREASING_TICK_VOLUME"
    )

elif (
    current["close"] < current["open"]
    and current["tick_volume"] > previous["tick_volume"]
):
    observations.append(
        "BEARISH_CANDLE_WITH_INCREASING_TICK_VOLUME"
    )

return observations

def analyze_volume(
candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:

data = _validate_candles(candles)

tick_volumes = [
    candle["tick_volume"]
    for candle in data
]

tick_statistics = _volume_statistics(tick_volumes)

real_volumes = [
    candle["real_volume"]
    for candle in data
]

real_volume_available = all(
    value is not None
    for value in real_volumes
)

real_statistics = None

if real_volume_available:
    real_statistics = _volume_statistics(
        real_volumes
    )

pressure = _analyze_candle_pressure(data[-1])

observations = _analyze_volume_price_relationship(
    data,
    tick_statistics,
)

return {
    "module": "volume_order_flow",
    "data_available": True,
    "candles_analyzed": len(data),
    "tick_volume": tick_statistics,
    "real_volume_available": real_volume_available,
    "real_volume": real_statistics,
    "estimated_candle_pressure": pressure,
    "observations": observations,
    "parameters": {
        "average_period": VOLUME_AVERAGE_PERIOD,
        "relative_volume_high": RELATIVE_VOLUME_HIGH,
        "relative_volume_low": RELATIVE_VOLUME_LOW,
        "abnormal_volume_zscore": ABNORMAL_VOLUME_ZSCORE,
    },
    "limitations": [
        "Tick volume counts price updates and is not traded contract volume.",
        "Real volume depends on broker and instrument data availability.",
        "Candle pressure is an estimate and cannot identify actual buyer and seller initiated trades.",
        "Volume anomalies require contextual interpretation.",
        "This module does not generate trading orders.",
    ],
}
