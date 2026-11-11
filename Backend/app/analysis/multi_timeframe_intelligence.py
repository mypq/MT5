"""
GOLD BOT - Multi-Timeframe Intelligence

Analyzes market direction and momentum across multiple timeframes.

Capabilities:

- Multi-timeframe trend classification
- Moving-average direction
- Price structure consistency
- Momentum comparison
- Higher-timeframe bias
- Trend agreement and conflict detection
- Data freshness and synchronization checks

IMPORTANT:

- This module does not produce standalone trade signals.
- Timeframes must use the same symbol and price convention.
- Candle observations must be chronological.
- Only closed candles should be used for confirmed analysis.
  """

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite
from typing import Any

SUPPORTED_TIMEFRAMES = (
"M1",
"M5",
"M15",
"M30",
"H1",
"H4",
"D1",
)

TIMEFRAME_SECONDS = {
"M1": 60,
"M5": 300,
"M15": 900,
"M30": 1800,
"H1": 3600,
"H4": 14400,
"D1": 86400,
}

MINIMUM_CANDLES = 30
MAXIMUM_CANDLES = 10000

SHORT_MA_PERIOD = 10
LONG_MA_PERIOD = 30

MAXIMUM_TIMEFRAME_AGE_MULTIPLIER = 2.0

class MultiTimeframeAnalysisError(ValueError):
"""Raised when timeframe input is invalid."""

def _number(value: Any, field: str) -> float:

if isinstance(value, bool):
    raise MultiTimeframeAnalysisError(
        f"{field} must be numeric."
    )

try:
    result = float(value)
except (TypeError, ValueError):
    raise MultiTimeframeAnalysisError(
        f"{field} must be numeric."
    )

if not isfinite(result):
    raise MultiTimeframeAnalysisError(
        f"{field} must be finite."
    )

return result

def _timestamp(value: Any, field: str) -> datetime:

if not isinstance(value, str):
    raise MultiTimeframeAnalysisError(
        f"{field} must be ISO-8601."
    )

try:
    result = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
except ValueError:
    raise MultiTimeframeAnalysisError(
        f"{field} is invalid."
    )

if result.tzinfo is None:
    raise MultiTimeframeAnalysisError(
        f"{field} must include a timezone."
    )

return result.astimezone(timezone.utc)

def _validate_candles(
candles: Sequence[Mapping[str, Any]],
timeframe: str,
) -> list[dict[str, Any]]:

if (
    isinstance(candles, (str, bytes))
    or not isinstance(candles, Sequence)
):
    raise MultiTimeframeAnalysisError(
        f"{timeframe} candles must be a sequence."
    )

if not (
    MINIMUM_CANDLES
    <= len(candles)
    <= MAXIMUM_CANDLES
):
    raise MultiTimeframeAnalysisError(
        f"{timeframe} requires between "
        f"{MINIMUM_CANDLES} and "
        f"{MAXIMUM_CANDLES} candles."
    )

validated = []
previous_timestamp = None

for index, candle in enumerate(candles):

    if not isinstance(candle, Mapping):
        raise MultiTimeframeAnalysisError(
            f"Invalid {timeframe} candle at {index}."
        )

    timestamp = _timestamp(
        candle.get("timestamp"),
        f"{timeframe}[{index}].timestamp",
    )

    open_price = _number(
        candle.get("open"),
        f"{timeframe}[{index}].open",
    )

    high = _number(
        candle.get("high"),
        f"{timeframe}[{index}].high",
    )

    low = _number(
        candle.get("low"),
        f"{timeframe}[{index}].low",
    )

    close = _number(
        candle.get("close"),
        f"{timeframe}[{index}].close",
    )

    if min(
        open_price,
        high,
        low,
        close,
    ) <= 0:
        raise MultiTimeframeAnalysisError(
            f"Non-positive price in {timeframe} candle {index}."
        )

    if high < low:
        raise MultiTimeframeAnalysisError(
            f"Invalid high/low in {timeframe} candle {index}."
        )

    if not low <= open_price <= high:
        raise MultiTimeframeAnalysisError(
            f"Open outside range in {timeframe} candle {index}."
        )

    if not low <= close <= high:
        raise MultiTimeframeAnalysisError(
            f"Close outside range in {timeframe} candle {index}."
        )

    if (
        previous_timestamp is not None
        and timestamp <= previous_timestamp
    ):
        raise MultiTimeframeAnalysisError(
            f"{timeframe} timestamps must be strictly increasing."
        )

    previous_timestamp = timestamp

    validated.append({
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
    })

return validated

def _average(values: Sequence[float]) -> float:

return sum(values) / len(values)

def _calculate_rsi(
closes: Sequence[float],
period: int = 14,
) -> float | None:

if len(closes) <= period:
    return None

changes = [
    closes[index] - closes[index - 1]
    for index in range(
        len(closes) - period,
        len(closes),
    )
]

gains = [
    max(change, 0.0)
    for change in changes
]

losses = [
    abs(min(change, 0.0))
    for change in changes
]

average_gain = _average(gains)
average_loss = _average(losses)

if average_loss == 0:

    if average_gain == 0:
        return 50.0

    return 100.0

relative_strength = (
    average_gain / average_loss
)

return (
    100.0
    - 100.0 / (1.0 + relative_strength)
)

def _analyze_timeframe(
candles: Sequence[dict[str, Any]],
timeframe: str,
reference_time: datetime,
) -> dict[str, Any]:

closes = [
    candle["close"]
    for candle in candles
]

highs = [
    candle["high"]
    for candle in candles
]

lows = [
    candle["low"]
    for candle in candles
]

short_ma = _average(
    closes[-SHORT_MA_PERIOD:]
)

long_ma = _average(
    closes[-LONG_MA_PERIOD:]
)

latest_close = closes[-1]

previous_close = closes[-2]

short_ma_difference_percent = (
    (short_ma - long_ma) / long_ma
) * 100.0

candle_return_percent = (
    (latest_close / previous_close) - 1.0
) * 100.0

recent_high = max(
    highs[-20:]
)

recent_low = min(
    lows[-20:]
)

rsi = _calculate_rsi(closes)

bullish_structure = (
    highs[-1] > highs[-2]
    and lows[-1] > lows[-2]
)

bearish_structure = (
    highs[-1] < highs[-2]
    and lows[-1] < lows[-2]
)

if (
    latest_close > short_ma > long_ma
    and short_ma_difference_percent > 0
):
    direction = "BULLISH"

elif (
    latest_close < short_ma < long_ma
    and short_ma_difference_percent < 0
):
    direction = "BEARISH"

else:
    direction = "NEUTRAL"

momentum = (
    "POSITIVE"
    if candle_return_percent > 0
    else "NEGATIVE"
    if candle_return_percent < 0
    else "FLAT"
)

if (
    direction == "BULLISH"
    and bullish_structure
):
    structure = "HIGHER_HIGH_HIGHER_LOW"

elif (
    direction == "BEARISH"
    and bearish_structure
):
    structure = "LOWER_HIGH_LOWER_LOW"

else:
    structure = "MIXED_OR_UNCONFIRMED"

latest_timestamp = candles[-1]["timestamp"]

age_seconds = (
    reference_time - latest_timestamp
).total_seconds()

timeframe_seconds = TIMEFRAME_SECONDS[
    timeframe
]

maximum_age = (
    timeframe_seconds
    * MAXIMUM_TIMEFRAME_AGE_MULTIPLIER
)

if age_seconds < 0:
    freshness = "FUTURE_DATED"

elif age_seconds > maximum_age:
    freshness = "STALE"

else:
    freshness = "FRESH"

return {
    "timeframe": timeframe,
    "direction": direction,
    "momentum": momentum,
    "structure": structure,
    "latest_close": latest_close,
    "short_moving_average": short_ma,
    "long_moving_average": long_ma,
    "moving_average_difference_percent": (
        short_ma_difference_percent
    ),
    "latest_candle_return_percent": (
        candle_return_percent
    ),
    "rsi_14": rsi,
    "recent_20_candle_high": recent_high,
    "recent_20_candle_low": recent_low,
    "latest_timestamp": latest_timestamp.isoformat(),
    "age_seconds": age_seconds,
    "freshness": freshness,
    "candles_analyzed": len(candles),
}

def _aggregate_directions(
results: Sequence[dict[str, Any]],
) -> dict[str, Any]:

bullish = sum(
    1 for item in results
    if item["direction"] == "BULLISH"
)

bearish = sum(
    1 for item in results
    if item["direction"] == "BEARISH"
)

neutral = sum(
    1 for item in results
    if item["direction"] == "NEUTRAL"
)

total = len(results)

if bullish > bearish:
    dominant = "BULLISH"

elif bearish > bullish:
    dominant = "BEARISH"

else:
    dominant = "MIXED"

agreement = (
    max(bullish, bearish) / total * 100.0
    if total
    else 0.0
)

return {
    "dominant_direction": dominant,
    "bullish_timeframes": bullish,
    "bearish_timeframes": bearish,
    "neutral_timeframes": neutral,
    "agreement_percent": round(
        agreement,
        2,
    ),
}

def analyze_multi_timeframe(
timeframe_data: Mapping[str, Sequence[Mapping[str, Any]]],
reference_time: str,
symbol: str = "XAUUSD",
) -> dict[str, Any]:

if not isinstance(timeframe_data, Mapping):
    raise MultiTimeframeAnalysisError(
        "timeframe_data must be a mapping."
    )

normalized_symbol = symbol.strip().upper()

if not normalized_symbol:
    raise MultiTimeframeAnalysisError(
        "Symbol cannot be empty."
    )

reference = _timestamp(
    reference_time,
    "reference_time",
)

results = []
warnings = []
missing_timeframes = []

for timeframe in SUPPORTED_TIMEFRAMES:

    candles = timeframe_data.get(
        timeframe
    )

    if candles is None:
        missing_timeframes.append(
            timeframe
        )
        continue

    validated = _validate_candles(
        candles,
        timeframe,
    )

    result = _analyze_timeframe(
        validated,
        timeframe,
        reference,
    )

    results.append(result)

    if result["freshness"] != "FRESH":

        warnings.append({
            "timeframe": timeframe,
            "type": result["freshness"],
        })

if not results:
    raise MultiTimeframeAnalysisError(
        "At least one supported timeframe is required."
    )

higher_timeframes = {
    "H1",
    "H4",
    "D1",
}

higher_results = [
    item for item in results
    if item["timeframe"] in higher_timeframes
]

lower_results = [
    item for item in results
    if item["timeframe"] not in higher_timeframes
]

overall = _aggregate_directions(
    results
)

higher = _aggregate_directions(
    higher_results
)

lower = _aggregate_directions(
    lower_results
)

conflict = (
    higher["dominant_direction"]
    in {"BULLISH", "BEARISH"}
    and lower["dominant_direction"]
    in {"BULLISH", "BEARISH"}
    and higher["dominant_direction"]
    != lower["dominant_direction"]
)

fresh_count = sum(
    1 for item in results
    if item["freshness"] == "FRESH"
)

data_quality_status = (
    "GOOD"
    if fresh_count == len(results)
    and not missing_timeframes
    else "PARTIAL"
    if fresh_count > 0
    else "STALE_OR_INVALID"
)

return {
    "module": "multi_timeframe_intelligence",
    "symbol": normalized_symbol,
    "timeframes_analyzed": [
        item["timeframe"]
        for item in results
    ],
    "missing_timeframes": missing_timeframes,
    "overall": overall,
    "higher_timeframe_bias": higher,
    "lower_timeframe_bias": lower,
    "higher_lower_conflict": conflict,
    "data_quality_status": data_quality_status,
    "fresh_timeframes": fresh_count,
    "timeframe_results": results,
    "warnings": warnings,
    "interpretation": (
        "HIGHER_AND_LOWER_TIMEFRAMES_CONFLICT"
        if conflict
        else "TIMEFRAME_ALIGNMENT_REQUIRES_REVIEW"
        if overall["agreement_percent"] < 70
        else "TIMEFRAME_ALIGNMENT_DETECTED"
    ),
    "limitations": [
        "Trend classification uses moving averages and recent candle structure.",
        "RSI is descriptive and is not an independent trade signal.",
        "Freshness thresholds are simplified and do not model exchange holidays or broker sessions.",
        "Timeframe candles must be sourced from the same symbol and compatible feed.",
        "This module does not resample or synchronize raw tick data.",
        "Only closed candles should be supplied.",
        "Missing timeframes reduce analytical coverage.",
        "Agreement does not imply a profitable trade.",
        "This module does not execute orders.",
    ],
}
