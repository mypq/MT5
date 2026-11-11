"""
GOLD BOT - Execution Intelligence

Analyzes order execution quality and transaction costs.

Capabilities:
- Bid/ask spread analysis
- Historical rolling spread anomaly detection
- Adverse and favorable slippage
- Execution latency distribution
- Partial fill and cancellation analysis
- Rejection statistics
- Account-currency execution cost analysis
- Session-based execution profiling
- Duplicate execution detection
- Chronological data validation

IMPORTANT:
- This module does not send, modify, or close trading orders.
- It does not connect directly to MT5.
- Execution records must come from a trusted source.
- Monetary values must use a consistent account currency.
- Historical execution quality does not guarantee future quality.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from math import isfinite
from statistics import median
from typing import Any


MAXIMUM_EXECUTIONS = 100000
MINIMUM_SPREAD_BASELINE = 10
MAXIMUM_FUTURE_MINUTES = 5

DEFAULT_SPREAD_ANOMALY_MULTIPLIER = 2.0
DEFAULT_MAXIMUM_LATENCY_MS = 1000.0
DEFAULT_MAXIMUM_ADVERSE_SLIPPAGE_POINTS = 5.0

DEFAULT_LATENCY_PERCENTILES = (50, 75, 90, 95, 99)


class ExecutionAnalysisError(ValueError):
    """Raised when execution input is invalid."""


def _number(
    value: Any,
    field: str,
    optional: bool = False,
) -> float | None:
    if value is None and optional:
        return None

    if isinstance(value, bool):
        raise ExecutionAnalysisError(
            f"{field} must be numeric."
        )

    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExecutionAnalysisError(
            f"{field} must be numeric."
        ) from exc

    if not isfinite(result):
        raise ExecutionAnalysisError(
            f"{field} must be finite."
        )

    return result


def _timestamp(
    value: Any,
    field: str,
    now: datetime,
) -> datetime:
    if isinstance(value, datetime):
        result = value

    elif isinstance(value, str):
        normalized = (
            value[:-1] + "+00:00"
            if value.endswith("Z")
            else value
        )

        try:
            result = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ExecutionAnalysisError(
                f"{field} is invalid."
            ) from exc

    else:
        raise ExecutionAnalysisError(
            f"{field} must be an ISO-8601 timestamp."
        )

    if result.tzinfo is None or result.utcoffset() is None:
        raise ExecutionAnalysisError(
            f"{field} must include a timezone."
        )

    result = result.astimezone(timezone.utc)

    if result > now + timedelta(
        minutes=MAXIMUM_FUTURE_MINUTES
    ):
        raise ExecutionAnalysisError(
            f"{field} is too far in the future."
        )

    return result


def _percentile(
    values: Sequence[float],
    percentage: float,
) -> float | None:
    if not values:
        return None

    ordered = sorted(values)

    position = (
        len(ordered) - 1
    ) * percentage / 100.0

    lower = int(position)
    upper = min(
        lower + 1,
        len(ordered) - 1,
    )

    fraction = position - lower

    return (
        ordered[lower] * (1.0 - fraction)
        + ordered[upper] * fraction
    )


def _validate_execution(
    record: Mapping[str, Any],
    index: int,
    now: datetime,
) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ExecutionAnalysisError(
            f"Execution {index} must be a mapping."
        )

    status = str(
        record.get("status", "")
    ).strip().upper()

    allowed_statuses = {
        "FILLED",
        "PARTIALLY_FILLED",
        "REJECTED",
        "CANCELLED",
        "EXPIRED",
    }

    if status not in allowed_statuses:
        raise ExecutionAnalysisError(
            f"Invalid execution status at index {index}."
        )

    timestamp = _timestamp(
        record.get("timestamp"),
        f"executions[{index}].timestamp",
        now,
    )

    symbol = str(
        record.get("symbol", "")
    ).strip().upper()

    if not symbol:
        raise ExecutionAnalysisError(
            f"Missing symbol at index {index}."
        )

    side = str(
        record.get("side", "")
    ).strip().upper()

    if side not in {"BUY", "SELL"}:
        raise ExecutionAnalysisError(
            f"Invalid side at index {index}."
        )

    execution_id_value = record.get("execution_id")

    execution_id = (
        str(execution_id_value).strip()
        if execution_id_value is not None
        else None
    )

    if execution_id == "":
        raise ExecutionAnalysisError(
            f"Execution {index} has an empty execution_id."
        )

    requested_volume = _number(
        record.get("requested_volume"),
        f"executions[{index}].requested_volume",
    )

    filled_volume = _number(
        record.get("filled_volume", 0),
        f"executions[{index}].filled_volume",
    )

    if requested_volume <= 0:
        raise ExecutionAnalysisError(
            "Requested volume must be positive."
        )

    if not 0 <= filled_volume <= requested_volume:
        raise ExecutionAnalysisError(
            "Filled volume must be between zero and requested volume."
        )

    requested_price = _number(
        record.get("requested_price"),
        f"executions[{index}].requested_price",
        optional=True,
    )

    executed_price = _number(
        record.get("executed_price"),
        f"executions[{index}].executed_price",
        optional=True,
    )

    bid = _number(
        record.get("bid"),
        f"executions[{index}].bid",
        optional=True,
    )

    ask = _number(
        record.get("ask"),
        f"executions[{index}].ask",
        optional=True,
    )

    point_size = _number(
        record.get("point_size"),
        f"executions[{index}].point_size",
        optional=True,
    )

    latency = _number(
        record.get("latency_ms"),
        f"executions[{index}].latency_ms",
        optional=True,
    )

    commission = _number(
        record.get("commission", 0),
        f"executions[{index}].commission",
    )

    swap = _number(
        record.get("swap", 0),
        f"executions[{index}].swap",
    )

    slippage_cost = _number(
        record.get("slippage_cost", 0),
        f"executions[{index}].slippage_cost",
    )

    spread_cost = _number(
        record.get("spread_cost", 0),
        f"executions[{index}].spread_cost",
    )

    if commission < 0:
        raise ExecutionAnalysisError(
            "Commission cannot be negative."
        )

    if latency is not None and latency < 0:
        raise ExecutionAnalysisError(
            "Latency cannot be negative."
        )

    if slippage_cost < 0 or spread_cost < 0:
        raise ExecutionAnalysisError(
            "Spread and slippage costs cannot be negative."
        )

    if (bid is None) != (ask is None):
        raise ExecutionAnalysisError(
            "Bid and ask must be provided together."
        )

    if bid is not None:
        if bid <= 0 or ask <= 0 or ask < bid:
            raise ExecutionAnalysisError(
                "Invalid bid/ask prices."
            )

    if point_size is not None and point_size <= 0:
        raise ExecutionAnalysisError(
            "Point size must be positive."
        )

    if requested_price is not None and requested_price <= 0:
        raise ExecutionAnalysisError(
            "Requested price must be positive."
        )

    if executed_price is not None and executed_price <= 0:
        raise ExecutionAnalysisError(
            "Executed price must be positive."
        )

    if status == "FILLED" and filled_volume != requested_volume:
        raise ExecutionAnalysisError(
            "FILLED execution must contain the full requested volume."
        )

    if status == "PARTIALLY_FILLED":
        if not 0 < filled_volume < requested_volume:
            raise ExecutionAnalysisError(
                "PARTIALLY_FILLED must contain a partial volume."
            )

    if status in {"REJECTED", "EXPIRED"} and filled_volume != 0:
        raise ExecutionAnalysisError(
            "Rejected or expired records cannot contain filled volume."
        )

    # CANCELLED may contain an earlier partial fill.
    # The caller must provide the actual cumulative filled volume.
    if filled_volume > 0:
        if executed_price is None:
            raise ExecutionAnalysisError(
                "Executed price is required for filled volume."
            )

        if requested_price is None:
            raise ExecutionAnalysisError(
                "Requested price is required for slippage analysis."
            )

        if point_size is None:
            raise ExecutionAnalysisError(
                "Point size is required for slippage analysis."
            )

    if bid is not None and point_size is None:
        raise ExecutionAnalysisError(
            "Point size is required when bid/ask spread is supplied."
        )

    if filled_volume > 0:
        raw_slippage = (
            executed_price - requested_price
            if side == "BUY"
            else requested_price - executed_price
        )

        slippage_points = raw_slippage / point_size

        adverse_slippage_points = max(
            slippage_points,
            0.0,
        )

        favorable_slippage_points = max(
            -slippage_points,
            0.0,
        )

    else:
        slippage_points = None
        adverse_slippage_points = 0.0
        favorable_slippage_points = 0.0

    spread_points = None

    if bid is not None:
        spread_points = (
            (ask - bid) / point_size
        )

    currency = str(
        record.get("currency", "")
    ).strip().upper()

    if any(
        value != 0
        for value in (
            commission,
            swap,
            slippage_cost,
            spread_cost,
        )
    ) and not currency:
        raise ExecutionAnalysisError(
            "A currency is required when monetary costs are supplied."
        )

    session = str(
        record.get("session", "UNKNOWN")
    ).strip().upper() or "UNKNOWN"

    if len(session) > 64:
        raise ExecutionAnalysisError(
            "Session label is too long."
        )

    rejection_reason = str(
        record.get("rejection_reason", "")
    ).strip() or None

    return {
        "execution_id": execution_id,
        "timestamp": timestamp,
        "symbol": symbol,
        "side": side,
        "status": status,
        "requested_volume": requested_volume,
        "filled_volume": filled_volume,
        "unfilled_volume": (
            requested_volume - filled_volume
        ),
        "requested_price": requested_price,
        "executed_price": executed_price,
        "bid": bid,
        "ask": ask,
        "point_size": point_size,
        "spread_points": spread_points,
        "latency_ms": latency,
        "slippage_points": slippage_points,
        "adverse_slippage_points": adverse_slippage_points,
        "favorable_slippage_points": favorable_slippage_points,
        "commission": commission,
        "swap": swap,
        "spread_cost": spread_cost,
        "slippage_cost": slippage_cost,
        "currency": currency or None,
        "rejection_reason": rejection_reason,
        "session": session,
    }


def _safe_average(
    values: Sequence[float],
) -> float | None:
    if not values:
        return None

    return sum(values) / len(values)


def _rolling_spread_analysis(
    executions: Sequence[dict[str, Any]],
    anomaly_multiplier: float,
) -> tuple[
    list[dict[str, Any]],
    dict[str, int],
]:
    """
    Compare each spread only with earlier observations
    from the same symbol and session.
    """
    history: dict[
        tuple[str, str],
        list[float],
    ] = {}

    anomalies = []
    baseline_counts = {}

    for item in executions:
        if item["status"] not in {
            "FILLED",
            "PARTIALLY_FILLED",
            "CANCELLED",
        }:
            continue

        spread = item["spread_points"]

        if spread is None:
            continue

        key = (
            item["symbol"],
            item["session"],
        )

        previous = history.setdefault(
            key,
            [],
        )

        if len(previous) >= MINIMUM_SPREAD_BASELINE:
            baseline = median(previous)

            is_anomaly = (
                spread > baseline * anomaly_multiplier
                if baseline > 0
                else spread > 0
            )

            if is_anomaly:
                anomalies.append({
                    "execution_id": item["execution_id"],
                    "timestamp": item["timestamp"].isoformat(),
                    "symbol": item["symbol"],
                    "session": item["session"],
                    "spread_points": spread,
                    "historical_median_spread_points": baseline,
                    "anomaly_multiplier": anomaly_multiplier,
                })

        previous.append(spread)

        baseline_counts[
            f"{key[0]}:{key[1]}"
        ] = len(previous)

    return anomalies, baseline_counts


def _latency_statistics(
    values: Sequence[float],
) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "minimum_ms": None,
            "average_ms": None,
            "median_ms": None,
            "p75_ms": None,
            "p90_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "maximum_ms": None,
        }

    return {
        "count": len(values),
        "minimum_ms": min(values),
        "average_ms": _safe_average(values),
        "median_ms": median(values),
        "p75_ms": _percentile(values, 75),
        "p90_ms": _percentile(values, 90),
        "p95_ms": _percentile(values, 95),
        "p99_ms": _percentile(values, 99),
        "maximum_ms": max(values),
    }


def analyze_execution_quality(
    executions: Sequence[Mapping[str, Any]],
    maximum_latency_ms: float = DEFAULT_MAXIMUM_LATENCY_MS,
    maximum_adverse_slippage_points: float = (
        DEFAULT_MAXIMUM_ADVERSE_SLIPPAGE_POINTS
    ),
    spread_anomaly_multiplier: float = (
        DEFAULT_SPREAD_ANOMALY_MULTIPLIER
    ),
    now: datetime | None = None,
) -> dict[str, Any]:
    if (
        isinstance(executions, (str, bytes))
        or not isinstance(executions, Sequence)
    ):
        raise ExecutionAnalysisError(
            "Executions must be a sequence."
        )

    if not (
        1 <= len(executions) <= MAXIMUM_EXECUTIONS
    ):
        raise ExecutionAnalysisError(
            "Invalid execution count."
        )

    if now is None:
        reference_time = datetime.now(timezone.utc)
    else:
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ExecutionAnalysisError(
                "now must be a timezone-aware datetime."
            )

        reference_time = now.astimezone(timezone.utc)

    maximum_latency_ms = _number(
        maximum_latency_ms,
        "maximum_latency_ms",
    )

    maximum_adverse_slippage_points = _number(
        maximum_adverse_slippage_points,
        "maximum_adverse_slippage_points",
    )

    spread_anomaly_multiplier = _number(
        spread_anomaly_multiplier,
        "spread_anomaly_multiplier",
    )

    if maximum_latency_ms <= 0:
        raise ExecutionAnalysisError(
            "maximum_latency_ms must be positive."
        )

    if maximum_adverse_slippage_points < 0:
        raise ExecutionAnalysisError(
            "maximum_adverse_slippage_points cannot be negative."
        )

    if spread_anomaly_multiplier <= 1:
        raise ExecutionAnalysisError(
            "spread_anomaly_multiplier must be greater than 1."
        )

    validated = [
        _validate_execution(
            record,
            index,
            reference_time,
        )
        for index, record in enumerate(executions)
    ]

    identifiers = [
        item["execution_id"]
        for item in validated
        if item["execution_id"] is not None
    ]

    if len(identifiers) != len(set(identifiers)):
        raise ExecutionAnalysisError(
            "Duplicate execution_id values were supplied."
        )

    currencies = {
        item["currency"]
        for item in validated
        if item["currency"] is not None
    }

    if len(currencies) > 1:
        raise ExecutionAnalysisError(
            "All monetary cost records must use the same currency."
        )

    validated.sort(
        key=lambda item: item["timestamp"]
    )

    filled = [
        item for item in validated
        if item["status"] == "FILLED"
    ]

    partial = [
        item for item in validated
        if item["status"] == "PARTIALLY_FILLED"
    ]

    rejected = [
        item for item in validated
        if item["status"] == "REJECTED"
    ]

    cancelled = [
        item for item in validated
        if item["status"] == "CANCELLED"
    ]

    expired = [
        item for item in validated
        if item["status"] == "EXPIRED"
    ]

    executed = [
        item for item in validated
        if item["filled_volume"] > 0
    ]

    latency_values = [
        item["latency_ms"]
        for item in validated
        if item["latency_ms"] is not None
    ]

    adverse_slippage = [
        item["adverse_slippage_points"]
        for item in executed
    ]

    favorable_slippage = [
        item["favorable_slippage_points"]
        for item in executed
    ]

    spread_values = [
        item["spread_points"]
        for item in executed
        if item["spread_points"] is not None
    ]

    requested_volume = sum(
        item["requested_volume"]
        for item in validated
    )

    filled_volume = sum(
        item["filled_volume"]
        for item in validated
    )

    unfilled_volume = sum(
        item["unfilled_volume"]
        for item in validated
    )

    fill_rate = (
        filled_volume / requested_volume * 100.0
        if requested_volume > 0
        else 0.0
    )

    rejection_rate = (
        len(rejected) / len(validated) * 100.0
    )

    cancelled_after_partial = [
        item for item in cancelled
        if item["filled_volume"] > 0
    ]

    average_adverse_slippage = _safe_average(
        adverse_slippage
    )

    average_favorable_slippage = _safe_average(
        favorable_slippage
    )

    average_spread = _safe_average(
        spread_values
    )

    spread_anomalies, baseline_counts = (
        _rolling_spread_analysis(
            validated,
            spread_anomaly_multiplier,
        )
    )

    slow_executions = [
        item for item in validated
        if (
            item["latency_ms"] is not None
            and item["latency_ms"] > maximum_latency_ms
        )
    ]

    high_slippage = [
        item for item in executed
        if (
            item["adverse_slippage_points"]
            > maximum_adverse_slippage_points
        )
    ]

    total_commission = sum(
        item["commission"]
        for item in validated
    )

    total_swap = sum(
        item["swap"]
        for item in validated
    )

    total_spread_cost = sum(
        item["spread_cost"]
        for item in validated
    )

    total_slippage_cost = sum(
        item["slippage_cost"]
        for item in validated
    )

    immediate_execution_cost = (
        total_commission
        + total_spread_cost
        + total_slippage_cost
    )

    total_cost_including_swap = (
        immediate_execution_cost
        + total_swap
    )

    session_profiles = {}

    for item in validated:
        session = item["session"]

        profile = session_profiles.setdefault(
            session,
            {
                "orders": 0,
                "filled_orders": 0,
                "partially_filled_orders": 0,
                "rejected_orders": 0,
                "cancelled_orders": 0,
                "expired_orders": 0,
                "requested_volume": 0.0,
                "filled_volume": 0.0,
                "latencies": [],
                "adverse_slippage": [],
                "spreads": [],
            },
        )

        profile["orders"] += 1

        if item["status"] == "FILLED":
            profile["filled_orders"] += 1

        elif item["status"] == "PARTIALLY_FILLED":
            profile["partially_filled_orders"] += 1

        elif item["status"] == "REJECTED":
            profile["rejected_orders"] += 1

        elif item["status"] == "CANCELLED":
            profile["cancelled_orders"] += 1

        elif item["status"] == "EXPIRED":
            profile["expired_orders"] += 1

        profile["requested_volume"] += (
            item["requested_volume"]
        )

        profile["filled_volume"] += (
            item["filled_volume"]
        )

        if item["latency_ms"] is not None:
            profile["latencies"].append(
                item["latency_ms"]
            )

        if item["filled_volume"] > 0:
            profile["adverse_slippage"].append(
                item["adverse_slippage_points"]
            )

            if item["spread_points"] is not None:
                profile["spreads"].append(
                    item["spread_points"]
                )

    for profile in session_profiles.values():
        profile["fill_rate_percent"] = (
            profile["filled_volume"]
            / profile["requested_volume"]
            * 100.0
            if profile["requested_volume"] > 0
            else 0.0
        )

        profile["latency"] = _latency_statistics(
            profile.pop("latencies")
        )

        profile["average_adverse_slippage_points"] = (
            _safe_average(
                profile.pop("adverse_slippage")
            )
        )

        profile["average_spread_points"] = (
            _safe_average(
                profile.pop("spreads")
            )
        )

    rejection_reasons = {}

    for item in rejected:
        reason = (
            item["rejection_reason"]
            or "UNSPECIFIED"
        )

        rejection_reasons[reason] = (
            rejection_reasons.get(reason, 0) + 1
        )

    alerts = []

    if slow_executions:
        alerts.append("HIGH_EXECUTION_LATENCY")

    if high_slippage:
        alerts.append("EXCESSIVE_ADVERSE_SLIPPAGE")

    if spread_anomalies:
        alerts.append("ABNORMAL_SPREAD_EXPANSION")

    if rejected:
        alerts.append("ORDER_REJECTIONS_PRESENT")

    if partial:
        alerts.append("PARTIAL_FILLS_PRESENT")

    if cancelled_after_partial:
        alerts.append("CANCELLED_AFTER_PARTIAL_FILL")

    if not alerts:
        overall_status = "NO_CONFIGURED_ALERTS"
    else:
        overall_status = "EXECUTION_WARNINGS"

    return {
        "module": "execution_intelligence",
        "executions_received": len(validated),
        "filled_orders": len(filled),
        "partially_filled_orders": len(partial),
        "rejected_orders": len(rejected),
        "cancelled_orders": len(cancelled),
        "cancelled_after_partial_fill": len(
            cancelled_after_partial
        ),
        "expired_orders": len(expired),
        "requested_volume": requested_volume,
        "filled_volume": filled_volume,
        "unfilled_volume": unfilled_volume,
        "fill_rate_percent": fill_rate,
        "rejection_rate_percent": rejection_rate,
        "latency": _latency_statistics(
            latency_values
        ),
        "average_adverse_slippage_points": (
            average_adverse_slippage
        ),
        "average_favorable_slippage_points": (
            average_favorable_slippage
        ),
        "average_spread_points": average_spread,
        "spread_anomaly_count": len(spread_anomalies),
        "spread_anomalies": spread_anomalies,
        "spread_baseline_observation_counts": (
            baseline_counts
        ),
        "slow_execution_count": len(slow_executions),
        "high_slippage_count": len(high_slippage),
        "rejection_reasons": rejection_reasons,
        "costs": {
            "commission": total_commission,
            "swap": total_swap,
            "spread_cost": total_spread_cost,
            "slippage_cost": total_slippage_cost,
            "immediate_execution_cost": (
                immediate_execution_cost
            ),
            "total_cost_including_swap": (
                total_cost_including_swap
            ),
            "currency": (
                next(iter(currencies))
                if currencies
                else None
            ),
            "currency_status": (
                "CONSISTENT"
                if len(currencies) == 1
                else "NO_MONETARY_CURRENCY_DATA"
            ),
        },
        "session_profiles": session_profiles,
        "alerts": alerts,
        "overall_status": overall_status,
        "trading_signal_allowed": False,
        "limitations": [
            "Execution records must come from a trusted broker or MT5 source.",
            "Execution IDs should be unique broker execution identifiers, not order IDs reused across fills.",
            "A cancelled record with filled volume represents a cumulative order outcome; event-by-event lifecycle reconstruction requires separate linked order events.",
            "Requested and executed prices must use the same price convention.",
            "Point size must match the broker symbol specification at execution time.",
            "Monetary costs must use the same account currency and must not be double-counted.",
            "This module does not independently convert currencies.",
            "A spread anomaly baseline requires at least 10 earlier observations for the same symbol and session.",
            "Spread baseline is historical within the supplied dataset, not a live market baseline.",
            "Session labels are supplied by the caller and are not independently verified.",
            "Latency is measured only when supplied by the trusted execution source.",
            "Missing latency or cost data is not interpreted as zero execution delay or zero cost.",
            "This module does not execute, modify, or authorize orders.",
        ],
      }
