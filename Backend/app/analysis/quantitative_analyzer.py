"""
GOLD BOT - Quantitative Intelligence Analyzer

Statistical analysis of XAUUSD market behavior.

Features:
- Logarithmic returns
- Mean, median and standard deviation
- Rolling volatility
- Volatility regime classification
- Historical Value at Risk
- Maximum drawdown
- Return distribution statistics
- Extreme return detection
- Observation quality and freshness checks
- Time-gap filtering

This module does not fetch market data,
predict guaranteed profits, or execute trades.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import exp, isfinite, log, sqrt
from statistics import median
from typing import Any


MINIMUM_OBSERVATIONS = 31
MINIMUM_RETURNS = 30

ROLLING_VOLATILITY_WINDOW = 20

EXTREME_RETURN_Z_SCORE = 3.0

LOW_VOLATILITY_RATIO = 0.70
HIGH_VOLATILITY_RATIO = 1.50

MAXIMUM_GAP_SECONDS = 3600
MAXIMUM_OBSERVATIONS = 10000
MAXIMUM_DATA_AGE_HOURS = 24
MAXIMUM_FUTURE_MINUTES = 5

SUPPORTED_SYMBOL = "XAUUSD"


class QuantitativeAnalysisError(ValueError):
    """Raised when quantitative input data is invalid."""


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value

    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise QuantitativeAnalysisError(
                "Invalid ISO-8601 timestamp."
            ) from exc

    else:
        raise QuantitativeAnalysisError(
            "Timestamp must be an ISO-8601 string or datetime."
        )

    if parsed.tzinfo is None:
        raise QuantitativeAnalysisError(
            "Timestamp must include a timezone."
        )

    return parsed.astimezone(timezone.utc)


def _validate_observations(
    observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(observations, Sequence) or isinstance(
        observations, (str, bytes)
    ):
        raise QuantitativeAnalysisError(
            "Observations must be a valid sequence."
        )

    if len(observations) > MAXIMUM_OBSERVATIONS:
        raise QuantitativeAnalysisError(
            "Observation count exceeds the maximum allowed."
        )

    validated = []

    for index, observation in enumerate(observations):
        if not isinstance(observation, Mapping):
            raise QuantitativeAnalysisError(
                f"Invalid observation at index {index}."
            )

        timestamp = _parse_timestamp(
            observation.get("timestamp")
        )

        raw_close = observation.get("close")

        if isinstance(raw_close, bool):
            raise QuantitativeAnalysisError(
                f"Invalid close price at index {index}."
            )

        try:
            close = float(raw_close)
        except (TypeError, ValueError, OverflowError) as exc:
            raise QuantitativeAnalysisError(
                f"Invalid close price at index {index}."
            ) from exc

        if not isfinite(close) or close <= 0:
            raise QuantitativeAnalysisError(
                f"Close price must be positive and finite at index {index}."
            )

        validated.append({
            "timestamp": timestamp,
            "close": close,
        })

    validated.sort(
        key=lambda item: item["timestamp"]
    )

    for index in range(1, len(validated)):
        if (
            validated[index]["timestamp"]
            == validated[index - 1]["timestamp"]
        ):
            raise QuantitativeAnalysisError(
                "Duplicate observation timestamps detected."
            )

    return validated


def _calculate_returns(
    observations: Sequence[Mapping[str, Any]],
    maximum_gap_seconds: int,
) -> list[dict[str, Any]]:
    returns = []

    for index in range(1, len(observations)):
        previous = observations[index - 1]
        current = observations[index]

        gap_seconds = (
            current["timestamp"] - previous["timestamp"]
        ).total_seconds()

        if gap_seconds <= 0:
            continue

        if gap_seconds > maximum_gap_seconds:
            continue

        logarithmic_return = log(
            current["close"] / previous["close"]
        )

        if not isfinite(logarithmic_return):
            continue

        returns.append({
            "timestamp": current["timestamp"],
            "value": logarithmic_return,
            "gap_seconds": gap_seconds,
        })

    return returns


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None

    return sum(values) / len(values)


def _sample_standard_deviation(
    values: Sequence[float],
) -> float | None:
    if len(values) < 2:
        return None

    average = _mean(values)

    variance = sum(
        (value - average) ** 2
        for value in values
    ) / (len(values) - 1)

    return sqrt(variance)


def _percentile(
    values: Sequence[float],
    percentile: float,
) -> float | None:
    if not values:
        return None

    if (
        isinstance(percentile, bool)
        or not isinstance(percentile, (int, float))
        or not isfinite(percentile)
        or not 0 <= percentile <= 100
    ):
        raise QuantitativeAnalysisError(
            "Percentile must be between 0 and 100."
        )

    ordered = sorted(values)

    position = (
        (len(ordered) - 1)
        * percentile
        / 100.0
    )

    lower_index = int(position)

    upper_index = min(
        lower_index + 1,
        len(ordered) - 1,
    )

    fraction = position - lower_index

    return (
        ordered[lower_index] * (1.0 - fraction)
        + ordered[upper_index] * fraction
    )


def _calculate_maximum_drawdown(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not observations:
        return {
            "maximum_drawdown_percent": None,
            "peak_timestamp": None,
            "trough_timestamp": None,
        }

    peak_price = observations[0]["close"]
    peak_timestamp = observations[0]["timestamp"]

    maximum_drawdown = 0.0
    maximum_peak_timestamp = peak_timestamp
    maximum_trough_timestamp = peak_timestamp

    for observation in observations:
        price = observation["close"]

        if price > peak_price:
            peak_price = price
            peak_timestamp = observation["timestamp"]

        drawdown = (
            (price - peak_price) / peak_price
        ) * 100.0

        if drawdown < maximum_drawdown:
            maximum_drawdown = drawdown
            maximum_peak_timestamp = peak_timestamp
            maximum_trough_timestamp = observation["timestamp"]

    return {
        "maximum_drawdown_percent": maximum_drawdown,
        "peak_timestamp": maximum_peak_timestamp.isoformat(),
        "trough_timestamp": maximum_trough_timestamp.isoformat(),
    }


def _classify_volatility_regime(
    rolling_volatility: float | None,
    historical_volatility: float | None,
) -> str:
    if (
        rolling_volatility is None
        or historical_volatility is None
        or historical_volatility <= 0
    ):
        return "INSUFFICIENT_DATA"

    ratio = (
        rolling_volatility / historical_volatility
    )

    if ratio < LOW_VOLATILITY_RATIO:
        return "LOW_VOLATILITY"

    if ratio > HIGH_VOLATILITY_RATIO:
        return "HIGH_VOLATILITY"

    return "NORMAL_VOLATILITY"


def _detect_extreme_returns(
    returns: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    values = [
        item["value"]
        for item in returns
    ]

    average = _mean(values)
    deviation = _sample_standard_deviation(values)

    if (
        average is None
        or deviation is None
        or deviation == 0
    ):
        return []

    extreme_events = []

    for item in returns:
        z_score = (
            item["value"] - average
        ) / deviation

        if abs(z_score) < EXTREME_RETURN_Z_SCORE:
            continue

        try:
            return_percent = (
                exp(item["value"]) - 1.0
            ) * 100.0
        except OverflowError:
            continue

        extreme_events.append({
            "timestamp": item["timestamp"].isoformat(),
            "return_percent": return_percent,
            "z_score": z_score,
            "classification": (
                "EXTREME_POSITIVE_RETURN"
                if z_score > 0
                else "EXTREME_NEGATIVE_RETURN"
            ),
        })

    return extreme_events


def _classify_data_freshness(
    latest_timestamp: datetime,
    current_time: datetime,
) -> dict[str, Any]:
    age_hours = (
        current_time - latest_timestamp
    ).total_seconds() / 3600.0

    if age_hours < -(MAXIMUM_FUTURE_MINUTES / 60.0):
        status = "FUTURE_TIMESTAMP"

    elif age_hours < 0:
        status = "RECENT_FUTURE_TIMESTAMP"

    elif age_hours > MAXIMUM_DATA_AGE_HOURS:
        status = "STALE"

    else:
        status = "CURRENT"

    return {
        "status": status,
        "age_hours": age_hours,
        "latest_timestamp": latest_timestamp.isoformat(),
    }


def analyze_quantitative(
    observations: Sequence[Mapping[str, Any]],
    maximum_gap_seconds: int = MAXIMUM_GAP_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    if (
        isinstance(maximum_gap_seconds, bool)
        or not isinstance(maximum_gap_seconds, int)
        or maximum_gap_seconds <= 0
    ):
        raise QuantitativeAnalysisError(
            "maximum_gap_seconds must be a positive integer."
        )

    if now is None:
        current_time = datetime.now(timezone.utc)

    else:
        if not isinstance(now, datetime):
            raise QuantitativeAnalysisError(
                "now must be a datetime object."
            )

        if now.tzinfo is None:
            raise QuantitativeAnalysisError(
                "now must include a timezone."
            )

        current_time = now.astimezone(timezone.utc)

    validated = _validate_observations(
        observations
    )

    if len(validated) < MINIMUM_OBSERVATIONS:
        raise QuantitativeAnalysisError(
            f"At least {MINIMUM_OBSERVATIONS} observations are required."
        )

    returns = _calculate_returns(
        validated,
        maximum_gap_seconds,
    )

    values = [
        item["value"]
        for item in returns
    ]

    if len(values) < MINIMUM_RETURNS:
        raise QuantitativeAnalysisError(
            "Not enough valid returns after gap filtering."
        )

    average_return = _mean(values)

    historical_volatility = (
        _sample_standard_deviation(values)
    )

    rolling_values = values[
        -ROLLING_VOLATILITY_WINDOW:
    ]

    rolling_volatility = (
        _sample_standard_deviation(rolling_values)
        if len(rolling_values) >= 2
        else None
    )

    negative_returns = [
        value
        for value in values
        if value < 0
    ]

    positive_return_count = sum(
        value > 0
        for value in values
    )

    historical_var_95 = _percentile(
        values,
        5.0,
    )

    historical_var_99 = _percentile(
        values,
        1.0,
    )

    latest_timestamp = validated[-1]["timestamp"]

    freshness = _classify_data_freshness(
        latest_timestamp,
        current_time,
    )

    drawdown = _calculate_maximum_drawdown(
        validated
    )

    return {
        "module": "quantitative_intelligence",
        "symbol": SUPPORTED_SYMBOL,
        "observation_count": len(validated),
        "valid_return_count": len(values),
        "excluded_gap_count": (
            len(validated) - 1 - len(returns)
        ),
        "maximum_gap_seconds": maximum_gap_seconds,
        "return_method": "LOGARITHMIC_RETURNS",
        "statistics": {
            "mean_return_percent": (
                average_return * 100.0
            ),
            "historical_volatility_percent": (
                historical_volatility * 100.0
            ),
            "rolling_volatility_percent": (
                rolling_volatility * 100.0
                if rolling_volatility is not None
                else None
            ),
            "median_return_percent": (
                median(values) * 100.0
            ),
            "positive_return_count": positive_return_count,
            "negative_return_count": len(negative_returns),
            "zero_return_count": sum(
                value == 0
                for value in values
            ),
            "positive_return_ratio_percent": (
                positive_return_count / len(values)
            ) * 100.0,
            "negative_return_ratio_percent": (
                len(negative_returns) / len(values)
            ) * 100.0,
            "worst_return_percent": min(values) * 100.0,
            "best_return_percent": max(values) * 100.0,
        },
        "historical_value_at_risk": {
            "confidence_95_percent": (
                historical_var_95 * 100.0
            ),
            "confidence_99_percent": (
                historical_var_99 * 100.0
            ),
            "interpretation": (
                "Historical return quantiles, not guaranteed maximum losses."
            ),
        },
        "drawdown": drawdown,
        "volatility_regime": (
            _classify_volatility_regime(
                rolling_volatility,
                historical_volatility,
            )
        ),
        "extreme_returns": _detect_extreme_returns(
            returns
        ),
        "data_freshness": freshness,
        "sample_quality": {
            "minimum_returns_required": MINIMUM_RETURNS,
            "sufficient_sample": (
                len(values) >= MINIMUM_RETURNS
            ),
            "first_observation": (
                validated[0]["timestamp"].isoformat()
            ),
            "last_observation": latest_timestamp.isoformat(),
            "excluded_gap_count": (
                len(validated) - 1 - len(returns)
            ),
        },
        "limitations": [
            "Historical statistics do not guarantee future market behavior.",
            "Volatility values are not annualized.",
            "Value at Risk uses historical return quantiles.",
            "Value at Risk is not a maximum possible loss.",
            "Maximum drawdown is calculated from the supplied price path.",
            "Results depend on timeframe and observation quality.",
            "Extreme returns are statistical outliers, not necessarily errors.",
            "A stale or future timestamp must not be treated as live data.",
            "This module does not calculate position size or authorize trades.",
            "This module does not fetch market data or execute trading orders.",
        ],
    }
