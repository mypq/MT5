"""
GOLD BOT - Intermarket Intelligence Analyzer

Analyzes statistical relationships between XAUUSD
and other financial markets.

Supported reference markets:

- DXY
- US10Y
- US02Y
- SILVER
- OIL
- SP500

Uses synchronized log returns rather than raw price levels.

This module does not fetch market data or execute trades.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite, log
from typing import Any

MINIMUM_PAIRED_RETURNS = 30

MAXIMUM_TIMESTAMP_GAP_SECONDS = 60

STRONG_CORRELATION_THRESHOLD = 0.70
MODERATE_CORRELATION_THRESHOLD = 0.40

SUPPORTED_MARKETS = {
"XAUUSD",
"DXY",
"US10Y",
"US02Y",
"SILVER",
"OIL",
"SP500",
}

class IntermarketAnalysisError(ValueError):
"""Raised when intermarket input data is invalid."""

def _parse_timestamp(value: Any) -> datetime:
if not isinstance(value, str):
raise IntermarketAnalysisError(
"Timestamp must be an ISO-8601 string."
)

try:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
except ValueError:
    raise IntermarketAnalysisError(
        "Invalid ISO-8601 timestamp."
    )

if parsed.tzinfo is None:
    raise IntermarketAnalysisError(
        "Timestamp must include a timezone."
    )

return parsed.astimezone(timezone.utc)

def _validate_market_series(
market: str,
observations: Sequence[Mapping[str, Any]],
) -> dict[datetime, float]:

if market not in SUPPORTED_MARKETS:
    raise IntermarketAnalysisError(
        f"Unsupported market: {market}"
    )

if isinstance(observations, (str, bytes)):
    raise IntermarketAnalysisError(
        f"Invalid observation sequence for {market}."
    )

validated = {}

for index, observation in enumerate(observations):

    if not isinstance(observation, Mapping):
        raise IntermarketAnalysisError(
            f"Invalid observation in {market} at index {index}."
        )

    timestamp = _parse_timestamp(
        observation.get("timestamp")
    )

    close = observation.get("close")

    if isinstance(close, bool):
        raise IntermarketAnalysisError(
            f"Invalid close price in {market}."
        )

    try:
        price = float(close)
    except (TypeError, ValueError):
        raise IntermarketAnalysisError(
            f"Invalid close price in {market}."
        )

    if not isfinite(price) or price <= 0:
        raise IntermarketAnalysisError(
            f"Close price must be finite and positive in {market}."
        )

    if timestamp in validated:
        raise IntermarketAnalysisError(
            f"Duplicate timestamp in {market}."
        )

    validated[timestamp] = price

return validated

def _calculate_log_returns(
series: Mapping[datetime, float],
) -> dict[datetime, float]:

ordered = sorted(series.items())

returns = {}

for index in range(1, len(ordered)):

    previous_time, previous_price = ordered[index - 1]
    current_time, current_price = ordered[index]

    gap_seconds = (
        current_time - previous_time
    ).total_seconds()

    if gap_seconds <= 0:
        continue

    if gap_seconds > MAXIMUM_TIMESTAMP_GAP_SECONDS:
        continue

    returns[current_time] = log(
        current_price / previous_price
    )

return returns

def _pearson_correlation(
x_values: Sequence[float],
y_values: Sequence[float],
) -> float | None:

if len(x_values) != len(y_values):
    raise IntermarketAnalysisError(
        "Correlation arrays must have equal lengths."
    )

if len(x_values) < MINIMUM_PAIRED_RETURNS:
    return None

x_mean = sum(x_values) / len(x_values)
y_mean = sum(y_values) / len(y_values)

x_deviations = [
    value - x_mean
    for value in x_values
]

y_deviations = [
    value - y_mean
    for value in y_values
]

covariance = sum(
    x * y
    for x, y in zip(
        x_deviations,
        y_deviations,
    )
)

x_variance = sum(
    value * value
    for value in x_deviations
)

y_variance = sum(
    value * value
    for value in y_deviations
)

denominator = (
    x_variance * y_variance
) ** 0.5

if denominator == 0:
    return None

correlation = covariance / denominator

return max(-1.0, min(1.0, correlation))

def _classify_correlation(
correlation: float | None,
) -> str:

if correlation is None:
    return "INSUFFICIENT_DATA"

absolute_value = abs(correlation)

if absolute_value >= STRONG_CORRELATION_THRESHOLD:
    strength = "STRONG"
elif absolute_value >= MODERATE_CORRELATION_THRESHOLD:
    strength = "MODERATE"
else:
    strength = "WEAK"

direction = (
    "POSITIVE"
    if correlation > 0
    else "NEGATIVE"
    if correlation < 0
    else "NEUTRAL"
)

return f"{strength}_{direction}"

def _interpret_gold_relationship(
market: str,
correlation: float | None,
) -> str:

if correlation is None:
    return "INSUFFICIENT_DATA"

if abs(correlation) < MODERATE_CORRELATION_THRESHOLD:
    return "NO_CLEAR_STATISTICAL_RELATIONSHIP"

if market in {"DXY", "US10Y", "US02Y"}:

    if correlation < 0:
        return "INVERSE_RELATIONSHIP_WITH_GOLD"

    return "POSITIVE_RELATIONSHIP_WITH_GOLD"

if market in {"SILVER", "OIL", "SP500"}:

    if correlation > 0:
        return "POSITIVE_RELATIONSHIP_WITH_GOLD"

    return "INVERSE_RELATIONSHIP_WITH_GOLD"

return "UNCLASSIFIED_RELATIONSHIP"

def analyze_intermarket(
market_data: Mapping[
str,
Sequence[Mapping[str, Any]]
],
) -> dict[str, Any]:

if not isinstance(market_data, Mapping):
    raise IntermarketAnalysisError(
        "market_data must be a mapping."
    )

if "XAUUSD" not in market_data:
    raise IntermarketAnalysisError(
        "XAUUSD reference data is required."
    )

validated_series = {}

for market, observations in market_data.items():

    if not isinstance(market, str):
        raise IntermarketAnalysisError(
            "Market names must be strings."
        )

    market = market.upper().strip()

    if market in validated_series:
        raise IntermarketAnalysisError(
            f"Duplicate market after normalization: {market}"
        )

    validated_series[market] = (
        _validate_market_series(
            market,
            observations,
        )
    )

gold_series = validated_series["XAUUSD"]

gold_returns = _calculate_log_returns(
    gold_series
)

relationships = {}

for market, series in validated_series.items():

    if market == "XAUUSD":
        continue

    market_returns = _calculate_log_returns(
        series
    )

    synchronized_timestamps = sorted(
        set(gold_returns) & set(market_returns)
    )

    gold_values = [
        gold_returns[timestamp]
        for timestamp in synchronized_timestamps
    ]

    market_values = [
        market_returns[timestamp]
        for timestamp in synchronized_timestamps
    ]

    correlation = _pearson_correlation(
        gold_values,
        market_values,
    )

    relationships[market] = {
        "correlation": correlation,
        "correlation_strength": (
            _classify_correlation(correlation)
        ),
        "gold_relationship": (
            _interpret_gold_relationship(
                market,
                correlation,
            )
        ),
        "paired_return_count": len(
            synchronized_timestamps
        ),
        "synchronized_data_available": (
            len(synchronized_timestamps)
            >= MINIMUM_PAIRED_RETURNS
        ),
        "first_paired_timestamp": (
            synchronized_timestamps[0].isoformat()
            if synchronized_timestamps
            else None
        ),
        "last_paired_timestamp": (
            synchronized_timestamps[-1].isoformat()
            if synchronized_timestamps
            else None
        ),
    }

return {
    "module": "intermarket_intelligence",
    "reference_market": "XAUUSD",
    "return_method": "LOGARITHMIC_RETURNS",
    "minimum_paired_returns": MINIMUM_PAIRED_RETURNS,
    "maximum_timestamp_gap_seconds": (
        MAXIMUM_TIMESTAMP_GAP_SECONDS
    ),
    "relationships": relationships,
    "available_reference_markets": list(
        relationships.keys()
    ),
    "limitations": [
        "Correlation measures statistical association, not causation.",
        "Correlation can change across market regimes.",
        "Exact synchronized return timestamps are required.",
        "Returns with excessive timestamp gaps are excluded.",
        "Different market trading sessions can reduce paired observations.",
        "This version does not calculate rolling or lagged correlations.",
        "Yield inputs must use consistent units and quote conventions.",
        "This module does not fetch market data or execute trades.",
    ],
  }
