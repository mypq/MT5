"""
GOLD BOT - Anomaly Detection Intelligence

Detects unusual market observations using robust statistics.

Detection categories:

- Abnormal price returns
- Abnormal candle ranges
- Abnormal volume
- Volatility shocks
- Unexpected timestamp gaps

Methods:

- Rolling median
- Median Absolute Deviation (MAD)
- Modified robust Z-score
- Historical baseline comparison

IMPORTANT:

- This module detects statistical anomalies, not market direction.
- An anomaly is not automatically a trading opportunity.
- Historical baseline quality affects detection quality.
- This module does not fetch data or execute orders.
  """

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite, sqrt
from statistics import median
from typing import Any

SUPPORTED_SYMBOL = "XAUUSD"

MINIMUM_OBSERVATIONS = 30
MAXIMUM_OBSERVATIONS = 100000

DEFAULT_BASELINE_WINDOW = 50

DEFAULT_RETURN_THRESHOLD = 3.5
DEFAULT_RANGE_THRESHOLD = 3.5
DEFAULT_VOLUME_THRESHOLD = 3.5

DEFAULT_MINIMUM_ABSOLUTE_RETURN_PERCENT = 0.10

MAD_NORMALIZATION = 0.6744897501960817
MAD_EPSILON = 1e-12

class AnomalyAnalysisError(ValueError):
"""Raised when anomaly detector input is invalid."""

def _number(value: Any, field: str) -> float:

if isinstance(value, bool):
    raise AnomalyAnalysisError(
        f"{field} must be numeric."
    )

try:
    result = float(value)
except (TypeError, ValueError):
    raise AnomalyAnalysisError(
        f"{field} must be numeric."
    )

if not isfinite(result):
    raise AnomalyAnalysisError(
        f"{field} must be finite."
    )

return result

def _parse_timestamp(
value: Any,
field: str,
) -> datetime:

if not isinstance(value, str):
    raise AnomalyAnalysisError(
        f"{field} must be an ISO-8601 timestamp."
    )

try:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
except ValueError:
    raise AnomalyAnalysisError(
        f"{field} is invalid."
    )

if parsed.tzinfo is None:
    raise AnomalyAnalysisError(
        f"{field} must include a timezone."
    )

return parsed.astimezone(timezone.utc)

def _validate_observations(
observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:

if (
    isinstance(observations, (str, bytes))
    or not isinstance(observations, Sequence)
):
    raise AnomalyAnalysisError(
        "Observations must be a sequence."
    )

if not (
    MINIMUM_OBSERVATIONS
    <= len(observations)
    <= MAXIMUM_OBSERVATIONS
):
    raise AnomalyAnalysisError(
        f"Observations must contain between "
        f"{MINIMUM_OBSERVATIONS} and "
        f"{MAXIMUM_OBSERVATIONS} candles."
    )

validated = []
previous_timestamp = None

for index, item in enumerate(observations):

    if not isinstance(item, Mapping):
        raise AnomalyAnalysisError(
            f"Invalid observation at index {index}."
        )

    timestamp = _parse_timestamp(
        item.get("timestamp"),
        f"observations[{index}].timestamp",
    )

    open_price = _number(
        item.get("open"),
        f"observations[{index}].open",
    )

    high = _number(
        item.get("high"),
        f"observations[{index}].high",
    )

    low = _number(
        item.get("low"),
        f"observations[{index}].low",
    )

    close = _number(
        item.get("close"),
        f"observations[{index}].close",
    )

    volume = _number(
        item.get("volume", 0),
        f"observations[{index}].volume",
    )

    if min(
        open_price,
        high,
        low,
        close,
    ) <= 0:
        raise AnomalyAnalysisError(
            f"Prices must be positive at index {index}."
        )

    if high < low:
        raise AnomalyAnalysisError(
            f"High must be greater than or equal to low at index {index}."
        )

    if not low <= open_price <= high:
        raise AnomalyAnalysisError(
            f"Open is outside candle range at index {index}."
        )

    if not low <= close <= high:
        raise AnomalyAnalysisError(
            f"Close is outside candle range at index {index}."
        )

    if volume < 0:
        raise AnomalyAnalysisError(
            f"Volume cannot be negative at index {index}."
        )

    if (
        previous_timestamp is not None
        and timestamp <= previous_timestamp
    ):
        raise AnomalyAnalysisError(
            "Timestamps must be strictly increasing and unique."
        )

    previous_timestamp = timestamp

    candle_range_percent = (
        (high - low) / open_price
    ) * 100.0

    validated.append({
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "range_percent": candle_range_percent,
    })

return validated

def _robust_score(
value: float,
baseline: Sequence[float],
) -> dict[str, Any]:

if not baseline:
    return {
        "score": None,
        "baseline_median": None,
        "baseline_mad": None,
        "method": "INSUFFICIENT_BASELINE",
    }

baseline_median = median(baseline)

absolute_deviations = [
    abs(item - baseline_median)
    for item in baseline
]

mad = median(absolute_deviations)

if mad > MAD_EPSILON:

    score = (
        MAD_NORMALIZATION
        * (value - baseline_median)
        / mad
    )

    method = "MEDIAN_ABSOLUTE_DEVIATION"

else:

    mean = sum(baseline) / len(baseline)

    variance = sum(
        (item - mean) ** 2
        for item in baseline
    ) / len(baseline)

    standard_deviation = sqrt(
        variance
    )

    if standard_deviation > MAD_EPSILON:

        score = (
            value - mean
        ) / standard_deviation

        method = "STANDARD_DEVIATION_FALLBACK"

    elif abs(value - baseline_median) <= MAD_EPSILON:

        score = 0.0
        method = "CONSTANT_BASELINE"

    else:

        # A non-zero deviation from a constant baseline
        # is considered an extreme statistical deviation.
        score = (
            1e12
            if value > baseline_median
            else -1e12
        )

        method = "CONSTANT_BASELINE_DEVIATION"

return {
    "score": score,
    "baseline_median": baseline_median,
    "baseline_mad": mad,
    "method": method,
}

def _threshold_check(
score: float | None,
threshold: float,
) -> bool:

return (
    score is not None
    and abs(score) >= threshold
)

def _analyze_observation(
observations: Sequence[Mapping[str, Any]],
index: int,
baseline_window: int,
return_threshold: float,
range_threshold: float,
volume_threshold: float,
minimum_absolute_return_percent: float,
expected_interval_seconds: int | None,
gap_multiplier: float,
) -> dict[str, Any]:

current = observations[index]

previous = observations[index - 1]

baseline_start = max(
    1,
    index - baseline_window,
)

historical = observations[
    baseline_start:index
]

historical_returns = []
historical_ranges = []
historical_volumes = []

for position in range(
    baseline_start,
    index,
):

    candle = observations[position]

    previous_close = observations[
        position - 1
    ]["close"]

    historical_return = (
        (candle["close"] / previous_close) - 1.0
    ) * 100.0

    historical_returns.append(
        abs(historical_return)
    )

    historical_ranges.append(
        candle["range_percent"]
    )

    historical_volumes.append(
        candle["volume"]
    )

previous_close = previous["close"]

current_return = (
    (current["close"] / previous_close) - 1.0
) * 100.0

absolute_return = abs(
    current_return
)

return_stats = _robust_score(
    absolute_return,
    historical_returns,
)

range_stats = _robust_score(
    current["range_percent"],
    historical_ranges,
)

volume_stats = _robust_score(
    current["volume"],
    historical_volumes,
)

return_anomaly = (
    _threshold_check(
        return_stats["score"],
        return_threshold,
    )
    and absolute_return
    >= minimum_absolute_return_percent
)

range_anomaly = _threshold_check(
    range_stats["score"],
    range_threshold,
)

volume_anomaly = _threshold_check(
    volume_stats["score"],
    volume_threshold,
)

elapsed_seconds = (
    current["timestamp"] - previous["timestamp"]
).total_seconds()

gap_anomaly = False

if expected_interval_seconds is not None:

    gap_anomaly = (
        elapsed_seconds
        > expected_interval_seconds * gap_multiplier
    )

anomaly_types = []

if return_anomaly:
    anomaly_types.append(
        "PRICE_RETURN_SPIKE"
    )

if range_anomaly:
    anomaly_types.append(
        "ABNORMAL_CANDLE_RANGE"
    )

if volume_anomaly:
    anomaly_types.append(
        "ABNORMAL_VOLUME"
    )

if gap_anomaly:
    anomaly_types.append(
        "UNEXPECTED_DATA_GAP"
    )

if (
    return_anomaly
    and range_anomaly
    and volume_anomaly
):
    anomaly_types.append(
        "COMBINED_PRICE_VOLUME_VOLATILITY_SHOCK"
    )

if not anomaly_types:
    status = "NORMAL"
elif gap_anomaly and len(anomaly_types) == 1:
    status = "DATA_QUALITY_WARNING"
else:
    status = "ANOMALY_DETECTED"

return {
    "timestamp": current["timestamp"].isoformat(),
    "status": status,
    "anomaly_types": anomaly_types,
    "close": current["close"],
    "return_percent": current_return,
    "range_percent": current["range_percent"],
    "volume": current["volume"],
    "elapsed_since_previous_seconds": elapsed_seconds,
    "statistics": {
        "return": return_stats,
        "candle_range": range_stats,
        "volume": volume_stats,
    },
    "thresholds": {
        "return": return_threshold,
        "range": range_threshold,
        "volume": volume_threshold,
        "minimum_absolute_return_percent": (
            minimum_absolute_return_percent
        ),
    },
    "baseline_observations": len(historical),
}

def analyze_anomalies(
observations: Sequence[Mapping[str, Any]],
baseline_window: int = DEFAULT_BASELINE_WINDOW,
return_threshold: float = DEFAULT_RETURN_THRESHOLD,
range_threshold: float = DEFAULT_RANGE_THRESHOLD,
volume_threshold: float = DEFAULT_VOLUME_THRESHOLD,
minimum_absolute_return_percent: float = (
DEFAULT_MINIMUM_ABSOLUTE_RETURN_PERCENT
),
expected_interval_seconds: int | None = None,
gap_multiplier: float = 2.0,
include_normal_observations: bool = False,
) -> dict[str, Any]:

if (
    isinstance(baseline_window, bool)
    or not isinstance(baseline_window, int)
    or not 10 <= baseline_window <= 1000
):
    raise AnomalyAnalysisError(
        "baseline_window must be between 10 and 1000."
    )

thresholds = {
    "return_threshold": return_threshold,
    "range_threshold": range_threshold,
    "volume_threshold": volume_threshold,
}

for name, value in thresholds.items():

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
    ):
        raise AnomalyAnalysisError(
            f"{name} must be positive and finite."
        )

if (
    isinstance(minimum_absolute_return_percent, bool)
    or not isinstance(minimum_absolute_return_percent, (int, float))
    or not isfinite(minimum_absolute_return_percent)
    or minimum_absolute_return_percent < 0
):
    raise AnomalyAnalysisError(
        "minimum_absolute_return_percent must be non-negative."
    )

if expected_interval_seconds is not None:

    if (
        isinstance(expected_interval_seconds, bool)
        or not isinstance(expected_interval_seconds, int)
        or expected_interval_seconds <= 0
    ):
        raise AnomalyAnalysisError(
            "expected_interval_seconds must be a positive integer."
        )

if (
    isinstance(gap_multiplier, bool)
    or not isinstance(gap_multiplier, (int, float))
    or not isfinite(gap_multiplier)
    or gap_multiplier <= 1.0
):
    raise AnomalyAnalysisError(
        "gap_multiplier must be greater than 1."
    )

if not isinstance(include_normal_observations, bool):
    raise AnomalyAnalysisError(
        "include_normal_observations must be boolean."
    )

validated = _validate_observations(
    observations
)

results = []

for index in range(1, len(validated)):

    result = _analyze_observation(
        validated,
        index,
        baseline_window,
        float(return_threshold),
        float(range_threshold),
        float(volume_threshold),
        float(minimum_absolute_return_percent),
        expected_interval_seconds,
        float(gap_multiplier),
    )

    if (
        include_normal_observations
        or result["status"] != "NORMAL"
    ):
        results.append(result)

detected = [
    item for item in results
    if item["status"] == "ANOMALY_DETECTED"
]

data_warnings = [
    item for item in results
    if item["status"] == "DATA_QUALITY_WARNING"
]

type_counts = {}

for item in detected:

    for anomaly_type in item["anomaly_types"]:

        type_counts[anomaly_type] = (
            type_counts.get(anomaly_type, 0) + 1
        )

total_analyzed = len(validated) - 1

return {
    "module": "anomaly_detection_intelligence",
    "symbol": SUPPORTED_SYMBOL,
    "data_available": True,
    "observations_received": len(validated),
    "observations_analyzed": total_analyzed,
    "anomalies_detected": len(detected),
    "data_quality_warnings": len(data_warnings),
    "normal_observations": (
        total_analyzed
        - len(detected)
        - len(data_warnings)
    ),
    "anomaly_rate_percent": (
        len(detected) / total_analyzed * 100.0
        if total_analyzed > 0
        else 0.0
    ),
    "anomaly_type_counts": type_counts,
    "settings": {
        "baseline_window": baseline_window,
        "return_threshold": return_threshold,
        "range_threshold": range_threshold,
        "volume_threshold": volume_threshold,
        "minimum_absolute_return_percent": (
            minimum_absolute_return_percent
        ),
        "expected_interval_seconds": expected_interval_seconds,
        "gap_multiplier": gap_multiplier,
    },
    "results": results,
    "limitations": [
        "The first observation is used only as a return reference.",
        "Each observation is compared with preceding observations only.",
        "Robust Z-scores are statistical screening measures, not calibrated probabilities.",
        "Constant historical baselines can produce very large anomaly scores.",
        "Volume anomalies depend on consistent volume units and data sources.",
        "Unexpected gaps depend on the configured expected interval.",
        "Market closures and scheduled trading breaks must be handled by the caller.",
        "The detector does not identify the fundamental cause of an anomaly.",
        "An anomaly does not imply a buy or sell signal.",
        "This module does not fetch market data or execute trading orders.",
    ],
}
