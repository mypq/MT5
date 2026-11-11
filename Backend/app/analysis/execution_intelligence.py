"""
GOLD BOT - Execution Intelligence

Analyzes order execution quality and transaction costs.

Capabilities:

- Bid/ask spread analysis
- Relative spread anomaly detection
- Slippage measurement
- Execution latency analysis
- Partial fill analysis
- Rejection statistics
- Estimated execution cost
- Session-based execution profiling

IMPORTANT:

- This module analyzes supplied execution records.
- It does not send, modify, or close trading orders.
- It does not connect directly to MT5.
- Monetary calculations require consistent account-currency values.
- Historical execution quality does not guarantee future quality.
  """

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite
from statistics import median
from typing import Any

MAXIMUM_EXECUTIONS = 100000
MINIMUM_SPREAD_BASELINE = 10

DEFAULT_SPREAD_ANOMALY_MULTIPLIER = 2.0
DEFAULT_MAXIMUM_LATENCY_MS = 1000.0
DEFAULT_MAXIMUM_ADVERSE_SLIPPAGE_POINTS = 5.0

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
except (TypeError, ValueError):
    raise ExecutionAnalysisError(
        f"{field} must be numeric."
    )

if not isfinite(result):
    raise ExecutionAnalysisError(
        f"{field} must be finite."
    )

return result

def _timestamp(
value: Any,
field: str,
) -> datetime:

if not isinstance(value, str):
    raise ExecutionAnalysisError(
        f"{field} must be ISO-8601."
    )

try:
    result = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
except ValueError:
    raise ExecutionAnalysisError(
        f"{field} is invalid."
    )

if result.tzinfo is None:
    raise ExecutionAnalysisError(
        f"{field} must include a timezone."
    )

return result.astimezone(
    timezone.utc
)

def _validate_execution(
record: Mapping[str, Any],
index: int,
) -> dict[str, Any]:

if not isinstance(record, Mapping):
    raise ExecutionAnalysisError(
        f"Execution {index} must be a mapping."
    )

status = str(
    record.get("status", "")
).upper()

if status not in {
    "FILLED",
    "PARTIALLY_FILLED",
    "REJECTED",
    "CANCELLED",
    "EXPIRED",
}:
    raise ExecutionAnalysisError(
        f"Invalid execution status at index {index}."
    )

timestamp = _timestamp(
    record.get("timestamp"),
    f"executions[{index}].timestamp",
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

if commission < 0:
    raise ExecutionAnalysisError(
        "Commission cannot be negative."
    )

if latency is not None and latency < 0:
    raise ExecutionAnalysisError(
        "Latency cannot be negative."
    )

if slippage_cost < 0:
    raise ExecutionAnalysisError(
        "Slippage cost cannot be negative."
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
        "FILLED execution must have the full requested volume."
    )

if status == "PARTIALLY_FILLED" and not (
    0 < filled_volume < requested_volume
):
    raise ExecutionAnalysisError(
        "PARTIALLY_FILLED execution must have a partial volume."
    )

if status in {"REJECTED", "CANCELLED", "EXPIRED"} and filled_volume != 0:
    raise ExecutionAnalysisError(
        "Rejected, cancelled, or expired records cannot contain filled volume."
    )

if filled_volume > 0 and executed_price is None:
    raise ExecutionAnalysisError(
        "Executed price is required for filled volume."
    )

if filled_volume > 0 and requested_price is None:
    raise ExecutionAnalysisError(
        "Requested price is required for slippage analysis."
    )

if filled_volume > 0 and point_size is None:
    raise ExecutionAnalysisError(
        "Point size is required for slippage analysis."
    )

if filled_volume > 0:

    raw_slippage = (
        executed_price - requested_price
        if side == "BUY"
        else requested_price - executed_price
    )

    slippage_points = (
        raw_slippage / point_size
    )

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
        if point_size is not None
        else None
    )

spread_cost = _number(
    record.get("spread_cost", 0),
    f"executions[{index}].spread_cost",
)

if spread_cost < 0:
    raise ExecutionAnalysisError(
        "Spread cost cannot be negative."
    )

return {
    "timestamp": timestamp,
    "symbol": symbol,
    "side": side,
    "status": status,
    "requested_volume": requested_volume,
    "filled_volume": filled_volume,
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
    "rejection_reason": str(
        record.get("rejection_reason", "")
    ).strip() or None,
    "session": str(
        record.get("session", "UNKNOWN")
    ).strip().upper(),
}

def _safe_average(
values: Sequence[float],
) -> float | None:

if not values:
    return None

return sum(values) / len(values)

def _spread_baseline(
executions: Sequence[dict[str, Any]],
) -> float | None:

values = [
    item["spread_points"]
    for item in executions
    if item["spread_points"] is not None
    and item["status"] in {
        "FILLED",
        "PARTIALLY_FILLED",
    }
]

if len(values) < MINIMUM_SPREAD_BASELINE:
    return None

return median(values)

def analyze_execution_quality(
executions: Sequence[Mapping[str, Any]],
maximum_latency_ms: float = DEFAULT_MAXIMUM_LATENCY_MS,
maximum_adverse_slippage_points: float = (
DEFAULT_MAXIMUM_ADVERSE_SLIPPAGE_POINTS
),
spread_anomaly_multiplier: float = (
DEFAULT_SPREAD_ANOMALY_MULTIPLIER
),
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
    _validate_execution(record, index)
    for index, record in enumerate(executions)
]

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

executed = filled + partial

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

fill_rate = (
    filled_volume / requested_volume * 100.0
    if requested_volume > 0
    else 0.0
)

rejection_rate = (
    len(rejected) / len(validated) * 100.0
)

average_latency = _safe_average(
    latency_values
)

average_adverse_slippage = _safe_average(
    adverse_slippage
)

average_favorable_slippage = _safe_average(
    favorable_slippage
)

average_spread = _safe_average(
    spread_values
)

baseline_spread = _spread_baseline(
    validated
)

spread_anomalies = []

if baseline_spread is not None:

    spread_anomalies = [
        item for item in executed
        if (
            item["spread_points"] is not None
            and item["spread_points"]
            > baseline_spread * spread_anomaly_multiplier
        )
    ]

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

known_execution_cost = (
    total_commission
    + total_spread_cost
    + total_slippage_cost
)

session_profiles = {}

for item in validated:

    session = item["session"]

    profile = session_profiles.setdefault(
        session,
        {
            "orders": 0,
            "filled_orders": 0,
            "rejected_orders": 0,
            "latencies": [],
            "adverse_slippage": [],
            "spreads": [],
        },
    )

    profile["orders"] += 1

    if item["status"] == "FILLED":
        profile["filled_orders"] += 1

    if item["status"] == "REJECTED":
        profile["rejected_orders"] += 1

    if item["latency_ms"] is not None:
        profile["latencies"].append(
            item["latency_ms"]
        )

    if item["status"] in {
        "FILLED",
        "PARTIALLY_FILLED",
    }:

        profile["adverse_slippage"].append(
            item["adverse_slippage_points"]
        )

        if item["spread_points"] is not None:
            profile["spreads"].append(
                item["spread_points"]
            )

for profile in session_profiles.values():

    profile["average_latency_ms"] = (
        _safe_average(profile.pop("latencies"))
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
    "expired_orders": len(expired),
    "fill_rate_percent": fill_rate,
    "rejection_rate_percent": rejection_rate,
    "average_latency_ms": average_latency,
    "average_adverse_slippage_points": (
        average_adverse_slippage
    ),
    "average_favorable_slippage_points": (
        average_favorable_slippage
    ),
    "average_spread_points": average_spread,
    "historical_median_spread_points": baseline_spread,
    "spread_anomaly_count": len(spread_anomalies),
    "slow_execution_count": len(slow_executions),
    "high_slippage_count": len(high_slippage),
    "rejection_reasons": rejection_reasons,
    "costs": {
        "commission": total_commission,
        "swap": total_swap,
        "spread_cost": total_spread_cost,
        "slippage_cost": total_slippage_cost,
        "known_execution_cost_excluding_swap": (
            known_execution_cost
        ),
        "total_cost_including_swap": (
            known_execution_cost + total_swap
        ),
        "currency": "CALLER_SUPPLIED_ACCOUNT_CURRENCY",
    },
    "session_profiles": session_profiles,
    "alerts": alerts,
    "overall_status": overall_status,
    "limitations": [
        "Execution records must come from a trusted broker or MT5 source.",
        "Requested and executed prices must use the same price convention.",
        "Point size must match the broker symbol specification.",
        "Spread cost and slippage cost must be supplied in a consistent account currency.",
        "Spread cost and slippage cost are not independently reconstructed from points because contract specifications and conversion rates are required.",
        "Swap is reported separately because it is a holding cost, not an immediate execution cost.",
        "A median spread baseline requires at least 10 valid spread observations.",
        "Session labels are supplied by the caller and are not independently verified.",
        "This module does not execute or modify orders.",
    ],
  }
