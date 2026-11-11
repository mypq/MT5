"""
GOLD BOT - Realistic Backtesting Engine

Candle-based historical strategy simulation.

Execution model:

- Signals are evaluated at candle close.
- Orders enter at the NEXT candle open.
- Spread and adverse slippage are applied to fills.
- Commission and estimated swap are deducted.
- Stop-loss and take-profit are checked on subsequent candles.
- If both SL and TP are touched in one candle, SL is assumed first.
- Remaining positions are closed at the final available candle.

IMPORTANT:

- This is a simplified OHLC backtester, not a tick-level simulator.
- It supports one open position at a time.
- Signals must be generated without future data.
- Broker-specific execution rules must be calibrated separately.
- No live orders are sent.
  """

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite
from typing import Any

class BacktestError(ValueError):
"""Raised when backtesting input is invalid."""

MAXIMUM_CANDLES = 500000
MAXIMUM_SIGNALS = 500000

def _number(value: Any, field: str) -> float:

if isinstance(value, bool):
    raise BacktestError(
        f"{field} must be numeric."
    )

try:
    result = float(value)
except (TypeError, ValueError):
    raise BacktestError(
        f"{field} must be numeric."
    )

if not isfinite(result):
    raise BacktestError(
        f"{field} must be finite."
    )

return result

def _positive(value: Any, field: str) -> float:

result = _number(value, field)

if result <= 0:
    raise BacktestError(
        f"{field} must be positive."
    )

return result

def _timestamp(value: Any, field: str) -> datetime:

if not isinstance(value, str):
    raise BacktestError(
        f"{field} must be ISO-8601."
    )

try:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
except ValueError:
    raise BacktestError(
        f"{field} is invalid."
    )

if parsed.tzinfo is None:
    raise BacktestError(
        f"{field} must include a timezone."
    )

return parsed.astimezone(timezone.utc)

def _validate_candles(
candles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:

if (
    isinstance(candles, (str, bytes))
    or not isinstance(candles, Sequence)
):
    raise BacktestError(
        "candles must be a sequence."
    )

if not 2 <= len(candles) <= MAXIMUM_CANDLES:
    raise BacktestError(
        "Invalid candle count."
    )

validated = []
previous_time = None

for index, candle in enumerate(candles):

    if not isinstance(candle, Mapping):
        raise BacktestError(
            f"Candle {index} must be a mapping."
        )

    timestamp = _timestamp(
        candle.get("timestamp"),
        f"candles[{index}].timestamp",
    )

    open_price = _positive(
        candle.get("open"),
        f"candles[{index}].open",
    )

    high = _positive(
        candle.get("high"),
        f"candles[{index}].high",
    )

    low = _positive(
        candle.get("low"),
        f"candles[{index}].low",
    )

    close = _positive(
        candle.get("close"),
        f"candles[{index}].close",
    )

    if high < low:
        raise BacktestError(
            f"Candle {index} has high below low."
        )

    if not low <= open_price <= high:
        raise BacktestError(
            f"Candle {index} open is outside its range."
        )

    if not low <= close <= high:
        raise BacktestError(
            f"Candle {index} close is outside its range."
        )

    if (
        previous_time is not None
        and timestamp <= previous_time
    ):
        raise BacktestError(
            "Candle timestamps must be strictly increasing."
        )

    previous_time = timestamp

    validated.append({
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
    })

return validated

def _validate_signals(
signals: Sequence[Mapping[str, Any]],
candle_count: int,
) -> dict[int, dict[str, Any]]:

if (
    isinstance(signals, (str, bytes))
    or not isinstance(signals, Sequence)
):
    raise BacktestError(
        "signals must be a sequence."
    )

if len(signals) > MAXIMUM_SIGNALS:
    raise BacktestError(
        "Too many signals."
    )

validated = {}

for index, signal in enumerate(signals):

    if not isinstance(signal, Mapping):
        raise BacktestError(
            f"Signal {index} must be a mapping."
        )

    candle_index = signal.get(
        "candle_index"
    )

    if (
        isinstance(candle_index, bool)
        or not isinstance(candle_index, int)
        or not 0 <= candle_index < candle_count - 1
    ):
        raise BacktestError(
            f"Signal {index} has invalid candle_index."
        )

    if candle_index in validated:
        raise BacktestError(
            f"Duplicate signal for candle {candle_index}."
        )

    direction = str(
        signal.get("direction", "WAIT")
    ).strip().upper()

    if direction not in {
        "BUY",
        "SELL",
        "WAIT",
        "NO_TRADE",
    }:
        raise BacktestError(
            f"Signal {index} has invalid direction."
        )

    stop_loss = signal.get("stop_loss")
    take_profit = signal.get("take_profit")

    if direction in {"BUY", "SELL"}:

        stop_loss = _positive(
            stop_loss,
            f"signals[{index}].stop_loss",
        )

        take_profit = _positive(
            take_profit,
            f"signals[{index}].take_profit",
        )

    validated[candle_index] = {
        "direction": direction,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }

return validated

def _execution_price(
raw_price: float,
side: str,
is_entry: bool,
spread_price: float,
slippage_price: float,
) -> float:

# BUY entries and SELL exits are executed higher.
# SELL entries and BUY exits are executed lower.

adverse_up = (
    (side == "BUY" and is_entry)
    or (side == "SELL" and not is_entry)
)

adjustment = (
    spread_price / 2.0
    + slippage_price
)

result = (
    raw_price + adjustment
    if adverse_up
    else raw_price - adjustment
)

if result <= 0:
    raise BacktestError(
        "Execution costs produced a non-positive price."
    )

return result

def _calculate_pnl(
side: str,
entry_price: float,
exit_price: float,
volume: float,
tick_size: float,
tick_value_per_lot: float,
) -> float:

price_difference = (
    exit_price - entry_price
    if side == "BUY"
    else entry_price - exit_price
)

return (
    price_difference / tick_size
) * tick_value_per_lot * volume

def _maximum_drawdown(
equity_curve: Sequence[float],
) -> tuple[float, float]:

peak = equity_curve[0]

maximum_amount = 0.0
maximum_percent = 0.0

for equity in equity_curve:

    peak = max(
        peak,
        equity,
    )

    drawdown = peak - equity

    drawdown_percent = (
        drawdown / peak * 100.0
        if peak > 0
        else 0.0
    )

    maximum_amount = max(
        maximum_amount,
        drawdown,
    )

    maximum_percent = max(
        maximum_percent,
        drawdown_percent,
    )

return (
    maximum_amount,
    maximum_percent,
)

def run_backtest(
candles: Sequence[Mapping[str, Any]],
signals: Sequence[Mapping[str, Any]],
initial_capital: float,
volume_lots: float,
tick_size: float,
tick_value_per_lot: float,
spread_price: float = 0.0,
slippage_ticks: float = 0.0,
commission_per_lot_round_turn: float = 0.0,
swap_per_lot_per_day: float = 0.0,
symbol: str = "XAUUSD",
) -> dict[str, Any]:

capital = _positive(
    initial_capital,
    "initial_capital",
)

volume = _positive(
    volume_lots,
    "volume_lots",
)

tick_size = _positive(
    tick_size,
    "tick_size",
)

tick_value = _positive(
    tick_value_per_lot,
    "tick_value_per_lot",
)

spread = _number(
    spread_price,
    "spread_price",
)

slippage = _number(
    slippage_ticks,
    "slippage_ticks",
)

commission = _number(
    commission_per_lot_round_turn,
    "commission_per_lot_round_turn",
)

swap = _number(
    swap_per_lot_per_day,
    "swap_per_lot_per_day",
)

if spread < 0 or slippage < 0:
    raise BacktestError(
        "Spread and slippage cannot be negative."
    )

if commission < 0:
    raise BacktestError(
        "Commission cannot be negative."
    )

if not isinstance(symbol, str) or not symbol.strip():
    raise BacktestError(
        "symbol cannot be empty."
    )

validated_candles = _validate_candles(
    candles
)

validated_signals = _validate_signals(
    signals,
    len(validated_candles),
)

slippage_price = slippage * tick_size

equity = capital
equity_curve = [equity]

trades = []
position = None

skipped_signals = []

def close_position(
    raw_exit_price: float,
    exit_time: datetime,
    exit_reason: str,
) -> None:

    nonlocal equity, position

    if position is None:
        return

    side = position["side"]

    executed_exit = _execution_price(
        raw_exit_price,
        side,
        False,
        spread,
        slippage_price,
    )

    gross_pnl = _calculate_pnl(
        side,
        position["entry_price"],
        executed_exit,
        volume,
        tick_size,
        tick_value,
    )

    holding_seconds = max(
        0.0,
        (
            exit_time - position["entry_time"]
        ).total_seconds(),
    )

    holding_days = (
        holding_seconds / 86400.0
    )

    commission_cost = (
        commission * volume
    )

    swap_cost = (
        swap * volume * holding_days
    )

    net_pnl = (
        gross_pnl
        - commission_cost
        - swap_cost
    )

    equity += net_pnl

    trades.append({
        "side": side,
        "entry_time": position["entry_time"].isoformat(),
        "exit_time": exit_time.isoformat(),
        "entry_price": position["entry_price"],
        "exit_price": executed_exit,
        "stop_loss": position["stop_loss"],
        "take_profit": position["take_profit"],
        "exit_reason": exit_reason,
        "volume_lots": volume,
        "gross_pnl": gross_pnl,
        "commission_cost": commission_cost,
        "swap_cost": swap_cost,
        "net_pnl": net_pnl,
        "holding_seconds": holding_seconds,
    })

    equity_curve.append(equity)

    position = None

for index, candle in enumerate(
    validated_candles
):

    # Existing positions are evaluated first.
    # New signals from this candle cannot enter
    # until the next candle open.

    if position is not None:

        stop = position["stop_loss"]
        target = position["take_profit"]
        side = position["side"]

        if side == "BUY":

            stop_hit = candle["low"] <= stop
            target_hit = candle["high"] >= target

        else:

            stop_hit = candle["high"] >= stop
            target_hit = candle["low"] <= target

        if stop_hit:

            # Conservative OHLC assumption:
            # if both stop and target are touched,
            # assume the stop was reached first.

            close_position(
                stop,
                candle["timestamp"],
                "STOP_LOSS",
            )

        elif target_hit:

            close_position(
                target,
                candle["timestamp"],
                "TAKE_PROFIT",
            )

    if position is not None:
        continue

    signal = validated_signals.get(
        index
    )

    if signal is None:
        continue

    direction = signal["direction"]

    if direction not in {"BUY", "SELL"}:
        continue

    next_candle = validated_candles[
        index + 1
    ]

    raw_entry = next_candle["open"]

    executed_entry = _execution_price(
        raw_entry,
        direction,
        True,
        spread,
        slippage_price,
    )

    stop = signal["stop_loss"]
    target = signal["take_profit"]

    if direction == "BUY":

        valid_levels = (
            stop < executed_entry < target
        )

    else:

        valid_levels = (
            target < executed_entry < stop
        )

    if not valid_levels:

        skipped_signals.append({
            "candle_index": index,
            "reason": "INVALID_LEVELS_AFTER_ENTRY_GAP",
        })

        continue

    position = {
        "side": direction,
        "entry_time": next_candle["timestamp"],
        "entry_price": executed_entry,
        "stop_loss": stop,
        "take_profit": target,
        "signal_candle_index": index,
    }

if position is not None:

    final_candle = validated_candles[-1]

    close_position(
        final_candle["close"],
        final_candle["timestamp"],
        "END_OF_DATA",
    )

winners = [
    trade for trade in trades
    if trade["net_pnl"] > 0
]

losers = [
    trade for trade in trades
    if trade["net_pnl"] < 0
]

gross_profit = sum(
    trade["net_pnl"]
    for trade in winners
)

gross_loss = abs(
    sum(
        trade["net_pnl"]
        for trade in losers
    )
)

total_net_profit = sum(
    trade["net_pnl"]
    for trade in trades
)

trade_count = len(trades)

win_rate = (
    len(winners) / trade_count * 100.0
    if trade_count
    else 0.0
)

profit_factor = (
    gross_profit / gross_loss
    if gross_loss > 0
    else None
)

expectancy = (
    total_net_profit / trade_count
    if trade_count
    else 0.0
)

maximum_drawdown_amount, maximum_drawdown_percent = (
    _maximum_drawdown(
        equity_curve
    )
)

total_costs = sum(
    trade["commission_cost"]
    + trade["swap_cost"]
    for trade in trades
)

total_gross_pnl = sum(
    trade["gross_pnl"]
    for trade in trades
)

return {
    "module": "realistic_backtesting_engine",
    "symbol": symbol.strip().upper(),
    "initial_capital": capital,
    "final_equity": round(
        equity,
        2,
    ),
    "net_profit": round(
        total_net_profit,
        2,
    ),
    "return_percent": round(
        total_net_profit / capital * 100.0,
        2,
    ),
    "total_trades": trade_count,
    "winning_trades": len(winners),
    "losing_trades": len(losers),
    "breakeven_trades": (
        trade_count
        - len(winners)
        - len(losers)
    ),
    "win_rate_percent": round(
        win_rate,
        2,
    ),
    "profit_factor": (
        round(profit_factor, 4)
        if profit_factor is not None
        else None
    ),
    "expectancy_per_trade": round(
        expectancy,
        4,
    ),
    "maximum_drawdown_amount": round(
        maximum_drawdown_amount,
        2,
    ),
    "maximum_drawdown_percent": round(
        maximum_drawdown_percent,
        2,
    ),
    "gross_pnl_before_commission_and_swap": round(
        total_gross_pnl,
        2,
    ),
    "commission_and_swap_costs": round(
        total_costs,
        2,
    ),
    "equity_curve": equity_curve,
    "trades": trades,
    "skipped_signals": skipped_signals,
    "execution_model": {
        "entry": "NEXT_CANDLE_OPEN",
        "spread_model": "HALF_SPREAD_PER_SIDE",
        "slippage_model": "FIXED_ADVERSE_SLIPPAGE",
        "same_candle_stop_target": "STOP_FIRST",
        "position_limit": 1,
        "end_of_data": "FORCED_CLOSE",
    },
    "limitations": [
        "OHLC candles cannot reveal the exact intrabar price path.",
        "Stop-first handling is conservative but cannot reproduce actual tick execution.",
        "Only one open position is supported.",
        "Position volume is fixed for the entire test.",
        "No partial fills or order-book liquidity simulation.",
        "No pending orders or multiple simultaneous strategies.",
        "Swap is approximated continuously by holding duration and does not model broker triple-swap days.",
        "Commission is modeled as a fixed round-turn amount per lot.",
        "Spread and slippage are fixed, not dynamic.",
        "No margin call or stop-out simulation.",
        "No automatic prevention of overlapping strategy signals beyond the single-position rule.",
        "Historical results do not guarantee future performance.",
        "Signals must be independently generated without look-ahead bias.",
    ],
    "live_execution_authorized": False,
  }
