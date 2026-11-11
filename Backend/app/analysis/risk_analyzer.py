"""
GOLD BOT - Risk Management Intelligence

Independent capital protection and trade risk validation.

Features:
- Risk-based position sizing
- Direction-aware stop-loss validation
- Net reward-to-risk calculation after estimated costs
- Realized and floating daily loss protection
- Account equity drawdown protection
- Open position risk aggregation
- Gold exposure limits
- Position count limits
- Broker volume constraints
- Estimated execution cost allowance

Important:
- This module never executes orders.
- Approval is not execution authorization.
- Broker specifications must be independently verified.
- Monetary values must use the account currency.
"""

from collections.abc import Mapping, Sequence
from math import floor, isfinite
from typing import Any


DEFAULT_MAXIMUM_RISK_PERCENT = 0.5
DEFAULT_MAXIMUM_DAILY_LOSS_PERCENT = 1.0
DEFAULT_MAXIMUM_DRAWDOWN_PERCENT = 10.0

DEFAULT_MAXIMUM_OPEN_RISK_PERCENT = 2.0
DEFAULT_MAXIMUM_OPEN_POSITIONS = 3
DEFAULT_MAXIMUM_SYMBOL_EXPOSURE_LOTS = 2.0

DEFAULT_MINIMUM_REWARD_TO_RISK = 1.5

SUPPORTED_SYMBOL = "XAUUSD"
VOLUME_TOLERANCE = 1e-9


class RiskAnalysisError(ValueError):
    """Raised when risk input data is invalid."""


def _number(
    value: Any,
    field: str,
    allow_zero: bool = False,
) -> float:
    if isinstance(value, bool):
        raise RiskAnalysisError(
            f"{field} must be numeric."
        )

    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RiskAnalysisError(
            f"{field} must be numeric."
        ) from exc

    if not isfinite(result):
        raise RiskAnalysisError(
            f"{field} must be finite."
        )

    if allow_zero:
        if result < 0:
            raise RiskAnalysisError(
                f"{field} cannot be negative."
            )
    elif result <= 0:
        raise RiskAnalysisError(
            f"{field} must be greater than zero."
        )

    return result


def _validate_policy(
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    defaults = {
        "maximum_risk_percent": DEFAULT_MAXIMUM_RISK_PERCENT,
        "maximum_daily_loss_percent": DEFAULT_MAXIMUM_DAILY_LOSS_PERCENT,
        "maximum_drawdown_percent": DEFAULT_MAXIMUM_DRAWDOWN_PERCENT,
        "maximum_open_risk_percent": DEFAULT_MAXIMUM_OPEN_RISK_PERCENT,
        "maximum_open_positions": DEFAULT_MAXIMUM_OPEN_POSITIONS,
        "maximum_symbol_exposure_lots": (
            DEFAULT_MAXIMUM_SYMBOL_EXPOSURE_LOTS
        ),
        "minimum_reward_to_risk": DEFAULT_MINIMUM_REWARD_TO_RISK,
    }

    if not isinstance(policy, Mapping):
        raise RiskAnalysisError(
            "policy must be a mapping."
        )

    unknown = set(policy.keys()) - set(defaults.keys())

    if unknown:
        raise RiskAnalysisError(
            f"Unknown risk policy fields: {sorted(unknown)}"
        )

    result = dict(defaults)
    result.update(policy)

    percentage_fields = (
        "maximum_risk_percent",
        "maximum_daily_loss_percent",
        "maximum_drawdown_percent",
        "maximum_open_risk_percent",
    )

    for field in percentage_fields:
        result[field] = _number(
            result[field],
            field,
        )

        if result[field] > 100:
            raise RiskAnalysisError(
                f"{field} cannot exceed 100."
            )

    result["minimum_reward_to_risk"] = _number(
        result["minimum_reward_to_risk"],
        "minimum_reward_to_risk",
    )

    positions_limit = result["maximum_open_positions"]

    if (
        isinstance(positions_limit, bool)
        or not isinstance(positions_limit, int)
        or positions_limit < 1
    ):
        raise RiskAnalysisError(
            "maximum_open_positions must be a positive integer."
        )

    result["maximum_symbol_exposure_lots"] = _number(
        result["maximum_symbol_exposure_lots"],
        "maximum_symbol_exposure_lots",
    )

    return result


def _validate_account(
    account: Mapping[str, Any],
) -> dict[str, float]:
    if not isinstance(account, Mapping):
        raise RiskAnalysisError(
            "account must be a mapping."
        )

    required_fields = (
        "equity",
        "balance",
        "daily_start_equity",
        "account_peak_equity",
        "daily_realized_loss",
        "daily_floating_loss",
    )

    missing = [
        field
        for field in required_fields
        if field not in account
    ]

    if missing:
        raise RiskAnalysisError(
            f"Missing required account fields: {missing}"
        )

    return {
        "equity": _number(
            account["equity"],
            "account.equity",
        ),
        "balance": _number(
            account["balance"],
            "account.balance",
        ),
        "daily_start_equity": _number(
            account["daily_start_equity"],
            "account.daily_start_equity",
        ),
        "account_peak_equity": _number(
            account["account_peak_equity"],
            "account.account_peak_equity",
        ),
        "daily_realized_loss": _number(
            account["daily_realized_loss"],
            "account.daily_realized_loss",
            allow_zero=True,
        ),
        "daily_floating_loss": _number(
            account["daily_floating_loss"],
            "account.daily_floating_loss",
            allow_zero=True,
        ),
    }


def _validate_instrument(
    instrument: Mapping[str, Any],
) -> dict[str, float]:
    if not isinstance(instrument, Mapping):
        raise RiskAnalysisError(
            "instrument must be a mapping."
        )

    required = (
        "tick_size",
        "tick_value_per_lot",
        "minimum_volume",
        "maximum_volume",
        "volume_step",
    )

    missing = [
        field
        for field in required
        if field not in instrument
    ]

    if missing:
        raise RiskAnalysisError(
            f"Missing instrument fields: {missing}"
        )

    result = {
        field: _number(
            instrument[field],
            f"instrument.{field}",
        )
        for field in required
    }

    result["commission_per_lot"] = _number(
        instrument.get("commission_per_lot", 0),
        "instrument.commission_per_lot",
        allow_zero=True,
    )

    result["slippage_ticks"] = _number(
        instrument.get("slippage_ticks", 0),
        "instrument.slippage_ticks",
        allow_zero=True,
    )

    result["swap_allowance_per_lot"] = _number(
        instrument.get("swap_allowance_per_lot", 0),
        "instrument.swap_allowance_per_lot",
        allow_zero=True,
    )

    _validate_volume_rules(result)

    return result


def _validate_volume_rules(
    instrument: Mapping[str, Any],
) -> None:
    minimum = instrument["minimum_volume"]
    maximum = instrument["maximum_volume"]
    step = instrument["volume_step"]

    if minimum > maximum:
        raise RiskAnalysisError(
            "Minimum volume exceeds maximum volume."
        )

    minimum_steps = minimum / step

    if abs(minimum_steps - round(minimum_steps)) > VOLUME_TOLERANCE:
        raise RiskAnalysisError(
            "Broker minimum volume is not aligned with volume step."
        )

    maximum_steps = maximum / step

    if abs(maximum_steps - round(maximum_steps)) > VOLUME_TOLERANCE:
        raise RiskAnalysisError(
            "Broker maximum volume is not aligned with volume step."
        )


def _calculate_execution_cost_per_lot(
    instrument: Mapping[str, float],
) -> float:
    """
    Return the estimated total execution cost per lot.

    commission_per_lot must represent the expected total commission
    for opening and closing one lot, not just one side.

    slippage_ticks is a conservative allowance for adverse execution.
    swap_allowance_per_lot is an estimated holding-period allowance.
    """

    cost = (
        instrument["commission_per_lot"]
        + instrument["swap_allowance_per_lot"]
        + (
            instrument["slippage_ticks"]
            * instrument["tick_value_per_lot"]
        )
    )

    if not isfinite(cost) or cost < 0:
        raise RiskAnalysisError(
            "Invalid execution cost allowance."
        )

    return cost


def _calculate_position_loss_per_lot(
    entry_price: float,
    stop_loss: float,
    instrument: Mapping[str, float],
) -> float:
    stop_distance = abs(
        entry_price - stop_loss
    )

    price_loss = (
        stop_distance / instrument["tick_size"]
    ) * instrument["tick_value_per_lot"]

    execution_cost = _calculate_execution_cost_per_lot(
        instrument
    )

    total_loss = price_loss + execution_cost

    if not isfinite(total_loss) or total_loss <= 0:
        raise RiskAnalysisError(
            "Unable to calculate position loss per lot."
        )

    return total_loss


def _calculate_net_reward_per_lot(
    entry_price: float,
    take_profit: float,
    instrument: Mapping[str, float],
) -> dict[str, float]:
    target_distance = abs(
        take_profit - entry_price
    )

    gross_reward = (
        target_distance / instrument["tick_size"]
    ) * instrument["tick_value_per_lot"]

    execution_cost = _calculate_execution_cost_per_lot(
        instrument
    )

    net_reward = max(
        0.0,
        gross_reward - execution_cost,
    )

    if not isfinite(gross_reward) or not isfinite(net_reward):
        raise RiskAnalysisError(
            "Unable to calculate net reward."
        )

    return {
        "gross_reward_per_lot": gross_reward,
        "execution_cost_per_lot": execution_cost,
        "net_reward_per_lot": net_reward,
    }


def _round_volume_down(
    raw_volume: float,
    minimum_volume: float,
    maximum_volume: float,
    volume_step: float,
) -> float:
    if raw_volume < minimum_volume:
        return 0.0

    bounded_volume = min(
        raw_volume,
        maximum_volume,
    )

    steps = floor(
        (bounded_volume / volume_step) + VOLUME_TOLERANCE
    )

    volume = steps * volume_step

    if volume < minimum_volume:
        return 0.0

    return round(volume, 8)


def _calculate_open_position_risk(
    positions: Sequence[Mapping[str, Any]],
    instrument: Mapping[str, float],
) -> dict[str, Any]:
    if not isinstance(positions, Sequence) or isinstance(
        positions, (str, bytes)
    ):
        raise RiskAnalysisError(
            "positions must be a valid sequence."
        )

    total_open_risk = 0.0
    symbol_exposure = 0.0
    position_count = 0
    positions_without_stop = 0

    for index, position in enumerate(positions):
        if not isinstance(position, Mapping):
            raise RiskAnalysisError(
                f"Invalid open position at index {index}."
            )

        symbol = position.get("symbol")

        if (
            not isinstance(symbol, str)
            or symbol.upper().strip() != SUPPORTED_SYMBOL
        ):
            raise RiskAnalysisError(
                "Only XAUUSD open positions are supported. "
                "Other symbols require their own verified specifications."
            )

        side = position.get("side")

        if not isinstance(side, str):
            raise RiskAnalysisError(
                f"Missing position side at index {index}."
            )

        side = side.upper().strip()

        if side not in {"BUY", "SELL"}:
            raise RiskAnalysisError(
                f"Invalid position side at index {index}."
            )

        volume = _number(
            position.get("volume"),
            f"positions[{index}].volume",
        )

        entry = _number(
            position.get("entry_price"),
            f"positions[{index}].entry_price",
        )

        stop_loss = position.get("stop_loss")

        position_count += 1
        symbol_exposure += volume

        if stop_loss is None:
            positions_without_stop += 1
            continue

        stop = _number(
            stop_loss,
            f"positions[{index}].stop_loss",
        )

        if side == "BUY" and stop >= entry:
            raise RiskAnalysisError(
                f"Invalid BUY stop loss at position {index}."
            )

        if side == "SELL" and stop <= entry:
            raise RiskAnalysisError(
                f"Invalid SELL stop loss at position {index}."
            )

        loss_per_lot = _calculate_position_loss_per_lot(
            entry,
            stop,
            instrument,
        )

        position_risk = loss_per_lot * volume

        if not isfinite(position_risk):
            raise RiskAnalysisError(
                f"Invalid calculated risk in position {index}."
            )

        total_open_risk += position_risk

    return {
        "position_count": position_count,
        "gold_exposure_lots": symbol_exposure,
        "estimated_open_risk": total_open_risk,
        "positions_without_stop": positions_without_stop,
    }


def analyze_trade_risk(
    account: Mapping[str, Any],
    instrument: Mapping[str, Any],
    trade: Mapping[str, Any],
    open_positions: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(trade, Mapping):
        raise RiskAnalysisError(
            "trade must be a mapping."
        )

    if policy is None:
        policy = {}

    rules = _validate_policy(policy)
    account_data = _validate_account(account)
    instrument_data = _validate_instrument(instrument)

    symbol = trade.get("symbol")

    if (
        not isinstance(symbol, str)
        or symbol.upper().strip() != SUPPORTED_SYMBOL
    ):
        raise RiskAnalysisError(
            "Only XAUUSD is supported by this risk policy."
        )

    side = trade.get("side")

    if not isinstance(side, str):
        raise RiskAnalysisError(
            "Trade side must be BUY or SELL."
        )

    side = side.upper().strip()

    if side not in {"BUY", "SELL"}:
        raise RiskAnalysisError(
            "Trade side must be BUY or SELL."
        )

    entry = _number(
        trade.get("entry_price"),
        "trade.entry_price",
    )

    stop = _number(
        trade.get("stop_loss"),
        "trade.stop_loss",
    )

    target = _number(
        trade.get("take_profit"),
        "trade.take_profit",
    )

    if side == "BUY":
        if stop >= entry:
            raise RiskAnalysisError(
                "BUY stop loss must be below entry."
            )

        if target <= entry:
            raise RiskAnalysisError(
                "BUY take profit must be above entry."
            )

    else:
        if stop <= entry:
            raise RiskAnalysisError(
                "SELL stop loss must be above entry."
            )

        if target >= entry:
            raise RiskAnalysisError(
                "SELL take profit must be below entry."
            )

    stop_distance = abs(entry - stop)

    loss_per_lot = _calculate_position_loss_per_lot(
        entry,
        stop,
        instrument_data,
    )

    reward_data = _calculate_net_reward_per_lot(
        entry,
        target,
        instrument_data,
    )

    net_reward_per_lot = reward_data["net_reward_per_lot"]

    reward_to_risk = (
        net_reward_per_lot / loss_per_lot
        if loss_per_lot > 0
        else 0.0
    )

    account_equity = account_data["equity"]

    risk_budget = (
        account_equity
        * rules["maximum_risk_percent"]
        / 100.0
    )

    daily_loss_limit = (
        account_data["daily_start_equity"]
        * rules["maximum_daily_loss_percent"]
        / 100.0
    )

    current_daily_loss = (
        account_data["daily_realized_loss"]
        + account_data["daily_floating_loss"]
    )

    current_equity_drawdown = max(
        0.0,
        account_data["account_peak_equity"] - account_equity,
    )

    drawdown_percent = (
        current_equity_drawdown
        / account_data["account_peak_equity"]
    ) * 100.0

    open_risk = _calculate_open_position_risk(
        open_positions,
        instrument_data,
    )

    remaining_daily_loss = max(
        0.0,
        daily_loss_limit - current_daily_loss,
    )

    maximum_open_risk_amount = (
        account_equity
        * rules["maximum_open_risk_percent"]
        / 100.0
    )

    remaining_open_risk = max(
        0.0,
        maximum_open_risk_amount
        - open_risk["estimated_open_risk"],
    )

    allowed_risk = min(
        risk_budget,
        remaining_daily_loss,
        remaining_open_risk,
    )

    raw_volume = allowed_risk / loss_per_lot

    calculated_volume = _round_volume_down(
        raw_volume,
        instrument_data["minimum_volume"],
        instrument_data["maximum_volume"],
        instrument_data["volume_step"],
    )

    rejection_reasons = []

    if current_daily_loss >= daily_loss_limit:
        rejection_reasons.append(
            "DAILY_LOSS_LIMIT_REACHED"
        )

    if drawdown_percent >= rules["maximum_drawdown_percent"]:
        rejection_reasons.append(
            "MAXIMUM_ACCOUNT_DRAWDOWN_REACHED"
        )

    if (
        open_risk["position_count"]
        >= rules["maximum_open_positions"]
    ):
        rejection_reasons.append(
            "MAXIMUM_OPEN_POSITIONS_REACHED"
        )

    if open_risk["positions_without_stop"] > 0:
        rejection_reasons.append(
            "OPEN_POSITION_WITHOUT_STOP_LOSS"
        )

    if (
        open_risk["gold_exposure_lots"]
        >= rules["maximum_symbol_exposure_lots"]
    ):
        rejection_reasons.append(
            "MAXIMUM_GOLD_EXPOSURE_REACHED"
        )

    if (
        open_risk["gold_exposure_lots"] + calculated_volume
        > rules["maximum_symbol_exposure_lots"] + VOLUME_TOLERANCE
    ):
        rejection_reasons.append(
            "PROPOSED_TRADE_EXCEEDS_GOLD_EXPOSURE_LIMIT"
        )

    if reward_to_risk < rules["minimum_reward_to_risk"]:
        rejection_reasons.append(
            "NET_REWARD_TO_RISK_BELOW_MINIMUM"
        )

    if calculated_volume <= 0:
        rejection_reasons.append(
            "CALCULATED_VOLUME_BELOW_BROKER_MINIMUM"
        )

    estimated_trade_risk = (
        calculated_volume * loss_per_lot
    )

    estimated_gross_reward = (
        calculated_volume
        * reward_data["gross_reward_per_lot"]
    )

    estimated_execution_cost = (
        calculated_volume
        * reward_data["execution_cost_per_lot"]
    )

    estimated_net_reward = (
        calculated_volume * net_reward_per_lot
    )

    approved = not rejection_reasons

    return {
        "module": "risk_management_intelligence",
        "symbol": SUPPORTED_SYMBOL,
        "approved": approved,
        "execution_authorized": False,
        "decision": (
            "RISK_APPROVED"
            if approved
            else "RISK_REJECTED"
        ),
        "rejection_reasons": rejection_reasons,
        "position_sizing": {
            "maximum_risk_percent": rules["maximum_risk_percent"],
            "risk_budget": risk_budget,
            "allowed_risk_after_limits": allowed_risk,
            "loss_per_lot_including_allowances": loss_per_lot,
            "calculated_volume_lots": calculated_volume,
            "estimated_trade_risk": estimated_trade_risk,
            "gross_reward_per_lot": (
                reward_data["gross_reward_per_lot"]
            ),
            "execution_cost_per_lot": (
                reward_data["execution_cost_per_lot"]
            ),
            "net_reward_per_lot": net_reward_per_lot,
            "estimated_gross_reward": estimated_gross_reward,
            "estimated_execution_cost": estimated_execution_cost,
            "estimated_net_reward": estimated_net_reward,
            "net_reward_to_risk": reward_to_risk,
            "minimum_required_reward_to_risk": (
                rules["minimum_reward_to_risk"]
            ),
        },
        "account_protection": {
            "equity": account_equity,
            "balance": account_data["balance"],
            "daily_loss_limit": daily_loss_limit,
            "daily_realized_loss": account_data["daily_realized_loss"],
            "daily_floating_loss": account_data["daily_floating_loss"],
            "combined_daily_loss": current_daily_loss,
            "remaining_daily_loss": remaining_daily_loss,
            "account_drawdown_percent": drawdown_percent,
            "maximum_drawdown_percent": (
                rules["maximum_drawdown_percent"]
            ),
            "open_risk": open_risk["estimated_open_risk"],
            "maximum_open_risk": maximum_open_risk_amount,
            "remaining_open_risk": remaining_open_risk,
        },
        "exposure": {
            "current_gold_lots": open_risk["gold_exposure_lots"],
            "maximum_gold_lots": (
                rules["maximum_symbol_exposure_lots"]
            ),
            "open_position_count": open_risk["position_count"],
            "maximum_open_positions": (
                rules["maximum_open_positions"]
            ),
        },
        "execution_cost_allowances": {
            "commission_per_lot": instrument_data["commission_per_lot"],
            "slippage_ticks": instrument_data["slippage_ticks"],
            "swap_allowance_per_lot": (
                instrument_data["swap_allowance_per_lot"]
            ),
            "commission_is_expected_round_trip": True,
        },
        "limitations": [
            "This module never executes or modifies trading orders.",
            "Risk approval is not execution authorization.",
            "Daily floating loss must be supplied by a trusted account source.",
            "Account loss figures must be measured from the correct daily baseline.",
            "Only XAUUSD open positions are accepted.",
            "Tick size and tick value must come from verified broker specifications.",
            "Commission allowance must represent the expected round-trip commission.",
            "Cost allowances are estimates and may differ from actual execution costs.",
            "Gap risk can cause losses beyond the estimated stop-loss amount.",
            "Pending orders and margin requirements are not calculated here.",
            "Broker-side validation is mandatory before any order.",
            "A risk approval is not a prediction or guarantee of profit.",
        ],
    }
