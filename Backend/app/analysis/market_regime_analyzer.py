"""
GOLD BOT - Market Regime Intelligence

Rule-based statistical classification of XAUUSD market conditions.

Regimes:
- TRENDING_UP
- TRENDING_DOWN
- RANGING
- HIGH_VOLATILITY
- TRANSITION
- INSUFFICIENT_DATA

This module:
- Uses logarithmic returns.
- Measures rolling and baseline volatility.
- Calculates directional efficiency.
- Detects statistical changes.
- Validates timestamps and data continuity.
- Rejects stale or future market observations.
- Does not fetch data or execute trades.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from math import isfinite, log, sqrt
from typing import Any


SUPPORTED_SYMBOL = "XAUUSD"

MINIMUM_OBSERVATIONS = 81
MINIMUM_VALID_RETURNS = 80

RECENT_WINDOW = 20
BASELINE_WINDOW = 60

MAXIMUM_GAP_SECONDS = 3600
MAXIMUM_DATA_AGE_SECONDS = 300
MAXIMUM_FUTURE_SECONDS = 5

TREND_EFFICIENCY_THRESHOLD = 0.40
STRONG_TREND_EFFICIENCY = 0.65

HIGH_VOLATILITY_RATIO = 1.50
LOW_VOLATILITY_RATIO = 0.65

CHANGE_MEAN_Z_THRESHOLD = 2.0
CHANGE_VOLATILITY_RATIO = 1.75

MINIMUM_STANDARD_DEVIATION = 1e-12


class MarketRegimeAnalysisError(ValueError):
    """Raised when market regime input data is invalid."""


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise MarketRegimeAnalysisError(
                "Invalid ISO-8601 timestamp."
            ) from exc
    else:
        raise MarketRegimeAnalysisError(
            "Timestamp must be an ISO-8601 string or datetime."
        )

    if parsed.tzinfo is None:
        raise MarketRegimeAnalysisError(
            "Timestamp must include a timezone."
        )

    return parsed.astimezone(timezone.utc)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise MarketRegimeAnalysisError(
            f"{field} must be numeric."
        )

    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarketRegimeAnalysisError(
            f"{field} must be numeric."
        ) from exc

    if not isfinite(result):
        raise MarketRegimeAnalysisError(
            f"{field} must be finite."
        )

    return result


def _validate_observations(
    observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:

    if (
        not isinstance(observations, Sequence)
        or isinstance(observations, (str, bytes))
    ):
        raise MarketRegimeAnalysisError(
            "Observations must be a sequence."
        )

    validated = []

    for index, observation in enumerate(observations):
        if not isinstance(observation, Mapping):
            raise MarketRegimeAnalysisError(
                f"Invalid observation at index {index}."
            )

        timestamp = _parse_timestamp(
            observation.get("timestamp")
        )

        close = _number(
            observation.get("close"),
            f"observations[{index}].close",
        )

        if close <= 0:
            raise MarketRegimeAnalysisError(
                f"Close must be positive at index {index}."
            )

        validated.append({
            "timestamp": timestamp,
            "close": close,
        })

    if len(validated) < 2:
        raise MarketRegimeAnalysisError(
            "At least two observations are required."
        )

    for index in range(1, len(validated)):
        previous_time = validated[index - 1]["timestamp"]
        current_time = validated[index]["timestamp"]

        if current_time <= previous_time:
            raise MarketRegimeAnalysisError(
                "Observations must be strictly chronological."
            )

    return validated


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None

    return sum(values) / len(values)


def _sample_std(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None

    average = _mean(values)

    variance = sum(
        (value - average) ** 2
        for value in values
    ) / (len(values) - 1)

    result = sqrt(variance)

    if not isfinite(result):
        raise MarketRegimeAnalysisError(
            "Invalid standard deviation."
        )

    return result


def _calculate_returns(
    observations: Sequence[Mapping[str, Any]],
    maximum_gap_seconds: int,
) -> tuple[list[float], int]:

    returns = []
    excluded_gaps = 0

    for index in range(1, len(observations)):
        previous = observations[index - 1]
        current = observations[index]

        gap_seconds = (
            current["timestamp"] - previous["timestamp"]
        ).total_seconds()

        if gap_seconds > maximum_gap_seconds:
            excluded_gaps += 1
            continue

        return_value = log(
            current["close"] / previous["close"]
        )

        if not isfinite(return_value):
            excluded_gaps += 1
            continue

        returns.append(return_value)

    return returns, excluded_gaps


def _calculate_efficiency_ratio(
    prices: Sequence[float],
) -> float | None:

    if len(prices) < 2:
        return None

    net_movement = abs(
        prices[-1] - prices[0]
    )

    total_movement = sum(
        abs(prices[index] - prices[index - 1])
        for index in range(1, len(prices))
    )

    if total_movement <= 0:
        return 0.0

    result = net_movement / total_movement

    return min(1.0, max(0.0, result))


def _detect_direction(
    prices: Sequence[float],
) -> str:

    if len(prices) < 2:
        return "UNKNOWN"

    if prices[-1] > prices[0]:
        return "UP"

    if prices[-1] < prices[0]:
        return "DOWN"

    return "FLAT"


def _detect_statistical_change(
    previous_returns: Sequence[float],
    recent_returns: Sequence[float],
) -> dict[str, Any]:

    previous_mean = _mean(previous_returns)
    recent_mean = _mean(recent_returns)

    previous_std = _sample_std(previous_returns)
    recent_std = _sample_std(recent_returns)

    if (
        previous_mean is None
        or recent_mean is None
        or previous_std is None
        or recent_std is None
    ):
        return {
            "detected": False,
            "mean_shift_z_score": None,
            "volatility_ratio": None,
            "reasons": ["INSUFFICIENT_DATA"],
        }

    pooled_scale = sqrt(
        (
            previous_std ** 2
            + recent_std ** 2
        ) / 2.0
    )

    if pooled_scale < MINIMUM_STANDARD_DEVIATION:
        mean_shift_z = 0.0
    else:
        mean_shift_z = (
            (recent_mean - previous_mean)
            / (
                pooled_scale
                * sqrt(
                    1.0 / len(previous_returns)
                    + 1.0 / len(recent_returns)
                )
            )
        )

    if previous_std < MINIMUM_STANDARD_DEVIATION:
        volatility_ratio = (
            float("inf")
            if recent_std >= MINIMUM_STANDARD_DEVIATION
            else 1.0
        )
    else:
        volatility_ratio = recent_std / previous_std

    reasons = []

    if abs(mean_shift_z) >= CHANGE_MEAN_Z_THRESHOLD:
        reasons.append("RETURN_MEAN_SHIFT")

    if (
        volatility_ratio >= CHANGE_VOLATILITY_RATIO
        or volatility_ratio <= 1.0 / CHANGE_VOLATILITY_RATIO
    ):
        reasons.append("VOLATILITY_SHIFT")

    return {
        "detected": bool(reasons),
        "mean_shift_z_score": (
            mean_shift_z
            if isfinite(mean_shift_z)
            else None
        ),
        "volatility_ratio": (
            volatility_ratio
            if isfinite(volatility_ratio)
            else None
        ),
        "reasons": (
            reasons
            if reasons
            else ["NO_SIGNIFICANT_CHANGE_DETECTED"]
        ),
    }


def _classify_regime(
    efficiency_ratio: float,
    direction: str,
    recent_volatility: float,
    baseline_volatility: float,
    change_detected: bool,
) -> str:

    if baseline_volatility <= MINIMUM_STANDARD_DEVIATION:
        if recent_volatility <= MINIMUM_STANDARD_DEVIATION:
            return "RANGING"

        return "TRANSITION"

    volatility_ratio = (
        recent_volatility / baseline_volatility
    )

    if volatility_ratio >= HIGH_VOLATILITY_RATIO:
        return "HIGH_VOLATILITY"

    if change_detected:
        return "TRANSITION"

    if efficiency_ratio >= TREND_EFFICIENCY_THRESHOLD:
        if direction == "UP":
            return "TRENDING_UP"

        if direction == "DOWN":
            return "TRENDING_DOWN"

    return "RANGING"


def _calculate_strength(
    efficiency_ratio: float,
    volatility_ratio: float | None,
    regime: str,
) -> str:

    if regime in {
        "INSUFFICIENT_DATA",
        "TRANSITION",
        "HIGH_VOLATILITY",
    }:
        return "UNSTABLE"

    if (
        efficiency_ratio >= STRONG_TREND_EFFICIENCY
        and volatility_ratio is not None
        and 0.70 <= volatility_ratio < HIGH_VOLATILITY_RATIO
    ):
        return "STRONG"

    if efficiency_ratio >= TREND_EFFICIENCY_THRESHOLD:
        return "MODERATE"

    return "WEAK"


def _insufficient_data_result(
    reason: str,
    observation_count: int,
    valid_returns: int | None = None,
    excluded_gaps: int | None = None,
) -> dict[str, Any]:

    result = {
        "module": "market_regime_intelligence",
        "symbol": SUPPORTED_SYMBOL,
        "data_available": False,
        "regime": "INSUFFICIENT_DATA",
        "regime_strength": "UNSTABLE",
        "required_observations": MINIMUM_OBSERVATIONS,
        "received_observations": observation_count,
        "reason": reason,
        "trading_signal_allowed": False,
    }

    if valid_returns is not None:
        result["valid_returns"] = valid_returns

    if excluded_gaps is not None:
        result["excluded_gap_count"] = excluded_gaps

    return result


def analyze_market_regime(
    observations: Sequence[Mapping[str, Any]],
    maximum_gap_seconds: int = MAXIMUM_GAP_SECONDS,
    maximum_data_age_seconds: int = MAXIMUM_DATA_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:

    for field, value in (
        ("maximum_gap_seconds", maximum_gap_seconds),
        ("maximum_data_age_seconds", maximum_data_age_seconds),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
        ):
            raise MarketRegimeAnalysisError(
                f"{field} must be a positive integer."
            )

    validated = _validate_observations(
        observations
    )

    if len(validated) < MINIMUM_OBSERVATIONS:
        return _insufficient_data_result(
            "NOT_ENOUGH_OBSERVATIONS",
            len(validated),
        )

    current_time = (
        _parse_timestamp(now)
        if now is not None
        else datetime.now(timezone.utc)
    )

    latest_timestamp = validated[-1]["timestamp"]

    age_seconds = (
        current_time - latest_timestamp
    ).total_seconds()

    if age_seconds < -MAXIMUM_FUTURE_SECONDS:
        return _insufficient_data_result(
            "LATEST_OBSERVATION_IS_IN_THE_FUTURE",
            len(validated),
        )

    if age_seconds > maximum_data_age_seconds:
        return _insufficient_data_result(
            "STALE_MARKET_DATA",
            len(validated),
        )

    returns, excluded_gaps = _calculate_returns(
        validated,
        maximum_gap_seconds,
    )

    required_returns = (
        BASELINE_WINDOW + RECENT_WINDOW
    )

    if len(returns) < required_returns:
        return _insufficient_data_result(
            "NOT_ENOUGH_CONTIGUOUS_RETURNS",
            len(validated),
            len(returns),
            excluded_gaps,
        )

    recent_returns = returns[-RECENT_WINDOW:]

    previous_returns = returns[
        -(BASELINE_WINDOW + RECENT_WINDOW):-RECENT_WINDOW
    ]

    # The baseline excludes the recent window to avoid overlap.
    baseline_returns = previous_returns

    recent_volatility = _sample_std(
        recent_returns
    )

    baseline_volatility = _sample_std(
        baseline_returns
    )

    efficiency_prices = [
        item["close"]
        for item in validated[-(RECENT_WINDOW + 1):]
    ]

    efficiency_ratio = _calculate_efficiency_ratio(
        efficiency_prices
    )

    direction = _detect_direction(
        efficiency_prices
    )

    change_detection = _detect_statistical_change(
        previous_returns,
        recent_returns,
    )

    if (
        recent_volatility is None
        or baseline_volatility is None
        or efficiency_ratio is None
    ):
        raise MarketRegimeAnalysisError(
            "Unable to calculate regime statistics."
        )

    volatility_ratio = (
        recent_volatility / baseline_volatility
        if baseline_volatility > MINIMUM_STANDARD_DEVIATION
        else None
    )

    regime = _classify_regime(
        efficiency_ratio,
        direction,
        recent_volatility,
        baseline_volatility,
        change_detection["detected"],
    )

    strength = _calculate_strength(
        efficiency_ratio,
        volatility_ratio,
        regime,
    )

    interpretation = {
        "TRENDING_UP": (
            "Price movement is directionally efficient and upward."
        ),
        "TRENDING_DOWN": (
            "Price movement is directionally efficient and downward."
        ),
        "RANGING": (
            "Directional efficiency is limited."
        ),
        "HIGH_VOLATILITY": (
            "Recent volatility is substantially above its baseline."
        ),
        "TRANSITION": (
            "Recent return behavior differs materially from the preceding window."
        ),
    }

    return {
        "module": "market_regime_intelligence",
        "symbol": SUPPORTED_SYMBOL,
        "data_available": True,
        "trading_signal_allowed": False,
        "regime": regime,
        "trend_direction": direction,
        "regime_strength": strength,
        "metrics": {
            "efficiency_ratio": efficiency_ratio,
            "recent_volatility": recent_volatility,
            "baseline_volatility": baseline_volatility,
            "volatility_ratio": volatility_ratio,
            "recent_window": RECENT_WINDOW,
            "baseline_window": BASELINE_WINDOW,
            "valid_return_count": len(returns),
            "excluded_gap_count": excluded_gaps,
        },
        "change_detection": change_detection,
        "data_quality": {
            "observation_count": len(validated),
            "first_timestamp": (
                validated[0]["timestamp"].isoformat()
            ),
            "last_timestamp": latest_timestamp.isoformat(),
            "latest_data_age_seconds": max(0.0, age_seconds),
            "maximum_gap_seconds": maximum_gap_seconds,
            "maximum_data_age_seconds": maximum_data_age_seconds,
            "chronological_order_verified": True,
        },
        "interpretation": interpretation[regime],
        "limitations": [
            "This is a rule-based statistical classifier, not an HMM.",
            "Change detection is a two-window statistical comparison, not Bayesian inference.",
            "Thresholds require validation for each timeframe.",
            "A detected transition is not proof of a permanent market regime change.",
            "The classifier uses closing prices and does not inspect order flow.",
            "Volatility is not annualized.",
            "Insufficient or stale data must not produce a trading signal.",
            "The baseline window excludes the recent window.",
            "This module does not fetch data or execute trading orders.",
        ],
    }
