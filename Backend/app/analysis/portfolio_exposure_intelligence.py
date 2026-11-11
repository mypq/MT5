"""
GOLD BOT - Portfolio & Exposure Intelligence

Portfolio-wide exposure and risk assessment.

This module:

- Evaluates open position exposure.
- Calculates stop-based monetary risk.
- Monitors realized and floating losses.
- Evaluates margin utilization.
- Detects directional concentration.
- Detects missing stop losses.
- Checks account-level risk limits.

IMPORTANT:

- Monetary risk requires broker-verified contract specifications.
- Correlation exposure requires separately supplied correlation data.
- This module never executes or closes trades.
- Missing information must never be interpreted as zero risk.
  """

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

class PortfolioExposureError(ValueError):
"""Raised when portfolio input is invalid."""

DEFAULT_LIMITS = {
"max_total_risk_percent": 3.0,
"max_daily_loss_percent": 2.0,
"max_margin_utilization_percent": 50.0,
"max_directional_concentration_percent": 80.0,
"max_open_positions": 10,
}

MAXIMUM_POSITIONS = 500
MAXIMUM_CORRELATION = 1.0

def _number(value: Any, field: str) -> float:

if isinstance(value, bool):
    raise PortfolioExposureError(
        f"{field} must be numeric."
    )

try:
    result = float(value)
except (TypeError, ValueError):
    raise PortfolioExposureError(
        f"{field} must be numeric."
    )

if not isfinite(result):
    raise PortfolioExposureError(
        f"{field} must be finite."
    )

return result

def _positive(
value: Any,
field: str,
) -> float:

result = _number(
    value,
    field,
)

if result <= 0:
    raise PortfolioExposureError(
        f"{field} must be positive."
    )

return result

def _validate_limits(
limits: Mapping[str, Any] | None,
) -> dict[str, float]:

result = dict(DEFAULT_LIMITS)

if limits is None:
    return result

if not isinstance(limits, Mapping):
    raise PortfolioExposureError(
        "limits must be a mapping."
    )

for name, value in limits.items():

    if name not in result:
        raise PortfolioExposureError(
            f"Unknown portfolio limit: {name}"
        )

    parsed = _number(
        value,
        f"limits.{name}",
    )

    if name == "max_open_positions":

        if (
            parsed < 1
            or not parsed.is_integer()
        ):
            raise PortfolioExposureError(
                "max_open_positions must be a positive integer."
            )

    elif not 0 < parsed <= 100:

        raise PortfolioExposureError(
            f"{name} must be greater than 0 and <= 100."
        )

    result[name] = parsed

return result

def _position_risk(
position: Mapping[str, Any],
index: int,
) -> dict[str, Any]:

if not isinstance(position, Mapping):
    raise PortfolioExposureError(
        f"Position {index} must be a mapping."
    )

symbol = position.get("symbol")

if not isinstance(symbol, str) or not symbol.strip():
    raise PortfolioExposureError(
        f"Position {index} has an invalid symbol."
    )

side = str(
    position.get("side", "")
).strip().upper()

if side not in {"BUY", "SELL"}:
    raise PortfolioExposureError(
        f"Position {index} side must be BUY or SELL."
    )

volume = _positive(
    position.get("volume"),
    f"position[{index}].volume",
)

entry_price = _positive(
    position.get("entry_price"),
    f"position[{index}].entry_price",
)

current_price = _positive(
    position.get("current_price"),
    f"position[{index}].current_price",
)

stop_loss = position.get(
    "stop_loss"
)

if stop_loss is None:
    risk_amount = None
    missing_stop = True

else:

    stop_loss = _positive(
        stop_loss,
        f"position[{index}].stop_loss",
    )

    if side == "BUY" and stop_loss >= entry_price:
        raise PortfolioExposureError(
            f"Position {index} BUY stop must be below entry."
        )

    if side == "SELL" and stop_loss <= entry_price:
        raise PortfolioExposureError(
            f"Position {index} SELL stop must be above entry."
        )

    tick_size = _positive(
        position.get("tick_size"),
        f"position[{index}].tick_size",
    )

    tick_value = _positive(
        position.get("tick_value_per_lot"),
        f"position[{index}].tick_value_per_lot",
    )

    if side == "BUY":
        distance = entry_price - stop_loss
    else:
        distance = stop_loss - entry_price

    risk_amount = (
        distance / tick_size
    ) * tick_value * volume

    missing_stop = False

if side == "BUY":
    unrealized_direction = (
        current_price - entry_price
    )
else:
    unrealized_direction = (
        entry_price - current_price
    )

tick_size_for_pnl = _positive(
    position.get("tick_size"),
    f"position[{index}].tick_size",
)

tick_value_for_pnl = _positive(
    position.get("tick_value_per_lot"),
    f"position[{index}].tick_value_per_lot",
)

floating_pnl = (
    unrealized_direction / tick_size_for_pnl
) * tick_value_for_pnl * volume

gross_notional = _number(
    position.get("notional_value"),
    f"position[{index}].notional_value",
)

if gross_notional < 0:
    raise PortfolioExposureError(
        f"Position {index} notional cannot be negative."
    )

return {
    "symbol": symbol.strip().upper(),
    "side": side,
    "volume": volume,
    "entry_price": entry_price,
    "current_price": current_price,
    "stop_loss": stop_loss,
    "risk_amount": risk_amount,
    "missing_stop_loss": missing_stop,
    "floating_pnl": floating_pnl,
    "gross_notional": gross_notional,
    "correlation_group": position.get(
        "correlation_group"
    ),
}

def _calculate_concentration(
positions: Sequence[dict[str, Any]],
) -> dict[str, Any]:

buy_notional = sum(
    item["gross_notional"]
    for item in positions
    if item["side"] == "BUY"
)

sell_notional = sum(
    item["gross_notional"]
    for item in positions
    if item["side"] == "SELL"
)

total = buy_notional + sell_notional

if total <= 0:
    return {
        "buy_notional": buy_notional,
        "sell_notional": sell_notional,
        "directional_concentration_percent": None,
    }

concentration = (
    max(
        buy_notional,
        sell_notional,
    ) / total
) * 100.0

return {
    "buy_notional": buy_notional,
    "sell_notional": sell_notional,
    "directional_concentration_percent": round(
        concentration,
        2,
    ),
}

def analyze_portfolio_exposure(
account: Mapping[str, Any],
positions: Sequence[Mapping[str, Any]],
limits: Mapping[str, Any] | None = None,
correlation_matrix: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:

if not isinstance(account, Mapping):
    raise PortfolioExposureError(
        "account must be a mapping."
    )

if (
    isinstance(positions, (str, bytes))
    or not isinstance(positions, Sequence)
):
    raise PortfolioExposureError(
        "positions must be a sequence."
    )

if len(positions) > MAXIMUM_POSITIONS:
    raise PortfolioExposureError(
        "Too many open positions."
    )

configured_limits = _validate_limits(
    limits
)

balance = _positive(
    account.get("balance"),
    "account.balance",
)

equity = _positive(
    account.get("equity"),
    "account.equity",
)

free_margin = _number(
    account.get("free_margin"),
    "account.free_margin",
)

used_margin = _number(
    account.get("used_margin"),
    "account.used_margin",
)

realized_daily_loss = _number(
    account.get("daily_realized_loss"),
    "account.daily_realized_loss",
)

if free_margin < 0 or used_margin < 0:
    raise PortfolioExposureError(
        "Margin values cannot be negative."
    )

if realized_daily_loss < 0:
    raise PortfolioExposureError(
        "daily_realized_loss must be a positive loss amount or zero."
    )

if abs(
    balance + _number(
        account.get("daily_realized_pnl", 0),
        "account.daily_realized_pnl",
    ) - equity
) > max(balance, equity) * 0.25:

    # Large differences are not automatically invalid:
    # deposits, withdrawals, swaps and commissions can affect equity.
    pass

normalized_positions = [
    _position_risk(position, index)
    for index, position in enumerate(positions)
]

total_open_risk = 0.0
known_risk_positions = 0
missing_stop_positions = []

for index, position in enumerate(
    normalized_positions
):

    if position["risk_amount"] is None:

        missing_stop_positions.append(index)

    else:

        total_open_risk += position["risk_amount"]
        known_risk_positions += 1

floating_pnl = sum(
    item["floating_pnl"]
    for item in normalized_positions
)

daily_floating_loss = max(
    0.0,
    -floating_pnl,
)

daily_total_loss = (
    realized_daily_loss
    + daily_floating_loss
)

open_risk_percent = (
    total_open_risk / equity
) * 100.0

daily_loss_percent = (
    daily_total_loss / balance
) * 100.0

margin_utilization = (
    used_margin / equity
) * 100.0

concentration = _calculate_concentration(
    normalized_positions
)

warnings = []
violations = []

if missing_stop_positions:
    violations.append(
        "POSITIONS_WITHOUT_STOP_LOSS"
    )

if open_risk_percent > configured_limits[
    "max_total_risk_percent"
]:
    violations.append(
        "TOTAL_OPEN_RISK_LIMIT_EXCEEDED"
    )

if daily_loss_percent > configured_limits[
    "max_daily_loss_percent"
]:
    violations.append(
        "DAILY_LOSS_LIMIT_EXCEEDED"
    )

if margin_utilization > configured_limits[
    "max_margin_utilization_percent"
]:
    violations.append(
        "MARGIN_UTILIZATION_LIMIT_EXCEEDED"
    )

if len(normalized_positions) > configured_limits[
    "max_open_positions"
]:
    violations.append(
        "MAXIMUM_OPEN_POSITIONS_EXCEEDED"
    )

directional_concentration = concentration[
    "directional_concentration_percent"
]

if (
    directional_concentration is not None
    and directional_concentration
    > configured_limits[
        "max_directional_concentration_percent"
    ]
):
    warnings.append(
        "HIGH_DIRECTIONAL_CONCENTRATION"
    )

if equity < balance * 0.90:
    warnings.append(
        "EQUITY_SIGNIFICANTLY_BELOW_BALANCE"
    )

if free_margin <= 0:
    violations.append(
        "NO_FREE_MARGIN"
    )

correlation_status = (
    "NOT_EVALUATED"
)

if correlation_matrix is not None:

    if not isinstance(
        correlation_matrix,
        Mapping,
    ):
        raise PortfolioExposureError(
            "correlation_matrix must be a mapping."
        )

    correlation_status = "INPUT_PROVIDED_REQUIRES_VALIDATION"

    # Correlation-adjusted portfolio risk is deliberately
    # not calculated from incomplete or unverified matrices.
    warnings.append(
        "CORRELATION_MATRIX_NOT_USED_FOR_RISK_REDUCTION"
    )

risk_coverage = (
    known_risk_positions / len(normalized_positions)
    if normalized_positions
    else 1.0
)

if risk_coverage < 1.0:
    warnings.append(
        "INCOMPLETE_STOP_BASED_RISK_COVERAGE"
    )

can_open_new_trade = (
    not violations
    and risk_coverage == 1.0
    and len(normalized_positions)
    < configured_limits["max_open_positions"]
)

return {
    "module": "portfolio_exposure_intelligence",
    "account_currency": account.get(
        "currency"
    ),
    "balance": balance,
    "equity": equity,
    "free_margin": free_margin,
    "used_margin": used_margin,
    "open_positions": len(
        normalized_positions
    ),
    "total_open_risk_amount": round(
        total_open_risk,
        2,
    ),
    "total_open_risk_percent": round(
        open_risk_percent,
        2,
    ),
    "floating_pnl": round(
        floating_pnl,
        2,
    ),
    "daily_realized_loss": round(
        realized_daily_loss,
        2,
    ),
    "daily_floating_loss": round(
        daily_floating_loss,
        2,
    ),
    "daily_total_loss": round(
        daily_total_loss,
        2,
    ),
    "daily_loss_percent": round(
        daily_loss_percent,
        2,
    ),
    "margin_utilization_percent": round(
        margin_utilization,
        2,
    ),
    "directional_concentration": concentration,
    "risk_coverage_percent": round(
        risk_coverage * 100.0,
        2,
    ),
    "missing_stop_positions": missing_stop_positions,
    "correlation_status": correlation_status,
    "violations": violations,
    "warnings": warnings,
    "can_open_new_trade": can_open_new_trade,
    "position_details": normalized_positions,
    "execution_authorized": False,
    "limitations": [
        "Broker contract specifications must be verified before monetary risk is trusted.",
        "Tick value may vary with price and account currency.",
        "Notional values must be converted into a common account currency before concentration analysis.",
        "Pending orders are not included.",
        "Commission, swaps and future slippage are not included in stop-based risk.",
        "Daily loss requires a reliable broker-defined trading-day baseline.",
        "Correlation-adjusted portfolio risk is not calculated.",
        "This module does not replace the independent risk gate.",
        "This module never opens or closes trades.",
    ],
}
