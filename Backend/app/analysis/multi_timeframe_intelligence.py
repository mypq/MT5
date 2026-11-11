"""
GOLD BOT - Multi-Timeframe Intelligence

Analyzes market direction, momentum and structure across timeframes.

Features:
- Multi-timeframe trend classification.
- Weighted higher/lower timeframe consensus.
- EMA-style moving-average trend comparison using SMA.
- Wilder RSI calculation.
- Recent price-structure analysis.
- Momentum comparison.
- Timestamp, freshness and closed-candle validation.
- Cross-timeframe conflict detection.

This module does not generate executable trading signals.
Only closed candles should be supplied by the trusted data adapter.
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

TIMEFRAME_WEIGHTS = {
    "M1": 1.0,
    "M5": 1.2,
    "M15": 1.4,
    "M30": 1.6,
    "H1": 2.0,
    "H4": 2.5,
    "D1": 3.0,
}

HIGHER_TIMEFRAMES = {"H1", "H4", "D1"}

SUPPORTED_SYMBOLS = {"XAUUSD"}

MINIMUM_CANDLES = 30
MAXIMUM_CANDLES = 10000

SHORT_MA_PERIOD = 10
LONG_MA_PERIOD = 30
RSI_PERIOD = 14

MAXIMUM_FUTURE_SKEW_SECONDS = 300
MAXIMUM_TIMEFRAME_AGE_MULTIPLIER = 2.0

MINIMUM_DIRECTION_AGREEMENT_PERCENT = 60.0


class MultiTimeframeAnalysisError(ValueError):
    """Raised when timeframe input is invalid."""


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise MultiTimeframeAnalysisError(
            f"{field} must be numeric."
        )

    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MultiTimeframeAnalysisError(
            f"{field} must be numeric."
        ) from exc

    if not isfinite(result):
        raise MultiTimeframeAnalysisError(
            f"{field} must be finite."
        )

    return result


def _timestamp(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise MultiTimeframeAnalysisError(
                f"{field} is invalid."
            ) from exc
    else:
        raise MultiTimeframeAnalysisError(
            f"{field} must be an ISO-8601 timestamp."
        )

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MultiTimeframeAnalysisError(
            f"{field} must include a timezone."
        )

    return parsed.astimezone(timezone.utc)


def _validate_reference_time(value: Any) -> datetime:
    reference = _timestamp(
        value,
        "reference_time",
    )

    now = datetime.now(timezone.utc)

    if (
        reference - now
    ).total_seconds() > MAXIMUM_FUTURE_SKEW_SECONDS:
        raise MultiTimeframeAnalysisError(
            "reference_time is too far in the future."
        )

    return reference


def _validate_symbol(symbol: Any) -> str:
    if not isinstance(symbol, str):
        raise MultiTimeframeAnalysisError(
            "Symbol must be a string."
        )

    normalized = symbol.strip().upper()

    if normalized not in SUPPORTED_SYMBOLS:
        raise MultiTimeframeAnalysisError(
            f"Unsupported symbol: {normalized or '<empty>'}."
        )

    return normalized


def _average(values: Sequence[float]) -> float:
    if not values:
        raise MultiTimeframeAnalysisError(
            "Cannot calculate an average from empty data."
        )

    return sum(values) / len(values)


def _validate_candles(
    candles: Sequence[Mapping[str, Any]],
    timeframe: str,
    reference_time: datetime,
) -> list[dict[str, Any]]:

    if (
        isinstance(candles, (str, bytes))
        or not isinstance(candles, Sequence)
    ):
        raise MultiTimeframeAnalysisError(
            f"{timeframe} candles must be a sequence."
        )

    if not MINIMUM_CANDLES <= len(candles) <= MAXIMUM_CANDLES:
        raise MultiTimeframeAnalysisError(
            f"{timeframe} requires between "
            f"{MINIMUM_CANDLES} and {MAXIMUM_CANDLES} candles."
        )

    validated = []
    previous_timestamp = None

    for index, candle in enumerate(candles):
        if not isinstance(candle, Mapping):
            raise MultiTimeframeAnalysisError(
                f"Invalid {timeframe} candle at index {index}."
            )

        if candle.get("is_closed") is False:
            raise MultiTimeframeAnalysisError(
                f"{timeframe} candle {index} is not closed."
            )

        timestamp = _timestamp(
            candle.get("timestamp"),
            f"{timeframe}[{index}].timestamp",
        )

        if timestamp > reference_time:
            raise MultiTimeframeAnalysisError(
                f"{timeframe} candle {index} is future-dated."
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

        if min(open_price, high, low, close) <= 0:
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


def _calculate_rsi(
    closes: Sequence[float],
    period: int = RSI_PERIOD,
) -> float | None:

    if len(closes) <= period:
        return None

    changes = [
        closes[index] - closes[index - 1]
        for index in range(1, len(closes))
    ]

    gains = [
        max(change, 0.0)
        for change in changes
    ]

    losses = [
        max(-change, 0.0)
        for change in changes
    ]

    average_gain = _average(gains[:period])
    average_loss = _average(losses[:period])

    for index in range(period, len(changes)):
        average_gain = (
            average_gain * (period - 1)
            + gains[index]
        ) / period

        average_loss = (
            average_loss * (period - 1)
            + losses[index]
        ) / period

    if average_loss == 0:
        if average_gain == 0:
            return 50.0

        return 100.0

    relative_strength = average_gain / average_loss

    return (
        100.0
        - 100.0 / (1.0 + relative_strength)
    )


def _calculate_structure(
    highs: Sequence[float],
    lows: Sequence[float],
) -> str:

    if len(highs) < 10 or len(lows) < 10:
        return "INSUFFICIENT_DATA"

    previous_high = max(highs[-10:-5])
    recent_high = max(highs[-5:])

    previous_low = min(lows[-10:-5])
    recent_low = min(lows[-5:])

    higher_high = recent_high > previous_high
    higher_low = recent_low > previous_low

    lower_high = recent_high < previous_high
    lower_low = recent_low < previous_low

    if higher_high and higher_low:
        return "HIGHER_HIGH_HIGHER_LOW"

    if lower_high and lower_low:
        return "LOWER_HIGH_LOWER_LOW"

    return "MIXED_OR_UNCONFIRMED"


def _analyze_timeframe(
    candles: Sequence[dict[str, Any]],
    timeframe: str,
    reference_time: datetime,
) -> dict[str, Any]:

    closes = [item["close"] for item in candles]
    highs = [item["high"] for item in candles]
    lows = [item["low"] for item in candles]

    short_ma = _average(closes[-SHORT_MA_PERIOD:])
    long_ma = _average(closes[-LONG_MA_PERIOD:])

    previous_short_ma = _average(
        closes[-SHORT_MA_PERIOD - 1:-1]
    )

    latest_close = closes[-1]
    previous_close = closes[-2]

    ma_difference_percent = (
        (short_ma - long_ma) / long_ma
    ) * 100.0

    ma_slope_percent = (
        (short_ma - previous_short_ma)
        / previous_short_ma
    ) * 100.0

    candle_return_percent = (
        (latest_close / previous_close) - 1.0
    ) * 100.0

    rsi = _calculate_rsi(closes)
    structure = _calculate_structure(highs, lows)

    if (
        latest_close > short_ma > long_ma
        and ma_slope_percent > 0
    ):
        direction = "BULLISH"

    elif (
        latest_close < short_ma < long_ma
        and ma_slope_percent < 0
    ):
        direction = "BEARISH"

    else:
        direction = "NEUTRAL"

    if candle_return_percent > 0:
        momentum = "POSITIVE"
    elif candle_return_percent < 0:
        momentum = "NEGATIVE"
    else:
        momentum = "FLAT"

    latest_timestamp = candles[-1]["timestamp"]

    age_seconds = (
        reference_time - latest_timestamp
    ).total_seconds()

    maximum_age = (
        TIMEFRAME_SECONDS[timeframe]
        * MAXIMUM_TIMEFRAME_AGE_MULTIPLIER
    )

    freshness = (
        "STALE"
        if age_seconds > maximum_age
        else "FRESH"
    )

    return {
        "timeframe": timeframe,
        "weight": TIMEFRAME_WEIGHTS[timeframe],
        "direction": direction,
        "momentum": momentum,
        "structure": structure,
        "latest_close": latest_close,
        "short_moving_average": short_ma,
        "long_moving_average": long_ma,
        "moving_average_difference_percent": (
            ma_difference_percent
        ),
        "moving_average_slope_percent": (
            ma_slope_percent
        ),
        "latest_candle_return_percent": (
            candle_return_percent
        ),
        "rsi_14": rsi,
        "recent_20_candle_high": max(highs[-20:]),
        "recent_20_candle_low": min(lows[-20:]),
        "latest_timestamp": latest_timestamp.isoformat(),
        "age_seconds": age_seconds,
        "maximum_age_seconds": maximum_age,
        "freshness": freshness,
        "candles_analyzed": len(candles),
    }


def _aggregate_directions(
    results: Sequence[dict[str, Any]],
) -> dict[str, Any]:

    bullish_weight = 0.0
    bearish_weight = 0.0
    neutral_weight = 0.0
    total_weight = 0.0

    bullish_count = 0
    bearish_count = 0
    neutral_count = 0

    for item in results:
        weight = item["weight"]
        total_weight += weight

        if item["direction"] == "BULLISH":
            bullish_weight += weight
            bullish_count += 1

        elif item["direction"] == "BEARISH":
            bearish_weight += weight
            bearish_count += 1

        else:
            neutral_weight += weight
            neutral_count += 1

    if total_weight == 0:
        return {
            "dominant_direction": "INSUFFICIENT_DATA",
            "bullish_timeframes": 0,
            "bearish_timeframes": 0,
            "neutral_timeframes": 0,
            "bullish_weight": 0.0,
            "bearish_weight": 0.0,
            "neutral_weight": 0.0,
            "agreement_percent": 0.0,
            "coverage_weight": 0.0,
        }

    directional_weights = {
        "BULLISH": bullish_weight,
        "BEARISH": bearish_weight,
        "NEUTRAL": neutral_weight,
    }

    dominant = max(
        directional_weights,
        key=directional_weights.get,
    )

    dominant_weight = directional_weights[dominant]

    agreement = (
        dominant_weight / total_weight
    ) * 100.0

    if dominant == "NEUTRAL":
        dominant = "MIXED"

    return {
        "dominant_direction": dominant,
        "bullish_timeframes": bullish_count,
        "bearish_timeframes": bearish_count,
        "neutral_timeframes": neutral_count,
        "bullish_weight": round(bullish_weight, 4),
        "bearish_weight": round(bearish_weight, 4),
        "neutral_weight": round(neutral_weight, 4),
        "agreement_percent": round(agreement, 2),
        "coverage_weight": round(total_weight, 4),
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

    normalized_symbol = _validate_symbol(symbol)

    reference = _validate_reference_time(
        reference_time
    )

    normalized_data = {}

    for key, value in timeframe_data.items():
        if not isinstance(key, str):
            raise MultiTimeframeAnalysisError(
                "Timeframe keys must be strings."
            )

        timeframe = key.strip().upper()

        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise MultiTimeframeAnalysisError(
                f"Unsupported timeframe: {timeframe}."
            )

        if timeframe in normalized_data:
            raise MultiTimeframeAnalysisError(
                f"Duplicate timeframe: {timeframe}."
            )

        normalized_data[timeframe] = value

    results = []
    warnings = []
    missing_timeframes = []
    stale_timeframes = []
    invalid_timeframes = []

    for timeframe in SUPPORTED_TIMEFRAMES:
        candles = normalized_data.get(timeframe)

        if candles is None:
            missing_timeframes.append(timeframe)
            continue

        try:
            validated = _validate_candles(
                candles,
                timeframe,
                reference,
            )

            result = _analyze_timeframe(
                validated,
                timeframe,
                reference,
            )

            results.append(result)

            if result["freshness"] == "STALE":
                stale_timeframes.append(timeframe)

        except MultiTimeframeAnalysisError as exc:
            invalid_timeframes.append({
                "timeframe": timeframe,
                "reason": str(exc),
            })

    if not results:
        raise MultiTimeframeAnalysisError(
            "No valid timeframe data was available."
        )

    fresh_results = [
        item for item in results
        if item["freshness"] == "FRESH"
    ]

    higher_results = [
        item for item in fresh_results
        if item["timeframe"] in HIGHER_TIMEFRAMES
    ]

    lower_results = [
        item for item in fresh_results
        if item["timeframe"] not in HIGHER_TIMEFRAMES
    ]

    overall = _aggregate_directions(fresh_results)
    higher = _aggregate_directions(higher_results)
    lower = _aggregate_directions(lower_results)

    higher_direction = higher["dominant_direction"]
    lower_direction = lower["dominant_direction"]

    conflict = (
        higher_direction in {"BULLISH", "BEARISH"}
        and lower_direction in {"BULLISH", "BEARISH"}
        and higher_direction != lower_direction
    )

    if not fresh_results:
        data_quality_status = "STALE_OR_INVALID"
    elif (
        missing_timeframes
        or stale_timeframes
        or invalid_timeframes
    ):
        data_quality_status = "PARTIAL"
    else:
        data_quality_status = "GOOD"

    latest_timestamps = [
        datetime.fromisoformat(item["latest_timestamp"])
        for item in fresh_results
    ]

    if len(latest_timestamps) >= 2:
        timestamp_skew_seconds = (
            max(latest_timestamps)
            - min(latest_timestamps)
        ).total_seconds()
    else:
        timestamp_skew_seconds = 0.0

    momentum_counts = {
        "POSITIVE": sum(
            1 for item in fresh_results
            if item["momentum"] == "POSITIVE"
        ),
        "NEGATIVE": sum(
            1 for item in fresh_results
            if item["momentum"] == "NEGATIVE"
        ),
        "FLAT": sum(
            1 for item in fresh_results
            if item["momentum"] == "FLAT"
        ),
    }

    structure_counts = {}

    for item in fresh_results:
        structure = item["structure"]
        structure_counts[structure] = (
            structure_counts.get(structure, 0) + 1
        )

    if missing_timeframes:
        warnings.append("MISSING_TIMEFRAMES")

    if stale_timeframes:
        warnings.append("STALE_TIMEFRAMES_EXCLUDED")

    if invalid_timeframes:
        warnings.append("INVALID_TIMEFRAMES_EXCLUDED")

    if conflict:
        warnings.append("HIGHER_LOWER_TIMEFRAME_CONFLICT")

    if not fresh_results:
        warnings.append("NO_FRESH_TIMEFRAME_DATA")

    if (
        overall["agreement_percent"]
        < MINIMUM_DIRECTION_AGREEMENT_PERCENT
    ):
        warnings.append("LOW_DIRECTION_AGREEMENT")

    if not higher_results:
        warnings.append("NO_FRESH_HIGHER_TIMEFRAME_DATA")

    if not lower_results:
        warnings.append("NO_FRESH_LOWER_TIMEFRAME_DATA")

    if not fresh_results:
        interpretation = "INSUFFICIENT_FRESH_DATA"
    elif conflict:
        interpretation = (
            "HIGHER_AND_LOWER_TIMEFRAMES_CONFLICT"
        )
    elif (
        overall["agreement_percent"]
        < MINIMUM_DIRECTION_AGREEMENT_PERCENT
    ):
        interpretation = "WEAK_TIMEFRAME_ALIGNMENT"
    else:
        interpretation = "TIMEFRAME_ALIGNMENT_DETECTED"

    analysis_usable = bool(
        fresh_results
        and not invalid_timeframes
        and overall["dominant_direction"] in {
            "BULLISH",
            "BEARISH",
        }
        and overall["agreement_percent"]
        >= MINIMUM_DIRECTION_AGREEMENT_PERCENT
    )

    return {
        "module": "multi_timeframe_intelligence",
        "symbol": normalized_symbol,
        "reference_time": reference.isoformat(),
        "timeframes_received": list(normalized_data.keys()),
        "timeframes_analyzed": [
            item["timeframe"] for item in results
        ],
        "fresh_timeframes": [
            item["timeframe"] for item in fresh_results
        ],
        "missing_timeframes": missing_timeframes,
        "stale_timeframes": stale_timeframes,
        "invalid_timeframes": invalid_timeframes,
        "overall": overall,
        "higher_timeframe_bias": higher,
        "lower_timeframe_bias": lower,
        "higher_lower_conflict": conflict,
        "data_quality_status": data_quality_status,
        "fresh_timeframe_count": len(fresh_results),
        "timestamp_skew_seconds": timestamp_skew_seconds,
        "momentum_distribution": momentum_counts,
        "structure_distribution": structure_counts,
        "timeframe_results": results,
        "warnings": warnings,
        "interpretation": interpretation,
        "analysis_usable": analysis_usable,
        "trading_signal_allowed": False,
        "limitations": [
            "Only closed candles should be supplied by the trusted data adapter.",
            "An omitted is_closed field is treated as caller-certified closed data.",
            "This module does not independently verify candle closure with MT5.",
            "Moving averages use simple arithmetic averages, not exponential averages.",
            "RSI uses Wilder smoothing.",
            "Structure classification compares recent five-candle ranges with the preceding five-candle ranges.",
            "Freshness thresholds are simplified and do not model broker holidays or session calendars.",
            "Timestamp skew is reported but is not proof of synchronized candle boundaries.",
            "All timeframes must use the same symbol and compatible price feed.",
            "Missing or stale timeframes reduce analytical coverage.",
            "Timeframe agreement does not imply a profitable trade.",
            "This module does not generate or authorize executable orders.",
        ],
        }
