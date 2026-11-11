"""
GOLD BOT - Quantitative Intelligence Analyzer

Statistical analysis of XAUUSD market behavior.

Features:

- Logarithmic returns
- Mean and standard deviation
- Rolling volatility
- Volatility regime classification
- Historical Value at Risk
- Maximum drawdown
- Return distribution statistics
- Extreme return detection
- Data quality checks

This module does not fetch market data,
predict guaranteed profits, or execute trades.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite, sqrt
from typing import Any

MINIMUM_OBSERVATIONS = 31
MINIMUM_RETURNS = MINIMUM_OBSERVATIONS - 1

ROLLING_VOLATILITY_WINDOW = 20

EXTREME_RETURN_Z_SCORE = 3.0

LOW_VOLATILITY_RATIO = 0.70
HIGH_VOLATILITY_RATIO = 1.50

MAXIMUM_GAP_SECONDS = 3600

SUPPORTED_SYMBOL = "XAUUSD"

class QuantitativeAnalysisError(ValueError):
"""Raised when quantitative input data is invalid."""

def _parse_timestamp(value: Any) -> datetime:
if not isinstance(value, str):
raise QuantitativeAnalysisError(
"Timestamp must be an ISO-8601 string."
)

try:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
except ValueError:
    raise QuantitativeAnalysisError(
        "Invalid ISO-8601 timestamp."
    )

if parsed.tzinfo is None:
    raise QuantitativeAnalysisError(
        "Timestamp must include a timezone."
    )

return parsed.astimezone(timezone.utc)

def _validate_observations(
observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:

if isinstance(observations, (str, bytes)):
    raise QuantitativeAnalysisError(
        "Observations must be a sequence."
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
    except (TypeError, ValueError):
        raise QuantitativeAnalysisError(
            f"Invalid close price at index {index}."
        )

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

    previous_close = previous["close"]
    current_close = current["close"]

    return_value = (
        current_close / previous_close
    )

    from math import log

    logarithmic_return = log(return_value)

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

if not 0 <= percentile <= 100:
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
    rolling_volatility
    / historical_volatility
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

if average is None or deviation is None or deviation == 0:
    return []

extreme_events = []

for item in returns:

    z_score = (
        item["value"] - average
    ) / deviation

    if abs(z_score) >= EXTREME_RETURN_Z_SCORE:

        extreme_events.append({
            "timestamp": item["timestamp"].isoformat(),
            "return_percent": (
                (2.718281828459045 ** item["value"]) - 1
            ) * 100.0,
            "z_score": z_score,
            "classification": (
                "EXTREME_POSITIVE_RETURN"
                if z_score > 0
                else "EXTREME_NEGATIVE_RETURN"
            ),
        })

return extreme_events

def analyze_quantitative(
observations: Sequence[Mapping[str, Any]],
maximum_gap_seconds: int = MAXIMUM_GAP_SECONDS,
) -> dict[str, Any]:

if (
    isinstance(maximum_gap_seconds, bool)
    or not isinstance(maximum_gap_seconds, int)
    or maximum_gap_seconds <= 0
):
    raise QuantitativeAnalysisError(
        "maximum_gap_seconds must be a positive integer."
    )

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
        "Not enough valid synchronized returns after gap filtering."
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

historical_var_95 = _percentile(
    values,
    5.0,
)

historical_var_99 = _percentile(
    values,
    1.0,
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
            if average_return is not None
            else None
        ),
        "historical_volatility_percent": (
            historical_volatility * 100.0
            if historical_volatility is not None
            else None
        ),
        "rolling_volatility_percent": (
            rolling_volatility * 100.0
            if rolling_volatility is not None
            else None
        ),
        "median_return_percent": (
            _percentile(values, 50.0) * 100.0
        ),
        "positive_return_count": sum(
            value > 0 for value in values
        ),
        "negative_return_count": len(
            negative_returns
        ),
        "zero_return_count": sum(
            value == 0 for value in values
        ),
        "positive_return_ratio_percent": (
            sum(value > 0 for value in values)
            / len(values)
        ) * 100.0,
        "worst_return_percent": (
            min(values) * 100.0
        ),
        "best_return_percent": (
            max(values) * 100.0
        ),
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
    "sample_quality": {
        "minimum_returns_required": MINIMUM_RETURNS,
        "sufficient_sample": (
            len(values) >= MINIMUM_RETURNS
        ),
        "first_observation": (
            validated[0]["timestamp"].isoformat()
        ),
        "last_observation": (
            validated[-1]["timestamp"].isoformat()
        ),
    },
    "limitations": [
        "Historical statistics do not guarantee future market behavior.",
        "Volatility values are not annualized.",
        "Value at Risk is based on historical return quantiles.",
        "Maximum drawdown is calculated from the supplied price path.",
        "Results depend on timeframe and observation quality.",
        "Extreme returns are statistical outliers, not necessarily errors.",
        "This module does not calculate position size or authorize trades.",
        "This module does not fetch market data or execute trading orders.",
    ],
}
