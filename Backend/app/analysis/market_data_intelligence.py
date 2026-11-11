"""
GOLD BOT - Market Data Intelligence

Central market-data validation and quality assessment.

Responsibilities:
- Validate OHLCV observations.
- Validate timestamps and chronological ordering.
- Detect duplicate timestamps and missing intervals.
- Separate gaps explained by market closures from unexplained gaps.
- Detect stale and future-dated data.
- Flag suspected price spikes (robust statistics, warning only).
- Validate symbol and source metadata.
- Separate critical errors from quality warnings.
- Fail closed when data is not trusted.

Timestamp convention: each candle timestamp is the candle OPEN time.

This module does not fetch market data.
Source authenticity must be established by a trusted provider adapter.
A caller-provided source_verified flag is not itself proof of authenticity.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import isfinite, log
from numbers import Real
from statistics import median
from typing import Any


MAXIMUM_OBSERVATIONS = 100000
MAXIMUM_FUTURE_SKEW_SECONDS = 300

SPIKE_ROBUST_Z_THRESHOLD = 10.0
MINIMUM_RETURNS_FOR_SPIKE_TEST = 30
MAD_TO_SIGMA = 1.4826

SUPPORTED_SYMBOLS = {
    "XAUUSD",
}

# Optional allow-list maintained by the trusted provider adapter layer.
# Empty set = no allow-list enforcement (previous behaviour).
TRUSTED_SOURCES: set = set()

QUALITY_THRESHOLDS = {
    "EXCELLENT": 95.0,
    "GOOD": 85.0,
    "ACCEPTABLE": 70.0,
}

INVALID_SCORE_CEILING = 49.0


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
    # Strings are rejected on purpose: float("1_0") == 10.0 in Python.
    if isinstance(value, bool) or not isinstance(
        value, (Real, Decimal)
    ):
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


def _parse_closure_windows(
    windows: Any,
) -> list[tuple[datetime, datetime]]:
    if windows is None:
        return []

    if isinstance(windows, (str, bytes)) or not isinstance(
        windows, Sequence
    ):
        raise MarketDataValidationError(
            "closure_windows must be a sequence of (start, end)."
        )

    parsed = []

    for index, window in enumerate(windows):
        if (
            isinstance(window, (str, bytes))
            or not isinstance(window, Sequence)
            or len(window) != 2
        ):
            raise MarketDataValidationError(
                f"closure_windows[{index}] must be (start, end)."
            )

        start = _parse_timestamp(
            window[0], f"closure_windows[{index}].start"
        )
        end = _parse_timestamp(
            window[1], f"closure_windows[{index}].end"
        )

        if end <= start:
            raise MarketDataValidationError(
                f"closure_windows[{index}] end must follow start."
            )

        parsed.append((start, end))

    return parsed


def _issue(
    index: int,
    timestamp: datetime,
    issue_type: str,
) -> dict[str, Any]:
    return {
        "index": index,
        "type": issue_type,
        "timestamp": timestamp.isoformat(),
    }


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

    candles = []
    issues = []
    seen_timestamps = set()
    latest_seen = None

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

        raw_volume = item.get("volume")

        if raw_volume is None:
            volume = 0.0
            issues.append(
                _issue(index, timestamp, "MISSING_VOLUME")
            )
        else:
            volume = _number(
                raw_volume,
                f"observations[{index}].volume",
            )

        if min(open_price, high, low, close) <= 0:
            issues.append(
                _issue(index, timestamp, "NON_POSITIVE_PRICE")
            )

        if high < low:
            issues.append(
                _issue(index, timestamp, "INVALID_HIGH_LOW")
            )
        else:
            if not low <= open_price <= high:
                issues.append(
                    _issue(
                        index, timestamp, "OPEN_OUTSIDE_RANGE"
                    )
                )

            if not low <= close <= high:
                issues.append(
                    _issue(
                        index, timestamp, "CLOSE_OUTSIDE_RANGE"
                    )
                )

        if volume < 0:
            issues.append(
                _issue(index, timestamp, "NEGATIVE_VOLUME")
            )

        if timestamp > reference:
            issues.append(
                _issue(index, timestamp, "FUTURE_TIMESTAMP")
            )

        if timestamp in seen_timestamps:
            issues.append(
                _issue(index, timestamp, "DUPLICATE_TIMESTAMP")
            )

        # Compare with the latest timestamp seen so far, not
        # only the previous row.
        if latest_seen is not None and timestamp < latest_seen:
            issues.append(
                _issue(
                    index, timestamp, "OUT_OF_ORDER_TIMESTAMP"
                )
            )

        seen_timestamps.add(timestamp)

        if latest_seen is None or timestamp > latest_seen:
            latest_seen = timestamp

        candles.append({
            "timestamp": timestamp,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })

    return candles, issues


def _detect_gaps(
    candles: Sequence[dict[str, Any]],
    expected_interval_seconds: int,
    gap_tolerance_seconds: int,
    closure_windows: Sequence[tuple[datetime, datetime]],
) -> list[dict[str, Any]]:

    gaps = []
    timestamps = sorted({c["timestamp"] for c in candles})

    for previous, current in zip(timestamps, timestamps[1:]):
        elapsed = (current - previous).total_seconds()

        if elapsed <= (
            expected_interval_seconds + gap_tolerance_seconds
        ):
            continue

        estimated_missing = (
            int(elapsed / expected_interval_seconds + 0.5) - 1
        )

        if estimated_missing <= 0:
            continue

        expected_next = previous + timedelta(
            seconds=expected_interval_seconds
        )

        explained = any(
            start <= expected_next and end >= current
            for start, end in closure_windows
        )

        gaps.append({
            "previous_timestamp": previous.isoformat(),
            "current_timestamp": current.isoformat(),
            "elapsed_seconds": elapsed,
            "expected_interval_seconds": (
                expected_interval_seconds
            ),
            "estimated_missing_intervals": estimated_missing,
            "explained_by_closure": explained,
        })

    return gaps


def _detect_price_spikes(
    candles: Sequence[dict[str, Any]],
    expected_interval_seconds: int,
) -> dict[str, Any]:

    ordered = sorted(candles, key=lambda c: c["timestamp"])
    returns = []

    for previous, current in zip(ordered, ordered[1:]):
        if previous["close"] <= 0 or current["close"] <= 0:
            continue

        elapsed = (
            current["timestamp"] - previous["timestamp"]
        ).total_seconds()

        if not 0 < elapsed <= 1.5 * expected_interval_seconds:
            continue

        returns.append((
            current["timestamp"],
            log(current["close"] / previous["close"]),
        ))

    if len(returns) < MINIMUM_RETURNS_FOR_SPIKE_TEST:
        return {"tested": False, "spikes": []}

    values = [value for _, value in returns]
    center = median(values)
    mad = median(abs(value - center) for value in values)

    if mad == 0:
        return {"tested": False, "spikes": []}

    sigma = MAD_TO_SIGMA * mad
    spikes = []

    for timestamp, value in returns:
        robust_z = (value - center) / sigma

        if abs(robust_z) > SPIKE_ROBUST_Z_THRESHOLD:
            spikes.append({
                "timestamp": timestamp.isoformat(),
                "log_return": value,
                "robust_z": round(robust_z, 2),
            })

    return {"tested": True, "spikes": spikes}


def _detect_staleness(
    candles: Sequence[dict[str, Any]],
    reference_time: datetime,
    maximum_age_seconds: int,
    expected_interval_seconds: int,
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
        "latest_candle_closed": (
            age_seconds >= expected_interval_seconds
        ),
    }


def _quality_score(
    total: int,
    critical_count: int,
    missing_count: int,
    stale: bool,
    future_dated: bool,
    source_verified: bool,
) -> float:
    """Heuristic score. Weights are not statistically calibrated."""

    if total <= 0:
        return 0.0

    score = 100.0

    score -= min(
        70.0,
        critical_count / total * 100.0,
    )

    missing_ratio = missing_count / (total + missing_count)
    score -= min(20.0, missing_ratio * 200.0)

    if stale:
        score -= 20.0

    if future_dated:
        score -= 30.0

    if not source_verified:
        score -= 10.0

    if critical_count > 0 or future_dated:
        score = min(score, INVALID_SCORE_CEILING)

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
    closure_windows: Sequence[Any] | None = None,
    max_reference_lag_seconds: int | None = None,
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

    if max_reference_lag_seconds is not None:
        max_reference_lag_seconds = _positive_integer(
            max_reference_lag_seconds,
            "max_reference_lag_seconds",
        )

    closures = _parse_closure_windows(closure_windows)

    reference = _parse_timestamp(
        reference_time,
        "reference_time",
    )

    system_time = datetime.now(timezone.utc)
    drift_seconds = (reference - system_time).total_seconds()

    if drift_seconds > MAXIMUM_FUTURE_SKEW_SECONDS:
        raise MarketDataValidationError(
            "reference_time is too far in the future."
        )

    if (
        max_reference_lag_seconds is not None
        and -drift_seconds > max_reference_lag_seconds
    ):
        raise MarketDataValidationError(
            "reference_time lags the system clock too much."
        )

    candles, validation_issues = _validate_candles(
        observations,
        reference,
    )

    gaps = _detect_gaps(
        candles,
        expected_interval_seconds,
        gap_tolerance_seconds,
        closures,
    )

    unexplained_gaps = [
        gap for gap in gaps if not gap["explained_by_closure"]
    ]
    unexplained_missing = sum(
        gap["estimated_missing_intervals"]
        for gap in unexplained_gaps
    )

    spike_result = _detect_price_spikes(
        candles,
        expected_interval_seconds,
    )

    staleness = _detect_staleness(
        candles,
        reference,
        maximum_age_seconds,
        expected_interval_seconds,
    )

    source_verified_effective = source_verified and (
        not TRUSTED_SOURCES
        or normalized_source in TRUSTED_SOURCES
    )

    critical_issues = [
        issue
        for issue in validation_issues
        if issue["type"] in CRITICAL_ISSUES
    ]

    missing_volume_count = sum(
        1
        for issue in validation_issues
        if issue["type"] == "MISSING_VOLUME"
    )

    score = _quality_score(
        total=len(candles),
        critical_count=len(critical_issues),
        missing_count=unexplained_missing,
        stale=staleness["is_stale"],
        future_dated=staleness["is_future_dated"],
        source_verified=source_verified_effective,
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

    if not source_verified_effective:
        warnings.append(
            "SOURCE_NOT_INDEPENDENTLY_VERIFIED"
        )

    if unexplained_gaps:
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

    if missing_volume_count:
        warnings.append(
            "MISSING_VOLUME_DATA"
        )

    if spike_result["spikes"]:
        warnings.append(
            "PRICE_SPIKES_SUSPECTED"
        )

    if not staleness["latest_candle_closed"]:
        warnings.append(
            "LATEST_CANDLE_MAY_BE_INCOMPLETE"
        )

    blocking_reasons = []

    if critical_issues:
        blocking_reasons.append("CRITICAL_DATA_ISSUES")

    if staleness["is_stale"]:
        blocking_reasons.append("STALE_DATA")

    if staleness["is_future_dated"]:
        blocking_reasons.append("FUTURE_DATED_DATA")

    if not source_verified_effective:
        blocking_reasons.append("SOURCE_NOT_VERIFIED")

    if status == "POOR":
        blocking_reasons.append("QUALITY_BELOW_ACCEPTABLE")

    can_be_used_for_analysis = not blocking_reasons

    return {
        "module": "market_data_intelligence",
        "symbol": normalized_symbol,
        "source": normalized_source,
        "source_verified": source_verified_effective,
        "source_verified_by_caller": source_verified,
        "observations_received": len(candles),
        "quality_score": score,
        "quality_status": status,
        "can_be_used_for_analysis": (
            can_be_used_for_analysis
        ),
        "blocking_reasons": blocking_reasons,
        "latest_data": staleness,
        "reference_drift_seconds": drift_seconds,
        "expected_interval_seconds": (
            expected_interval_seconds
        ),
        "validation_issues": validation_issues,
        "critical_issue_count": len(critical_issues),
        "missing_intervals": gaps,
        "missing_interval_count": len(gaps),
        "unexplained_gap_count": len(unexplained_gaps),
        "unexplained_missing_intervals": unexplained_missing,
        "missing_volume_count": missing_volume_count,
        "price_spike_test_performed": spike_result["tested"],
        "suspected_price_spikes": spike_result["spikes"],
        "warnings": warnings,
        "trading_signal_allowed": False,
        "limitations": [
            "This module does not fetch market data.",
            "Source authenticity must be established by a trusted provider adapter.",
            "The source_verified argument must never be trusted directly from an untrusted client.",
            "A quality score is a heuristic and is not proof that data is genuine.",
            "Timestamps are assumed to be candle OPEN times.",
            "Gaps are explained only by closure_windows supplied by the caller.",
            "Missing interval counts are estimates, not reconstructed candles.",
            "Spike detection is statistical; real news moves can be flagged.",
            "Spot-gold volume is usually tick volume, not traded volume.",
            "The caller must provide the correct candle interval and reference time.",
            "Broker-specific symbol mapping must be handled by the provider adapter.",
            "This module does not authorize or execute trades.",
        ],
    }
