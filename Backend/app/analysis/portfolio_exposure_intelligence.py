"""
GOLD BOT - Portfolio & Exposure Intelligence

Conservative portfolio exposure and account risk assessment.

This module:
- Calculates stop-based monetary risk.
- Separates net floating P/L from gross floating losses.
- Monitors realized and floating daily losses.
- Evaluates margin utilization and directional concentration.
- Rejects unverified, stale, or incomplete account and position data.
- Never executes or authorizes trades.

All monetary values must use the account currency.
Broker specifications must come from a trusted MT5 adapter.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
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
MAXIMUM_DATA_AGE_SECONDS = 120
MAXIMUM_REFERENCE_FUTURE_SECONDS = 60


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise PortfolioExposureError(f"{field} must be numeric.")

    try:
        result = float(value)
    except (TypeError, ValueError):
        raise PortfolioExposureError(
            f"{field} must be numeric."
        ) from None

    if not isfinite(result):
        raise PortfolioExposureError(f"{field} must be finite.")

    return result


def _positive(value: Any, field: str) -> float:
    result = _number(value, field)

    if result <= 0:
        raise PortfolioExposureError(f"{field} must be positive.")

    return result


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise PortfolioExposureError(
            f"{field} must be an ISO-8601 string."
        )

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        raise PortfolioExposureError(
            f"{field} is invalid."
        ) from None

    if parsed.tzinfo is None:
        raise PortfolioExposureError(
            f"{field} must include a timezone."
        )

    return parsed.astimezone(timezone.utc)


def _validate_freshness(
    timestamp_value: Any,
    reference_time: datetime,
    field: str,
    maximum_age_seconds: float = MAXIMUM_DATA_AGE_SECONDS,
) -> float:
    timestamp = _parse_timestamp(timestamp_value, field)
    now = datetime.now(timezone.utc)

    if timestamp > now:
        raise PortfolioExposureError(
            f"{field} cannot be in the future."
        )

    age = (reference_time - timestamp).total_seconds()

    if age < 0:
        raise PortfolioExposureError(
            f"{field} is later than reference_time."
        )

    if age > maximum_age_seconds:
        raise PortfolioExposureError(
            f"{field} is stale."
        )

    return age


def _validate_limits(
    limits: Mapping[str, Any] | None,
) -> dict[str, float]:
    result = dict(DEFAULT_LIMITS)

    if limits is None:
        return result

    if not isinstance(limits, Mapping):
        raise PortfolioExposureError("limits must be a mapping.")

    for name, value in limits.items():
        if name not in result:
            raise PortfolioExposureError(
                f"Unknown portfolio limit: {name}"
            )

        parsed = _number(value, f"limits.{name}")

        if name == "max_open_positions":
            if parsed < 1 or not parsed.is_integer():
                raise PortfolioExposureError(
                    "max_open_positions must be a positive integer."
                )
        elif not 0 < parsed <= 100:
            raise PortfolioExposureError(
                f"{name} must be greater than 0 and at most 100."
            )

        result[name] = parsed

    return result


def _validate_account(
    account: Mapping[str, Any],
    reference_time: datetime,
) -> dict[str, Any]:
    if not isinstance(account, Mapping):
        raise PortfolioExposureError("account must be a mapping.")

    if account.get("source_verified") is not True:
        raise PortfolioExposureError(
            "Account data must be verified by a trusted adapter."
        )

    currency = account.get("currency")

    if not isinstance(currency, str) or not currency.strip():
        raise PortfolioExposureError(
            "Account currency is required."
        )

    currency = currency.strip().upper()

    if account.get("account_mode") != "DEMO":
        raise PortfolioExposureError(
            "Only a verified DEMO account is accepted."
        )

    _validate_freshness(
        account.get("timestamp"),
        reference_time,
        "account.timestamp",
    )

    balance = _positive(account.get("balance"), "account.balance")
    equity = _positive(account.get("equity"), "account.equity")
    daily_start_equity = _positive(
        account.get("daily_start_equity"),
        "account.daily_start_equity",
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
            "daily_realized_loss must be a non-negative loss amount."
        )

    if used_margin + free_margin <= 0:
        raise PortfolioExposureError(
            "Account margin information is invalid."
        )

    return {
        "currency": currency,
        "balance": balance,
        "equity": equity,
        "daily_start_equity": daily_start_equity,
        "free_margin": free_margin,
        "used_margin": used_margin,
        "daily_realized_loss": realized_daily_loss,
    }


def _position_risk(
    position: Mapping[str, Any],
    index: int,
    account_currency: str,
    reference_time: datetime,
) -> dict[str, Any]:
    if not isinstance(position, Mapping):
        raise PortfolioExposureError(
            f"Position {index} must be a mapping."
        )

    if position.get("source_verified") is not True:
        raise PortfolioExposureError(
            f"Position {index} source is not verified."
        )

    _validate_freshness(
        position.get("timestamp"),
        reference_time,
        f"position[{index}].timestamp",
    )

    position_id = position.get("position_id")

    if not isinstance(position_id, (str, int)):
        raise PortfolioExposureError(
            f"Position {index} requires a valid position_id."
        )

    symbol = position.get("symbol")

    if not isinstance(symbol, str) or symbol.strip().upper() != "XAUUSD":
        raise PortfolioExposureError(
            f"Position {index} symbol must be XAUUSD."
        )

    side = str(position.get("side", "")).strip().upper()

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

    tick_size = _positive(
        position.get("tick_size"),
        f"position[{index}].tick_size",
    )

    tick_value = _positive(
        position.get("tick_value_per_lot"),
        f"position[{index}].tick_value_per_lot",
    )

    tick_value_currency = position.get("tick_value_currency")

    if (
        not isinstance(tick_value_currency, str)
        or tick_value_currency.strip().upper() != account_currency
    ):
        raise PortfolioExposureError(
            f"Position {index} tick value currency must match account currency."
        )

    stop_loss = position.get("stop_loss")
    missing_stop = stop_loss is None
    risk_amount = None

    if not missing_stop:
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

        if side == "BUY" and current_price <= stop_loss:
            raise PortfolioExposureError(
                f"Position {index} BUY stop has been reached or breached."
            )

        if side == "SELL" and current_price >= stop_loss:
            raise PortfolioExposureError(
                f"Position {index} SELL stop has been reached or breached."
            )

        distance = (
            entry_price - stop_loss
            if side == "BUY"
            else stop_loss - entry_price
        )

        risk_amount = (
            distance / tick_size
        ) * tick_value * volume

    price_difference = (
        current_price - entry_price
        if side == "BUY"
        else entry_price - current_price
    )

    floating_pnl = (
        price_difference / tick_size
    ) * tick_value * volume

    notional_value = _positive(
        position.get("notional_value"),
        f"position[{index}].notional_value",
    )

    notional_currency = position.get("notional_currency")

    if (
        not isinstance(notional_currency, str)
        or notional_currency.strip().upper() != account_currency
    ):
        raise PortfolioExposureError(
            f"Position {index} notional currency must match account currency."
        )

    return {
        "position_id": str(position_id),
        "symbol": "XAUUSD",
        "side": side,
        "volume": volume,
        "entry_price": entry_price,
        "current_price": current_price,
        "stop_loss": stop_loss,
        "risk_amount": risk_amount,
        "missing_stop_loss": missing_stop,
        "floating_pnl": floating_pnl,
        "gross_floating_loss": max(0.0, -floating_pnl),
        "gross_notional": notional_value,
        "correlation_group": position.get("correlation_group"),
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
        concentration = None
    else:
        concentration = (
            max(buy_notional, sell_notional) / total
        ) * 100.0

    return {
        "buy_notional": round(buy_notional, 2),
        "sell_notional": round(sell_notional, 2),
        "total_notional": round(total, 2),
        "directional_concentration_percent": (
            round(concentration, 2)
            if concentration is not None
            else None
        ),
    }


def analyze_portfolio_exposure(
    account: Mapping[str, Any],
    positions: Sequence[Mapping[str, Any]],
    limits: Mapping[str, Any] | None = None,
    correlation_matrix: Mapping[str, Mapping[str, Any]] | None = None,
    reference_time: str | None = None,
) -> dict[str, Any]:
    """
    Analyze account and open XAUUSD positions.

    Account requirements:
    - source_verified=True
    - account_mode="DEMO"
    - timezone-aware timestamp
    - currency, balance, equity, daily_start_equity
    - free_margin, used_margin, daily_realized_loss

    Position requirements:
    - source_verified=True
    - timezone-aware timestamp
    - unique position_id
    - symbol, side, volume, entry_price, current_price
    - tick_size, tick_value_per_lot, tick_value_currency
    - notional_value, notional_currency
    - stop_loss (None is reported as a violation)

    Monetary specifications must be supplied by a trusted broker adapter.
    """

    if reference_time is None:
        reference = datetime.now(timezone.utc)
    else:
        reference = _parse_timestamp(
            reference_time,
            "reference_time",
        )

    now = datetime.now(timezone.utc)

    if (
        reference - now
    ).total_seconds() > MAXIMUM_REFERENCE_FUTURE_SECONDS:
        raise PortfolioExposureError(
            "reference_time is too far in the future."
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

    configured_limits = _validate_limits(limits)
    normalized_account = _validate_account(account, reference)

    normalized_positions = []
    seen_ids = set()

    for index, position in enumerate(positions):
        normalized = _position_risk(
            position,
            index,
            normalized_account["currency"],
            reference,
        )

        if normalized["position_id"] in seen_ids:
            raise PortfolioExposureError(
                "Duplicate position_id detected."
            )

        seen_ids.add(normalized["position_id"])
        normalized_positions.append(normalized)

    total_open_risk = sum(
        item["risk_amount"] or 0.0
        for item in normalized_positions
    )

    missing_stop_positions = [
        item["position_id"]
        for item in normalized_positions
        if item["missing_stop_loss"]
    ]

    net_floating_pnl = sum(
        item["floating_pnl"]
        for item in normalized_positions
    )

    # Gross losses prevent profitable positions from hiding losing ones.
    gross_floating_loss = sum(
        item["gross_floating_loss"]
        for item in normalized_positions
    )

    daily_total_loss = (
        normalized_account["daily_realized_loss"]
        + gross_floating_loss
    )

    open_risk_percent = (
        total_open_risk / normalized_account["equity"]
    ) * 100.0

    daily_loss_percent = (
        daily_total_loss
        / normalized_account["daily_start_equity"]
    ) * 100.0

    margin_utilization = (
        normalized_account["used_margin"]
        / normalized_account["equity"]
    ) * 100.0

    concentration = _calculate_concentration(
        normalized_positions
    )

    directional_concentration = concentration[
        "directional_concentration_percent"
    ]

    violations: list[str] = []
    warnings: list[str] = []

    if missing_stop_positions:
        violations.append("POSITIONS_WITHOUT_STOP_LOSS")

    if open_risk_percent >= configured_limits[
        "max_total_risk_percent"
    ]:
        violations.append("TOTAL_OPEN_RISK_LIMIT_REACHED")

    if daily_loss_percent >= configured_limits[
        "max_daily_loss_percent"
    ]:
        violations.append("DAILY_LOSS_LIMIT_REACHED")

    if margin_utilization >= configured_limits[
        "max_margin_utilization_percent"
    ]:
        violations.append("MARGIN_UTILIZATION_LIMIT_REACHED")

    if len(normalized_positions) >= configured_limits[
        "max_open_positions"
    ]:
        violations.append("MAXIMUM_OPEN_POSITIONS_REACHED")

    if (
        directional_concentration is not None
        and directional_concentration >= configured_limits[
            "max_directional_concentration_percent"
        ]
    ):
        warnings.append("HIGH_DIRECTIONAL_CONCENTRATION")

    if normalized_account["free_margin"] <= 0:
        violations.append("NO_FREE_MARGIN")

    if normalized_account["equity"] < (
        normalized_account["balance"] * 0.90
    ):
        warnings.append("EQUITY_SIGNIFICANTLY_BELOW_BALANCE")

    known_risk_positions = sum(
        1
        for item in normalized_positions
        if item["risk_amount"] is not None
    )

    risk_coverage = (
        known_risk_positions / len(normalized_positions)
        if normalized_positions
        else 1.0
    )

    if risk_coverage < 1.0:
        warnings.append("INCOMPLETE_STOP_BASED_RISK_COVERAGE")

    correlation_status = "NOT_PROVIDED"

    if correlation_matrix is not None:
        if not isinstance(correlation_matrix, Mapping):
            raise PortfolioExposureError(
                "correlation_matrix must be a mapping."
            )

        # Correlation is deliberately not used to reduce risk.
        correlation_status = "PROVIDED_BUT_NOT_APPLIED"
        warnings.append("CORRELATION_DATA_NOT_USED_FOR_RISK_REDUCTION")

    can_open_new_trade = (
        not violations
        and risk_coverage == 1.0
        and len(normalized_positions)
        < configured_limits["max_open_positions"]
    )

    return {
        "module": "portfolio_exposure_intelligence",
        "symbol_scope": "XAUUSD",
        "account_currency": normalized_account["currency"],
        "balance": round(normalized_account["balance"], 2),
        "equity": round(normalized_account["equity"], 2),
        "daily_start_equity": round(
            normalized_account["daily_start_equity"],
            2,
        ),
        "free_margin": round(normalized_account["free_margin"], 2),
        "used_margin": round(normalized_account["used_margin"], 2),
        "open_positions": len(normalized_positions),
        "total_open_risk_amount": round(total_open_risk, 2),
        "total_open_risk_percent": round(open_risk_percent, 2),
        "net_floating_pnl": round(net_floating_pnl, 2),
        "gross_floating_loss": round(gross_floating_loss, 2),
        "daily_realized_loss": round(
            normalized_account["daily_realized_loss"],
            2,
        ),
        "daily_total_loss": round(daily_total_loss, 2),
        "daily_loss_percent": round(daily_loss_percent, 2),
        "margin_utilization_percent": round(
            margin_utilization,
            2,
        ),
        "directional_concentration": concentration,
        "risk_coverage_percent": round(risk_coverage * 100.0, 2),
        "missing_stop_positions": missing_stop_positions,
        "correlation_status": correlation_status,
        "violations": violations,
        "warnings": warnings,
        "can_open_new_trade": can_open_new_trade,
        "position_details": normalized_positions,
        "execution_authorized": False,
        "trading_signal_allowed": False,
        "limitations": [
            "Account and position source verification must come from a trusted MT5 adapter.",
            "DEMO mode must be independently verified by the account adapter.",
            "Tick value and contract specifications must match the account currency.",
            "Notional values must be converted into account currency before submission.",
            "Pending orders are not included.",
            "Commission, swaps, and future slippage are not included in stop-based risk.",
            "Daily realized loss must use a reliable broker trading-day baseline.",
            "Gross floating losses are added without offsetting profitable positions.",
            "Correlation-adjusted portfolio risk is not calculated.",
            "This module does not replace the independent risk gate.",
            "This module never opens or closes trades.",
        ],
  }
