"""
GOLD BOT - Correlation & Dollar Intelligence

Analyzes relationships between gold and external markets.

Features:

- Return-based correlation
- Rolling correlation
- Dollar relationship analysis
- Correlation regime detection
- Relationship stability
- Gold confirmation and divergence context

IMPORTANT:

- Inputs must contain synchronized observations.
- Correlation does not establish causation.
- This module does not fetch market data.
- This module does not generate trade execution commands.
  """

from collections.abc import Mapping, Sequence
from math import isfinite, sqrt
from statistics import mean
from typing import Any

class CorrelationError(ValueError):
"""Raised when correlation inputs are invalid."""

DEFAULT_ASSETS = {
"DXY": "USD_INDEX",
"US10Y": "US_TREASURY_YIELD",
"SILVER": "PRECIOUS_METAL",
"EURUSD": "FX_MAJOR",
"USDJPY": "FX_MAJOR",
}

def _validate_series(
values: Sequence[Any],
name: str,
) -> list[float]:

if (
    isinstance(values, (str, bytes))
    or not isinstance(values, Sequence)
):
    raise CorrelationError(
        f"{name} must be a numeric sequence."
    )

result = []

for index, value in enumerate(values):

    if isinstance(value, bool):
        raise CorrelationError(
            f"{name}[{index}] cannot be boolean."
        )

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CorrelationError(
            f"{name}[{index}] must be numeric."
        )

    if not isfinite(number):
        raise CorrelationError(
            f"{name}[{index}] must be finite."
        )

    if number <= 0:
        raise CorrelationError(
            f"{name}[{index}] must be positive."
        )

    result.append(number)

return result

def calculate_returns(
prices: Sequence[Any],
) -> list[float]:

values = _validate_series(
    prices,
    "prices",
)

if len(values) < 2:
    return []

return [
    (values[index] / values[index - 1]) - 1.0
    for index in range(1, len(values))
]

def pearson_correlation(
first: Sequence[Any],
second: Sequence[Any],
) -> float | None:

if len(first) != len(second):
    raise CorrelationError(
        "Series lengths must match."
    )

if len(first) < 3:
    return None

x = _validate_series(
    first,
    "first",
)

y = _validate_series(
    second,
    "second",
)

mean_x = mean(x)
mean_y = mean(y)

deviations_x = [
    value - mean_x
    for value in x
]

deviations_y = [
    value - mean_y
    for value in y
]

numerator = sum(
    a * b
    for a, b in zip(
        deviations_x,
        deviations_y,
    )
)

sum_x = sum(
    value * value
    for value in deviations_x
)

sum_y = sum(
    value * value
    for value in deviations_y
)

denominator = sqrt(
    sum_x * sum_y
)

if denominator <= 0:
    return None

return max(
    -1.0,
    min(
        1.0,
        numerator / denominator,
    ),
)

def correlation_strength(
correlation: float | None,
) -> str:

if correlation is None:
    return "INSUFFICIENT_DATA"

value = abs(correlation)

if value >= 0.80:
    return "VERY_STRONG"

if value >= 0.60:
    return "STRONG"

if value >= 0.40:
    return "MODERATE"

if value >= 0.20:
    return "WEAK"

return "VERY_WEAK"

def correlation_direction(
correlation: float | None,
) -> str:

if correlation is None:
    return "UNKNOWN"

if correlation >= 0.20:
    return "POSITIVE"

if correlation <= -0.20:
    return "NEGATIVE"

return "NEAR_ZERO"

def rolling_correlation(
first_returns: Sequence[Any],
second_returns: Sequence[Any],
window: int = 30,
) -> list[dict[str, Any]]:

if (
    isinstance(window, bool)
    or not isinstance(window, int)
    or window < 3
):
    raise CorrelationError(
        "window must be an integer >= 3."
    )

first = _validate_series(
    first_returns,
    "first_returns",
)

second = _validate_series(
    second_returns,
    "second_returns",
)

if len(first) != len(second):
    raise CorrelationError(
        "Return series lengths must match."
    )

results = []

if len(first) < window:
    return results

for end in range(
    window,
    len(first) + 1,
):

    x = first[end - window:end]
    y = second[end - window:end]

    value = pearson_correlation(
        x,
        y,
    )

    results.append({
        "start_index": end - window,
        "end_index": end - 1,
        "correlation": value,
        "strength": correlation_strength(
            value
        ),
        "direction": correlation_direction(
            value
        ),
    })

return results

def analyze_pair(
gold_prices: Sequence[Any],
external_prices: Sequence[Any],
asset_name: str,
window: int = 30,
) -> dict[str, Any]:

if not isinstance(asset_name, str) or not asset_name.strip():
    raise CorrelationError(
        "asset_name cannot be empty."
    )

gold_returns = calculate_returns(
    gold_prices
)

external_returns = calculate_returns(
    external_prices
)

if len(gold_returns) != len(external_returns):
    raise CorrelationError(
        "Price series must be synchronized and equal in length."
    )

current = pearson_correlation(
    gold_returns,
    external_returns,
)

rolling = rolling_correlation(
    gold_returns,
    external_returns,
    window,
)

recent_values = [
    item["correlation"]
    for item in rolling[-5:]
    if item["correlation"] is not None
]

recent_average = (
    mean(recent_values)
    if recent_values
    else None
)

if current is None or recent_average is None:

    stability = "INSUFFICIENT_DATA"

elif abs(current - recent_average) >= 0.50:

    stability = "UNSTABLE"

elif abs(current - recent_average) >= 0.25:

    stability = "CHANGING"

else:

    stability = "RELATIVELY_STABLE"

return {
    "asset": asset_name.strip().upper(),
    "observation_count": len(gold_returns),
    "correlation": current,
    "direction": correlation_direction(
        current
    ),
    "strength": correlation_strength(
        current
    ),
    "recent_rolling_average": recent_average,
    "stability": stability,
    "rolling_observations": len(rolling),
    "interpretation": (
        "Positive correlation means both return series "
        "have tended to move in the same direction."
        if current is not None and current > 0
        else
        "Negative correlation means the return series "
        "have tended to move in opposite directions."
        if current is not None and current < 0
        else
        "Relationship cannot currently be classified."
    ),
}

def analyze_market_relationships(
gold_prices: Sequence[Any],
external_markets: Mapping[str, Sequence[Any]],
window: int = 30,
) -> dict[str, Any]:

if not isinstance(external_markets, Mapping):
    raise CorrelationError(
        "external_markets must be a mapping."
    )

if not external_markets:
    raise CorrelationError(
        "At least one external market is required."
    )

results = {}

for asset, prices in external_markets.items():

    if not isinstance(asset, str) or not asset.strip():
        raise CorrelationError(
            "Asset names cannot be empty."
        )

    results[asset.upper()] = analyze_pair(
        gold_prices=gold_prices,
        external_prices=prices,
        asset_name=asset,
        window=window,
    )

return {
    "primary_asset": "GOLD",
    "relationships": results,
    "market_count": len(results),
    "data_synchronization_verified": (
        "NOT_INDEPENDENTLY_VERIFIED"
    ),
    "causation_established": False,
}

def analyze_dollar_context(
dxy_correlation: float | None,
dxy_return: float,
) -> dict[str, Any]:

movement = _validate_series(
    [dxy_return],
    "dxy_return",
)[0]

if dxy_correlation is None:

    return {
        "status": "INSUFFICIENT_DATA",
        "gold_context": "UNKNOWN",
        "reason": "DXY correlation is unavailable.",
    }

if not isfinite(dxy_correlation) or not -1 <= dxy_correlation <= 1:
    raise CorrelationError(
        "dxy_correlation must be between -1 and 1."
    )

if abs(dxy_correlation) < 0.20:

    return {
        "status": "WEAK_RELATIONSHIP",
        "gold_context": "NO_CLEAR_DOLLAR_CONFIRMATION",
        "reason": "Gold and DXY currently show weak correlation.",
    }

expected_gold_direction = (
    -1
    if dxy_correlation < 0
    else 1
)

actual_gold_context = (
    "DOLLAR_RISING"
    if movement > 0
    else "DOLLAR_FALLING"
    if movement < 0
    else "DOLLAR_UNCHANGED"
)

return {
    "status": "RELATIONSHIP_OBSERVED",
    "gold_context": actual_gold_context,
    "historical_correlation": dxy_correlation,
    "expected_gold_direction_from_relationship": (
        "DOWN"
        if expected_gold_direction == -1 and movement > 0
        else "UP"
        if expected_gold_direction == -1 and movement < 0
        else "UP"
        if expected_gold_direction == 1 and movement > 0
        else "DOWN"
        if expected_gold_direction == 1 and movement < 0
        else "NO_DIRECTION"
    ),
    "reason": (
        "This is a statistical relationship, not a guaranteed "
        "gold price prediction."
    ),
}

def detect_correlation_break(
historical_correlations: Sequence[Any],
current_correlation: float,
minimum_change: float = 0.40,
) -> dict[str, Any]:

history = []

for value in historical_correlations:

    if isinstance(value, bool):
        raise CorrelationError(
            "Historical correlations must be numeric."
        )

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CorrelationError(
            "Invalid historical correlation."
        )

    if not isfinite(number) or not -1 <= number <= 1:
        raise CorrelationError(
            "Historical correlation must be between -1 and 1."
        )

    history.append(number)

if (
    isinstance(current_correlation, bool)
    or not isinstance(current_correlation, (int, float))
    or not isfinite(current_correlation)
    or not -1 <= current_correlation <= 1
):
    raise CorrelationError(
        "Invalid current correlation."
    )

if (
    isinstance(minimum_change, bool)
    or not isinstance(minimum_change, (int, float))
    or not isfinite(minimum_change)
    or not 0 < minimum_change <= 2
):
    raise CorrelationError(
        "minimum_change must be between 0 and 2."
    )

if len(history) < 3:

    return {
        "detected": False,
        "status": "INSUFFICIENT_HISTORY",
        "change": None,
    }

baseline = mean(
    history
)

change = abs(
    current_correlation - baseline
)

detected = change >= minimum_change

return {
    "detected": detected,
    "status": (
        "CORRELATION_BREAK_CANDIDATE"
        if detected
        else "NO_SIGNIFICANT_CHANGE"
    ),
    "historical_average": baseline,
    "current_correlation": current_correlation,
    "absolute_change": change,
    "threshold": minimum_change,
    "requires_confirmation": detected,
  }
