"""
GOLD BOT - Market Data Intelligence

Central market-data validation and quality assessment.

Responsibilities:
- Validate OHLCV observations.
- Validate timestamps and chronological ordering.
- Detect duplicate timestamps and missing intervals.
- Detect stale and future-dated data.
- Validate symbol and source metadata.
- Separate critical errors from quality warnings.
- Fail closed when data is not trusted.

This module does not fetch market data.
Source authenticity must be established by a trusted provider adapter.
A caller-provided source_verified flag is not itself proof of authenticity.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import ceil, isfinite
from typing import Any


MAXIMUM_OBSERVATIONS = 100000
MAXIMUM_FUTURE_SKEW_SECONDS = 300

SUPPORTED_SYMBOLS = {
    "XAUUSD",
}

QUALITY_THRESHOLDS = {
    "EXCELLENT": 95.0,
    "GOOD": 85.0,
    "ACCEPTABLE": 70.0,
}


CRITICAL_ISSUES = {
    "NON_POSITIVE_PRICE",
    "INVALID_HIGH_LOW",
    "OPEN_OUTSIDE_RANGE",
    "CLOSE_OUTSIDE_RANGE",
    "NEGATIVE_VOLUME",
    "DUPLICATE_TIMESTAMP",
    "OUT_OF_ORDER_TIMESTAMP",
    "FUTURE_TIMESTAMP",
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
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarketDataValidationError(
            f"{field} must be numeric."
        ) from exc

    if not isfinite(result):
        raise MarketDataValidationError(
            f"{field} must be finite."
        )

    return result


def _positive_integer(value: Any, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise MarketDataValidationError(
            f"{field} must be a positive integer."
        )

    return value


def _parse_timestamp(
    value: Any,
    field: str,
) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise MarketDataValidationError(
                f"{field} is not a valid ISO-8601 timestamp."
            ) from exc
    else:
        raise MarketDataValidationError(
            f"{field} must be an ISO-8601 timestamp."
        )

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketDataValidationError(
            f"{field} must include a timezone."
        )

    return parsed.astimezone(timezone.utc)


def _validate_symbol(symbol: Any) -> str:
    if not isinstance(symbol, str):
        raise MarketDataValidationError(
            "Symbol must be a string."
        )

    normalized = symbol.strip().upper()

    if normalized not in SUPPORTED_SYMBOLS:
        raise MarketDataValidationError(
            f"Unsupported symbol: {normalized or '<empty>'}."
        )

    return normalized


def _validate_source(source: Any) -> str:
    if not isinstance(source, str):
        raise MarketDataValidationError(
            "Source identifier must be a string."
        )

    normalized = source.strip()

    if not normalized:
        raise MarketDataValidationError(
            "A non-empty source identifier is required."
        )

    if len(normalized) > 128:
        raise MarketDataValidationError(
            "Source identifier is too long."
        )

    return normalized


def _validate_candles(
    observations: Sequence[Mapping[str, Any]],
    reference: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:

    if (
        isinstance(observations, (str, bytes))
        or not isinstance(observations, Sequence)
    ):
        raise MarketDataValidationError(
            "Observations must be a sequence of mappings."
        )

    if not 1 <= len(observations) <= MAXIMUM_OBSERVATIONS:
        raise MarketDataValidationError(
            "Invalid observation count."
        )

    valid = []
    issues = []
    seen_timestamps = set()
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

        def add_issue(issue_type: str) -> None:
            issues.append({
                "index": index,
                "type": issue_type,
                "timestamp": timestamp.isoformat(),
            })

        if min(open_price, high, low, close) <= 0:
            add_issue("NON_POSITIVE_PRICE")

        if high < low:
            add_issue("INVALID_HIGH_LOW")
        else:
            if not low <= open_price <= high:
                add_issue("OPEN_OUTSIDE_RANGE")

            if not low <= close <= high:
                add_issue("CLOSE_OUTSIDE_RANGE")

        if volume < 0:
            add_issue("NEGATIVE_VOLUME")

        if timestamp > reference:
            add_issue("FUTURE_TIMESTAMP")

        if timestamp in seen_timestamps:
            add_issue("DUPLICATE_TIMESTAMP")

        if (
            previous_timestamp is not None
            and timestamp < previous_timestamp
        ):
            add_issue("OUT_OF_ORDER_TIMESTAMP")

        seen_timestamps.add(timestamp)
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
            current["timestamp"] - previous["timestamp"]
        ).total_seconds()

        if elapsed <= 0:
            continue

        if elapsed <= (
            expected_interval_seconds + gap_tolerance_seconds
        ):
            continue

        estimated_missing = max(
            0,
            ceil(
                (
                    elapsed - gap_tolerance_seconds
                ) / expected_interval_seconds
            ) - 1,
        )

        if estimated_missing > 0:
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
                    estimated_missing
                ),
            })

    return gaps


def _detect_staleness(
    candles: Sequence[dict[str, Any]],
    reference_time: datetime,
    maximum_age_seconds: int,
) -> dict[str, Any]:

    latest = max(
        candle["timestamp"] for candle in candles
    )

    age_seconds = (
        reference_time - latest
    ).total_seconds()

    return {
        "latest_timestamp": latest.isoformat(),
        "reference_time": reference_time.isoformat(),
        "age_seconds": age_seconds,
        "maximum_age_seconds": maximum_age_seconds,
        "is_stale": age_seconds > maximum_age_seconds,
        "is_future_dated": age_seconds < 0,
    }


def _quality_score(
    total: int,
    critical_count: int,
    gap_count: int,
    stale: bool,
    future_dated: bool,
    source_verified: bool,
) -> float:

    if total <= 0:
        return 0.0

    score = 100.0

    score -= min(
        70.0,
        critical_count / total * 100.0,
    )

    score -= min(20.0, gap_count * 2.0)

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

    normalized_symbol = _validate_symbol(symbol)
    normalized_source = _validate_source(source)

    expected_interval_seconds = _positive_integer(
        expected_interval_seconds,
        "expected_interval_seconds",
    )

    maximum_age_seconds = _positive_integer(
        maximum_age_seconds,
        "maximum_age_seconds",
    )

    if (
        isinstance(gap_tolerance_seconds, bool)
        or not isinstance(gap_tolerance_seconds, int)
        or gap_tolerance_seconds < 0
    ):
        raise MarketDataValidationError(
            "gap_tolerance_seconds cannot be negative."
        )

    if not isinstance(source_verified, bool):
        raise MarketDataValidationError(
            "source_verified must be boolean."
        )

    reference = _parse_timestamp(
        reference_time,
        "reference_time",
    )

    system_time = datetime.now(timezone.utc)

    if (
        reference
        > system_time.replace(microsecond=0)
        .__class__(
            year=system_time.year,
            month=system_time.month,
            day=system_time.day,
            hour=system_time.hour,
            minute=system_time.minute,
            second=system_time.second,
            tzinfo=timezone.utc,
        )
    ):
        if (
            reference - system_time
        ).total_seconds() > MAXIMUM_FUTURE_SKEW_SECONDS:
            raise MarketDataValidationError(
                "reference_time is too far in the future."
            )

    candles, validation_issues = _validate_candles(
        observations,
        reference,
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
        issue
        for issue in validation_issues
        if issue["type"] in CRITICAL_ISSUES
    ]

    score = _quality_score(
        total=len(candles),
        critical_count=len(critical_issues),
        gap_count=len(gaps),
        stale=staleness["is_stale"],
        future_dated=staleness["is_future_dated"],
        source_verified=source_verified,
    )

    if critical_issues:
        status = "INVALID"
    elif staleness["is_future_dated"]:
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
        and not critical_issues
        and not staleness["is_stale"]
        and not staleness["is_future_dated"]
    )

    return {
        "module": "market_data_intelligence",
        "symbol": normalized_symbol,
        "source": normalized_source,
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
        "critical_issue_count": len(critical_issues),
        "missing_intervals": gaps,
        "missing_interval_count": len(gaps),
        "warnings": warnings,
        "trading_signal_allowed": False,
        "limitations": [
            "This module does not fetch market data.",
            "Source authenticity must be established by a trusted provider adapter.",
            "The source_verified argument must never be trusted directly from an untrusted client.",
            "A quality score is not proof that data is genuine.",
            "Timestamp gaps may represent market closures or scheduled breaks.",
            "Missing interval counts are estimates, not reconstructed candles.",
            "The caller must provide the correct candle interval and reference time.",
            "Broker-specific symbol mapping must be handled by the provider adapter.",
            "This module does not authorize or execute trades.",
        ],
    }
