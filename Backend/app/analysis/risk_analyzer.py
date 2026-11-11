"""
GOLD BOT - Risk Management Intelligence

Independent capital protection and trade risk validation.

Responsibilities:

- Risk-based position sizing
- Stop-loss validation
- Reward-to-risk calculation
- Daily loss protection
- Account drawdown protection
- Open position risk aggregation
- Maximum exposure controls
- Position count limits
- Broker volume constraints

Important:

- This module never executes orders.
- It does not guarantee a maximum realized loss.
- Broker contract specifications must be verified.
- All monetary values must use the account currency.
  """

from collections.abc import Mapping, Sequence
from math import floor, isfinite
from typing import Any

DEFAULT_MAXIMUM_RISK_PERCENT = 1.0
DEFAULT_MAXIMUM_DAILY_LOSS_PERCENT = 3.0
DEFAULT_MAXIMUM_DRAWDOWN_PERCENT = 10.0

DEFAULT_MAXIMUM_OPEN_RISK_PERCENT = 2.0
DEFAULT_MAXIMUM_OPEN_POSITIONS = 3
DEFAULT_MAXIMUM_SYMBOL_EXPOSURE_LOTS = 2.0

MINIMUM_REWARD_TO_RISK = 1.5

SUPPORTED_SYMBOL = "XAUUSD"

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
except (TypeError, ValueError):
    raise RiskAnalysisError(
        f"{field} must be numeric."
    )

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
    "maximum_symbol_exposure_lots": DEFAULT_MAXIMUM_SYMBOL_EXPOSURE_LOTS,
    "minimum_reward_to_risk": MINIMUM_REWARD_TO_RISK,
}

if not isinstance(policy, Mapping):
    raise RiskAnalysisError(
        "policy must be a mapping."
    )

result = dict(defaults)

for key in defaults:
    if key in policy:
        result[key] = policy[key]

percentage_fields = [
    "maximum_risk_percent",
    "maximum_daily_loss_percent",
    "maximum_drawdown_percent",
    "maximum_open_risk_percent",
    "minimum_reward_to_risk",
]

for field in percentage_fields:
    result[field] = _number(
        result[field],
        field,
    )

if result["maximum_risk_percent"] > 100:
    raise RiskAnalysisError(
        "Maximum risk percent cannot exceed 100."
    )

if result["maximum_daily_loss_percent"] > 100:
    raise RiskAnalysisError(
        "Maximum daily loss percent cannot exceed 100."
    )

if result["maximum_drawdown_percent"] > 100:
    raise RiskAnalysisError(
        "Maximum drawdown percent cannot exceed 100."
    )

if result["maximum_open_risk_percent"] > 100:
    raise RiskAnalysisError(
        "Maximum open risk percent cannot exceed 100."
    )

for field in [
    "maximum_open_positions",
]:
    value = result[field]

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
    ):
        raise RiskAnalysisError(
            f"{field} must be a positive integer."
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

equity = _number(
    account.get("equity"),
    "account.equity",
)

balance = _number(
    account.get("balance"),
    "account.balance",
)

daily_start_equity = _number(
    account.get("daily_start_equity"),
    "account.daily_start_equity",
)

account_peak_equity = _number(
    account.get("account_peak_equity"),
    "account.account_peak_equity",
)

daily_realized_loss = _number(
    account.get("daily_realized_loss", 0),
    "account.daily_realized_loss",
    allow_zero=True,
)

return {
    "equity": equity,
    "balance": balance,
    "daily_start_equity": daily_start_equity,
    "account_peak_equity": account_peak_equity,
    "daily_realized_loss": daily_realized_loss,
}

def _validate_instrument(
instrument: Mapping[str, Any],
) -> dict[str, float]:

if not isinstance(instrument, Mapping):
    raise RiskAnalysisError(
        "instrument must be a mapping."
    )

return {
    "tick_size": _number(
        instrument.get("tick_size"),
        "instrument.tick_size",
    ),
    "tick_value_per_lot": _number(
        instrument.get("tick_value_per_lot"),
        "instrument.tick_value_per_lot",
    ),
    "minimum_volume": _number(
        instrument.get("minimum_volume"),
        "instrument.minimum_volume",
    ),
    "maximum_volume": _number(
        instrument.get("maximum_volume"),
        "instrument.maximum_volume",
    ),
    "volume_step": _number(
        instrument.get("volume_step"),
        "instrument.volume_step",
    ),
}

def _validate_volume_rules(
instrument: Mapping[str, Any],
) -> None:

if (
    instrument["minimum_volume"]
    > instrument["maximum_volume"]
):
    raise RiskAnalysisError(
        "Minimum volume exceeds maximum volume."
    )

if (
    instrument["minimum_volume"]
    < instrument["volume_step"] * 0.5
):
    raise RiskAnalysisError(
        "Invalid minimum volume relative to volume step."
    )

def _calculate_position_loss_per_lot(
entry_price: float,
stop_loss: float,
tick_size: float,
tick_value_per_lot: float,
) -> float:

stop_distance = abs(
    entry_price - stop_loss
)

loss_per_lot = (
    stop_distance / tick_size
) * tick_value_per_lot

if not isfinite(loss_per_lot) or loss_per_lot <= 0:
    raise RiskAnalysisError(
        "Unable to calculate position loss per lot."
    )

return loss_per_lot

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
    (bounded_volume + 1e-12) / volume_step
)

volume = steps * volume_step

if volume < minimum_volume:
    return 0.0

return round(volume, 8)

def _calculate_open_position_risk(
positions: Sequence[Mapping[str, Any]],
tick_size: float,
tick_value_per_lot: float,
) -> dict[str, Any]:

if isinstance(positions, (str, bytes)):
    raise RiskAnalysisError(
        "positions must be a sequence."
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

    volume = _number(
        position.get("volume"),
        f"positions[{index}].volume",
    )

    symbol = position.get("symbol")

    if not isinstance(symbol, str) or not symbol.strip():
        raise RiskAnalysisError(
            f"Missing symbol in position {index}."
        )

    position_count += 1

    if symbol.upper().strip() == SUPPORTED_SYMBOL:
        symbol_exposure += volume

    stop_loss = position.get("stop_loss")
    entry_price = position.get("entry_price")

    if stop_loss is None:
        positions_without_stop += 1
        continue

    entry = _number(
        entry_price,
        f"positions[{index}].entry_price",
    )

    stop = _number(
        stop_loss,
        f"positions[{index}].stop_loss",
    )

    stop_distance = abs(entry - stop)

    position_risk = (
        stop_distance / tick_size
    ) * tick_value_per_lot * volume

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

account_data = _validate_account(
    account
)

instrument_data = _validate_instrument(
    instrument
)

_validate_volume_rules(
    instrument_data
)

symbol = trade.get("symbol")

if (
    not isinstance(symbol, str)
    or symbol.upper().strip() != SUPPORTED_SYMBOL
):
    raise RiskAnalysisError(
        "Only XAUUSD is supported by this risk policy."
    )

side = trade.get("side")

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
    if not stop < entry:
        raise RiskAnalysisError(
            "BUY stop loss must be below entry."
        )

    if not target > entry:
        raise RiskAnalysisError(
            "BUY take profit must be above entry."
        )

else:
    if not stop > entry:
        raise RiskAnalysisError(
            "SELL stop loss must be above entry."
        )

    if not target < entry:
        raise RiskAnalysisError(
            "SELL take profit must be below entry."
        )

stop_distance = abs(
    entry - stop
)

target_distance = abs(
    target - entry
)

reward_to_risk = (
    target_distance / stop_distance
)

loss_per_lot = _calculate_position_loss_per_lot(
    entry,
    stop,
    instrument_data["tick_size"],
    instrument_data["tick_value_per_lot"],
)

reward_per_lot = (
    target_distance
    / instrument_data["tick_size"]
) * instrument_data["tick_value_per_lot"]

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

current_equity_drawdown = max(
    0.0,
    (
        account_data["account_peak_equity"]
        - account_equity
    ),
)

drawdown_percent = (
    current_equity_drawdown
    / account_data["account_peak_equity"]
) * 100.0

open_risk = _calculate_open_position_risk(
    open_positions,
    instrument_data["tick_size"],
    instrument_data["tick_value_per_lot"],
)

current_daily_loss = (
    account_data["daily_realized_loss"]
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

raw_volume = (
    allowed_risk / loss_per_lot
)

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
    open_risk["gold_exposure_lots"]
    + calculated_volume
    > rules["maximum_symbol_exposure_lots"] + 1e-9
):
    rejection_reasons.append(
        "PROPOSED_TRADE_EXCEEDS_GOLD_EXPOSURE_LIMIT"
    )

if reward_to_risk < rules["minimum_reward_to_risk"]:
    rejection_reasons.append(
        "REWARD_TO_RISK_BELOW_MINIMUM"
    )

if calculated_volume <= 0:
    rejection_reasons.append(
        "CALCULATED_VOLUME_BELOW_BROKER_MINIMUM"
    )

estimated_trade_risk = (
    calculated_volume * loss_per_lot
)

estimated_reward = (
    calculated_volume * reward_per_lot
)

approved = not rejection_reasons

return {
    "module": "risk_management_intelligence",
    "symbol": SUPPORTED_SYMBOL,
    "approved": approved,
    "decision": (
        "RISK_APPROVED"
        if approved
        else "RISK_REJECTED"
    ),
    "rejection_reasons": rejection_reasons,
    "position_sizing": {
        "maximum_risk_percent": (
            rules["maximum_risk_percent"]
        ),
        "risk_budget": risk_budget,
        "allowed_risk_after_limits": allowed_risk,
        "loss_per_lot": loss_per_lot,
        "calculated_volume_lots": calculated_volume,
        "estimated_trade_risk": estimated_trade_risk,
        "estimated_reward": estimated_reward,
        "reward_to_risk": reward_to_risk,
    },
    "account_protection": {
        "equity": account_equity,
        "balance": account_data["balance"],
        "daily_loss_limit": daily_loss_limit,
        "daily_realized_loss": current_daily_loss,
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
        "current_gold_lots": (
            open_risk["gold_exposure_lots"]
        ),
        "maximum_gold_lots": (
            rules["maximum_symbol_exposure_lots"]
        ),
        "open_position_count": (
            open_risk["position_count"]
        ),
        "maximum_open_positions": (
            rules["maximum_open_positions"]
        ),
    },
    "limitations": [
        "This module never executes or modifies trading orders.",
        "Loss estimates exclude commissions, swaps, slippage and gaps.",
        "Tick value and tick size must come from verified broker specifications.",
        "Daily realized loss must include all relevant account losses.",
        "Open positions must include all relevant account exposure.",
        "Open position risk is estimated using supplied stop losses.",
        "Positions without stop losses cause rejection.",
        "Pending orders and margin requirements are not calculated here.",
        "A risk approval is not a prediction or guarantee of profit.",
        "A broker-side execution check is mandatory before any order.",
    ],
               }
