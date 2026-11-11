"""
GOLD BOT - Advanced Intermarket Intelligence Analyzer

Features:
- Strict market data validation
- Synchronized logarithmic returns
- Pearson correlation
- Rolling correlation
- Lagged correlation analysis
- Market data freshness classification
- Timestamp gap filtering
- Correlation strength classification
- Statistical relationship interpretation

Important:
Correlation does not prove causation.
A statistical relationship is not a trading signal.
This module does not fetch market data or execute orders.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite, log
from typing import Any


MINIMUM_PAIRED_RETURNS = 30
MAXIMUM_TIMESTAMP_GAP_SECONDS = 86400
MAXIMUM_DATA_AGE_HOURS = 24
MAX_FUTURE_MINUTES = 5

STRONG_CORRELATION_THRESHOLD = 0.70
MODERATE_CORRELATION_THRESHOLD = 0.40

ROLLING_WINDOW_SIZE = 60
MINIMUM_ROLLING_RETURNS = 30

MAXIMUM_LAG_PERIODS = 5
MINIMUM_LAGGED_RETURNS = 30

MAXIMUM_OBSERVATIONS_PER_MARKET = 10000

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
    if isinstance(value, datetime):
        parsed = value

    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise IntermarketAnalysisError(
                "Invalid ISO-8601 timestamp."
            ) from exc

    else:
        raise IntermarketAnalysisError(
            "Timestamp must be an ISO-8601 string or datetime."
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

    if not isinstance(observations, Sequence) or isinstance(
        observations, (str, bytes)
    ):
        raise IntermarketAnalysisError(
            f"Invalid observation sequence for {market}."
        )

    if len(observations) > MAXIMUM_OBSERVATIONS_PER_MARKET:
        raise IntermarketAnalysisError(
            f"Too many observations for {market}."
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
        except (TypeError, ValueError, OverflowError) as exc:
            raise IntermarketAnalysisError(
                f"Invalid close price in {market}."
            ) from exc

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

        result = log(current_price / previous_price)

        if not isfinite(result):
            continue

        returns[current_time] = result

    return returns


def _pearson_correlation(
    x_values: Sequence[float],
    y_values: Sequence[float],
    minimum_samples: int = MINIMUM_PAIRED_RETURNS,
) -> float | None:
    if len(x_values) != len(y_values):
        raise IntermarketAnalysisError(
            "Correlation arrays must have equal lengths."
        )

    if len(x_values) < minimum_samples:
        return None

    if not all(isfinite(value) for value in x_values):
        raise IntermarketAnalysisError(
            "X values contain non-finite numbers."
        )

    if not all(isfinite(value) for value in y_values):
        raise IntermarketAnalysisError(
            "Y values contain non-finite numbers."
        )

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

    denominator = (x_variance * y_variance) ** 0.5

    if denominator == 0:
        return None

    correlation = covariance / denominator

    if not isfinite(correlation):
        return None

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

    if correlation > 0:
        direction = "POSITIVE"

    elif correlation < 0:
        direction = "NEGATIVE"

    else:
        direction = "NEUTRAL"

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


def _calculate_rolling_correlations(
    gold_values: Sequence[float],
    market_values: Sequence[float],
) -> dict[str, Any]:
    if len(gold_values) != len(market_values):
        raise IntermarketAnalysisError(
            "Rolling correlation arrays must have equal lengths."
        )

    sample_count = len(gold_values)

    window_size = min(
        ROLLING_WINDOW_SIZE,
        sample_count,
    )

    if window_size < MINIMUM_ROLLING_RETURNS:
        return {
            "available": False,
            "window_size": window_size,
            "correlation": None,
            "classification": "INSUFFICIENT_DATA",
        }

    correlation = _pearson_correlation(
        gold_values[-window_size:],
        market_values[-window_size:],
        minimum_samples=MINIMUM_ROLLING_RETURNS,
    )

    return {
        "available": correlation is not None,
        "window_size": window_size,
        "correlation": correlation,
        "classification": _classify_correlation(
            correlation
        ),
    }


def _calculate_lagged_correlations(
    gold_values: Sequence[float],
    market_values: Sequence[float],
) -> dict[str, Any]:
    if len(gold_values) != len(market_values):
        raise IntermarketAnalysisError(
            "Lagged correlation arrays must have equal lengths."
        )

    results = []

    for lag in range(
        -MAXIMUM_LAG_PERIODS,
        MAXIMUM_LAG_PERIODS + 1,
    ):
        if lag > 0:
            gold_sample = gold_values[lag:]
            market_sample = market_values[:-lag]

        elif lag < 0:
            gold_sample = gold_values[:lag]
            market_sample = market_values[-lag:]

        else:
            gold_sample = gold_values
            market_sample = market_values

        correlation = _pearson_correlation(
            gold_sample,
            market_sample,
            minimum_samples=MINIMUM_LAGGED_RETURNS,
        )

        results.append({
            "lag_periods": lag,
            "correlation": correlation,
            "classification": _classify_correlation(
                correlation
            ),
            "paired_return_count": len(gold_sample),
        })

    available_results = [
        item
        for item in results
        if item["correlation"] is not None
    ]

    if not available_results:
        return {
            "available": False,
            "results": results,
            "strongest_absolute_relationship": None,
        }

    strongest = max(
        available_results,
        key=lambda item: abs(item["correlation"]),
    )

    if strongest["lag_periods"] > 0:
        interpretation = "MARKET_LEADS_GOLD"

    elif strongest["lag_periods"] < 0:
        interpretation = "GOLD_LEADS_MARKET"

    else:
        interpretation = "NO_OBSERVED_LAG"

    return {
        "available": True,
        "results": results,
        "strongest_absolute_relationship": {
            **strongest,
            "interpretation": interpretation,
        },
        "warning": (
            "Lagged correlation does not prove predictive power."
        ),
    }


def _classify_data_freshness(
    latest_timestamp: datetime | None,
    current_time: datetime,
) -> dict[str, Any]:
    if latest_timestamp is None:
        return {
            "status": "NO_DATA",
            "age_hours": None,
        }

    age_hours = (
        current_time - latest_timestamp
    ).total_seconds() / 3600.0

    if age_hours < -(MAX_FUTURE_MINUTES / 60.0):
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


def analyze_intermarket(
    market_data: Mapping[
        str,
        Sequence[Mapping[str, Any]]
    ],
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(market_data, Mapping):
        raise IntermarketAnalysisError(
            "market_data must be a mapping."
        )

    if "XAUUSD" not in {
        str(key).upper().strip()
        for key in market_data.keys()
    }:
        raise IntermarketAnalysisError(
            "XAUUSD reference data is required."
        )

    if now is None:
        current_time = datetime.now(timezone.utc)

    else:
        if not isinstance(now, datetime):
            raise IntermarketAnalysisError(
                "now must be a datetime object."
            )

        if now.tzinfo is None:
            raise IntermarketAnalysisError(
                "now must include a timezone."
            )

        current_time = now.astimezone(timezone.utc)

    validated_series = {}

    for market_name, observations in market_data.items():
        if not isinstance(market_name, str):
            raise IntermarketAnalysisError(
                "Market names must be strings."
            )

        market = market_name.upper().strip()

        if market in validated_series:
            raise IntermarketAnalysisError(
                f"Duplicate market after normalization: {market}"
            )

        validated_series[market] = _validate_market_series(
            market,
            observations,
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

        rolling = _calculate_rolling_correlations(
            gold_values,
            market_values,
        )

        lagged = _calculate_lagged_correlations(
            gold_values,
            market_values,
        )

        latest_timestamp = (
            max(series.keys())
            if series
            else None
        )

        gold_latest_timestamp = (
            max(gold_series.keys())
            if gold_series
            else None
        )

        freshness = _classify_data_freshness(
            latest_timestamp,
            current_time,
        )

        gold_freshness = _classify_data_freshness(
            gold_latest_timestamp,
            current_time,
        )

        if latest_timestamp and gold_latest_timestamp:
            synchronization_gap_seconds = abs(
                (
                    latest_timestamp - gold_latest_timestamp
                ).total_seconds()
            )
        else:
            synchronization_gap_seconds = None

        relationships[market] = {
            "correlation": correlation,
            "correlation_strength": _classify_correlation(
                correlation
            ),
            "gold_relationship": _interpret_gold_relationship(
                market,
                correlation,
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
            "rolling_correlation": rolling,
            "lagged_correlation": lagged,
            "market_data_freshness": freshness,
            "gold_data_freshness": gold_freshness,
            "latest_market_timestamp_gap_seconds": (
                synchronization_gap_seconds
            ),
            "latest_data_synchronized": (
                synchronization_gap_seconds == 0
                if synchronization_gap_seconds is not None
                else False
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
        "maximum_data_age_hours": MAXIMUM_DATA_AGE_HOURS,
        "rolling_window_size": ROLLING_WINDOW_SIZE,
        "maximum_lag_periods": MAXIMUM_LAG_PERIODS,
        "relationships": relationships,
        "available_reference_markets": list(
            relationships.keys()
        ),
        "limitations": [
            "Correlation measures association, not causation.",
            "Correlation can change across market regimes.",
            "Exact synchronized return timestamps are required.",
            "Different market sessions can reduce paired observations.",
            "Rolling correlation can be unstable with small samples.",
            "Lagged correlation is exploratory, not predictive proof.",
            "Lag periods represent observations, not fixed clock durations.",
            "Yield inputs must use consistent units and quote conventions.",
            "Price observations must represent comparable sampling intervals.",
            "Freshness thresholds may require timeframe-specific configuration.",
            "This module does not fetch live market data.",
            "This module does not generate or execute trading orders.",
        ],
    }
