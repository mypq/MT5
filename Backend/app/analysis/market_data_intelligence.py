"""
GOLD BOT - Market Data Intelligence

Central market-data validation and quality assessment.

Capabilities:

- OHLCV validation
- Timestamp and timezone validation
- Duplicate and ordering detection
- Missing interval detection
- Stale data detection
- Price consistency checks
- Volume validation
- Source metadata validation
- Data quality scoring

IMPORTANT:

- This module does not fetch market data.
- Source authenticity must be verified by the provider adapter.
- A quality score is not proof that market data is genuine.
- Invalid data must not be silently repaired.
  """

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite
from typing import Any

MAXIMUM_OBSERVATIONS = 100000

SUPPORTED_SYMBOLS = {
"XAUUSD",
}

QUALITY_THRESHOLDS = {
"EXCELLENT": 95.0,
"GOOD": 85.0,
"ACCEPTABLE": 70.0,
}

class MarketDataValidationError(ValueError):
"""Raised when market-data input is invalid."""

def _number(value: Any, field: str) -> float:

if isinstance(value, bool):
    raise MarketDataValidationError(
        f"{field} must be numeric."
    )

try:
    result = float(value)
except (TypeError, ValueError):
    raise MarketDataValidationError(
        f"{field} must be numeric."
    )

if not isfinite(result):
    raise MarketDataValidationError(
        f"{field} must be finite."
    )

return result

def _parse_timestamp(
value: Any,
field: str,
) -> datetime:

if not isinstance(value, str):
    raise MarketDataValidationError(
        f"{field} must be an ISO-8601 timestamp."
    )

try:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
except ValueError:
    raise MarketDataValidationError(
        f"{field} is invalid."
    )

if parsed.tzinfo is None:
    raise MarketDataValidationError(
        f"{field} must include a timezone."
    )

return parsed.astimezone(timezone.utc)

def _validate_symbol(symbol: str) -> str:

if not isinstance(symbol, str):
    raise MarketDataValidationError(
        "Symbol must be a string."
    )

normalized = symbol.strip().upper()

if not normalized:
    raise MarketDataValidationError(
        "Symbol cannot be empty."
    )

return normalized

def _validate_candles(
observations: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:

if (
    isinstance(observations, (str, bytes))
    or not isinstance(observations, Sequence)
):
    raise MarketDataValidationError(
        "Observations must be a sequence."
    )

if not (
    1 <= len(observations) <= MAXIMUM_OBSERVATIONS
):
    raise MarketDataValidationError(
        "Invalid observation count."
    )

valid = []
issues = []

previous_timestamp = None

for index, item in enumerate(observations):

    if not isinstance(item, Mapping):
        raise MarketDataValidationError(
            f"Observation {index} must be a mapping."
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
        issues.append({
            "index": index,
            "type": "NON_POSITIVE_PRICE",
        })

    if high < low:
        issues.append({
            "index": index,
            "type": "INVALID_HIGH_LOW",
        })

    if not low <= open_price <= high:
        issues.append({
            "index": index,
            "type": "OPEN_OUTSIDE_RANGE",
        })

    if not low <= close <= high:
        issues.append({
            "index": index,
            "type": "CLOSE_OUTSIDE_RANGE",
        })

    if volume < 0:
        issues.append({
            "index": index,
            "type": "NEGATIVE_VOLUME",
        })

    if previous_timestamp is not None:

        if timestamp == previous_timestamp:
            issues.append({
                "index": index,
                "type": "DUPLICATE_TIMESTAMP",
            })

        elif timestamp < previous_timestamp:
            issues.append({
                "index": index,
                "type": "OUT_OF_ORDER_TIMESTAMP",
            })

    previous_timestamp = timestamp

    valid.append({
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })

return valid, issues

def _detect_gaps(
candles: Sequence[dict[str, Any]],
expected_interval_seconds: int,
gap_tolerance_seconds: int,
) -> list[dict[str, Any]]:

gaps = []

for index in range(1, len(candles)):

    previous = candles[index - 1]
    current = candles[index]

    elapsed = (
        current["timestamp"]
        - previous["timestamp"]
    ).total_seconds()

    if elapsed <= 0:
        continue

    missing_intervals = max(
        0,
        int(
            (elapsed - gap_tolerance_seconds)
            // expected_interval_seconds
        ),
    )

    if missing_intervals > 0:

        gaps.append({
            "previous_timestamp": (
                previous["timestamp"].isoformat()
            ),
            "current_timestamp": (
                current["timestamp"].isoformat()
            ),
            "elapsed_seconds": elapsed,
            "expected_interval_seconds": (
                expected_interval_seconds
            ),
            "estimated_missing_intervals": (
                missing_intervals
            ),
        })

return gaps

def _detect_staleness(
candles: Sequence[dict[str, Any]],
reference_time: datetime,
maximum_age_seconds: int,
) -> dict[str, Any]:

latest = candles[-1]["timestamp"]

age_seconds = (
    reference_time - latest
).total_seconds()

return {
    "latest_timestamp": latest.isoformat(),
    "reference_time": reference_time.isoformat(),
    "age_seconds": age_seconds,
    "maximum_age_seconds": maximum_age_seconds,
    "is_stale": (
        age_seconds > maximum_age_seconds
    ),
    "is_future_dated": age_seconds < 0,
}

def _quality_score(
total: int,
invalid_count: int,
gap_count: int,
stale: bool,
future_dated: bool,
source_verified: bool,
) -> float:

score = 100.0

if total > 0:
    invalid_penalty = min(
        70.0,
        invalid_count / total * 100.0,
    )

    score -= invalid_penalty

score -= min(
    20.0,
    gap_count * 2.0,
)

if stale:
    score -= 20.0

if future_dated:
    score -= 30.0

if not source_verified:
    score -= 10.0

return round(
    max(0.0, min(100.0, score)),
    2,
)

def assess_market_data(
observations: Sequence[Mapping[str, Any]],
symbol: str,
source: str,
reference_time: str,
expected_interval_seconds: int,
maximum_age_seconds: int,
gap_tolerance_seconds: int = 0,
source_verified: bool = False,
) -> dict[str, Any]:

normalized_symbol = _validate_symbol(
    symbol
)

if not isinstance(source, str) or not source.strip():
    raise MarketDataValidationError(
        "A non-empty source identifier is required."
    )

if (
    isinstance(expected_interval_seconds, bool)
    or not isinstance(expected_interval_seconds, int)
    or expected_interval_seconds <= 0
):
    raise MarketDataValidationError(
        "Expected interval must be a positive integer."
    )

if (
    isinstance(maximum_age_seconds, bool)
    or not isinstance(maximum_age_seconds, int)
    or maximum_age_seconds <= 0
):
    raise MarketDataValidationError(
        "Maximum age must be a positive integer."
    )

if (
    isinstance(gap_tolerance_seconds, bool)
    or not isinstance(gap_tolerance_seconds, int)
    or gap_tolerance_seconds < 0
):
    raise MarketDataValidationError(
        "Gap tolerance cannot be negative."
    )

if not isinstance(source_verified, bool):
    raise MarketDataValidationError(
        "source_verified must be boolean."
    )

reference = _parse_timestamp(
    reference_time,
    "reference_time",
)

candles, validation_issues = _validate_candles(
    observations
)

gaps = _detect_gaps(
    candles,
    expected_interval_seconds,
    gap_tolerance_seconds,
)

staleness = _detect_staleness(
    candles,
    reference,
    maximum_age_seconds,
)

critical_issues = [
    issue for issue in validation_issues
    if issue["type"] in {
        "NON_POSITIVE_PRICE",
        "INVALID_HIGH_LOW",
        "OPEN_OUTSIDE_RANGE",
        "CLOSE_OUTSIDE_RANGE",
        "NEGATIVE_VOLUME",
        "DUPLICATE_TIMESTAMP",
        "OUT_OF_ORDER_TIMESTAMP",
    }
]

score = _quality_score(
    len(candles),
    len(critical_issues),
    len(gaps),
    staleness["is_stale"],
    staleness["is_future_dated"],
    source_verified,
)

if critical_issues or staleness["is_future_dated"]:
    status = "INVALID"

elif staleness["is_stale"]:
    status = "STALE"

elif score >= QUALITY_THRESHOLDS["EXCELLENT"]:
    status = "EXCELLENT"

elif score >= QUALITY_THRESHOLDS["GOOD"]:
    status = "GOOD"

elif score >= QUALITY_THRESHOLDS["ACCEPTABLE"]:
    status = "ACCEPTABLE"

else:
    status = "POOR"

warnings = []

if not source_verified:
    warnings.append(
        "SOURCE_NOT_INDEPENDENTLY_VERIFIED"
    )

if gaps:
    warnings.append(
        "MISSING_INTERVALS_DETECTED"
    )

if staleness["is_stale"]:
    warnings.append(
        "STALE_MARKET_DATA"
    )

if staleness["is_future_dated"]:
    warnings.append(
        "FUTURE_DATED_MARKET_DATA"
    )

if critical_issues:
    warnings.append(
        "INVALID_CANDLE_DATA"
    )

can_be_used_for_analysis = (
    status in {
        "EXCELLENT",
        "GOOD",
        "ACCEPTABLE",
    }
    and source_verified
)

return {
    "module": "market_data_intelligence",
    "symbol": normalized_symbol,
    "source": source.strip(),
    "source_verified": source_verified,
    "observations_received": len(candles),
    "quality_score": score,
    "quality_status": status,
    "can_be_used_for_analysis": (
        can_be_used_for_analysis
    ),
    "latest_data": staleness,
    "expected_interval_seconds": (
        expected_interval_seconds
    ),
    "validation_issues": validation_issues,
    "missing_intervals": gaps,
    "warnings": warnings,
    "limitations": [
        "Source verification must be performed by a trusted provider adapter.",
        "This module does not fetch prices from MT5.",
        "A timestamp gap may represent a market closure or scheduled break.",
        "The estimated missing interval count is not a reconstructed candle count.",
        "A quality score is a screening metric, not a probability of data correctness.",
        "The caller must supply a correct reference time and expected interval.",
        "Broker-specific symbol mapping must be handled by the data adapter.",
    ],
      }
