"""
GOLD BOT - Strategy Research Lab

Quantitative strategy evaluation and research utilities.

Capabilities:

- Historical trade performance analysis
- Equity curve and drawdown calculations
- Profit factor and expectancy
- Win/loss statistics
- Out-of-sample comparison
- Walk-forward window generation
- Parameter experiment ranking
- Monte Carlo trade-sequence simulation

IMPORTANT:

- This module does not execute trades.
- Backtest results are not guarantees of future performance.
- Transaction costs must be included in supplied trade results.
- Strategy acceptance requires independent validation.
  """

from collections.abc import Mapping, Sequence
from math import isfinite, sqrt
from statistics import mean, median
from typing import Any
import random

MAXIMUM_TRADES = 100000
MAXIMUM_EXPERIMENTS = 10000
MAXIMUM_MONTE_CARLO_SIMULATIONS = 10000

MINIMUM_TRADES_FOR_EVALUATION = 10

class StrategyResearchError(ValueError):
"""Raised when research input is invalid."""

def _finite_number(
value: Any,
field: str,
) -> float:

if isinstance(value, bool):
    raise StrategyResearchError(
        f"{field} must be numeric."
    )

try:
    result = float(value)
except (TypeError, ValueError):
    raise StrategyResearchError(
        f"{field} must be numeric."
    )

if not isfinite(result):
    raise StrategyResearchError(
        f"{field} must be finite."
    )

return result

def _validate_trades(
trades: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:

if (
    isinstance(trades, (str, bytes))
    or not isinstance(trades, Sequence)
):
    raise StrategyResearchError(
        "Trades must be a sequence."
    )

if not (
    1 <= len(trades) <= MAXIMUM_TRADES
):
    raise StrategyResearchError(
        f"Trades must contain between 1 and "
        f"{MAXIMUM_TRADES} records."
    )

validated = []

for index, trade in enumerate(trades):

    if not isinstance(trade, Mapping):
        raise StrategyResearchError(
            f"Trade {index} must be a mapping."
        )

    pnl = _finite_number(
        trade.get("net_pnl"),
        f"trades[{index}].net_pnl",
    )

    validated.append({
        "net_pnl": pnl,
        "strategy": str(
            trade.get("strategy", "UNSPECIFIED")
        ),
    })

return validated

def _equity_curve(
pnls: Sequence[float],
initial_capital: float,
) -> list[float]:

equity = initial_capital
curve = [equity]

for pnl in pnls:
    equity += pnl
    curve.append(equity)

return curve

def _drawdown_statistics(
equity_curve: Sequence[float],
) -> dict[str, float]:

peak = equity_curve[0]

maximum_drawdown = 0.0
maximum_drawdown_percent = 0.0

for equity in equity_curve:

    peak = max(peak, equity)

    drawdown = peak - equity

    drawdown_percent = (
        drawdown / peak * 100.0
        if peak > 0
        else 0.0
    )

    maximum_drawdown = max(
        maximum_drawdown,
        drawdown,
    )

    maximum_drawdown_percent = max(
        maximum_drawdown_percent,
        drawdown_percent,
    )

return {
    "maximum_drawdown": maximum_drawdown,
    "maximum_drawdown_percent": (
        maximum_drawdown_percent
    ),
}

def evaluate_strategy(
trades: Sequence[Mapping[str, Any]],
initial_capital: float = 10000.0,
) -> dict[str, Any]:

capital = _finite_number(
    initial_capital,
    "initial_capital",
)

if capital <= 0:
    raise StrategyResearchError(
        "Initial capital must be positive."
    )

validated = _validate_trades(trades)

pnls = [
    trade["net_pnl"]
    for trade in validated
]

winners = [
    pnl for pnl in pnls
    if pnl > 0
]

losers = [
    pnl for pnl in pnls
    if pnl < 0
]

breakeven = [
    pnl for pnl in pnls
    if pnl == 0
]

gross_profit = sum(winners)

gross_loss = abs(sum(losers))

net_profit = sum(pnls)

final_capital = capital + net_profit

win_rate = (
    len(winners) / len(pnls) * 100.0
)

average_win = (
    mean(winners)
    if winners
    else 0.0
)

average_loss = (
    abs(mean(losers))
    if losers
    else 0.0
)

expectancy = mean(pnls)

profit_factor = (
    gross_profit / gross_loss
    if gross_loss > 0
    else None
)

equity_curve = _equity_curve(
    pnls,
    capital,
)

drawdown = _drawdown_statistics(
    equity_curve
)

average_win_loss_ratio = (
    average_win / average_loss
    if average_loss > 0
    else None
)

total_return_percent = (
    net_profit / capital * 100.0
)

average_trade_percent = (
    mean([
        pnl / capital * 100.0
        for pnl in pnls
    ])
)

return {
    "module": "strategy_research_lab",
    "trade_count": len(pnls),
    "winning_trades": len(winners),
    "losing_trades": len(losers),
    "breakeven_trades": len(breakeven),
    "initial_capital": capital,
    "final_capital": final_capital,
    "gross_profit": gross_profit,
    "gross_loss": gross_loss,
    "net_profit": net_profit,
    "total_return_percent": total_return_percent,
    "win_rate_percent": win_rate,
    "average_win": average_win,
    "average_loss": average_loss,
    "average_win_loss_ratio": average_win_loss_ratio,
    "expectancy_per_trade": expectancy,
    "average_trade_return_percent": (
        average_trade_percent
    ),
    "profit_factor": profit_factor,
    "maximum_drawdown": (
        drawdown["maximum_drawdown"]
    ),
    "maximum_drawdown_percent": (
        drawdown["maximum_drawdown_percent"]
    ),
    "equity_curve": equity_curve,
    "median_trade_pnl": median(pnls),
    "research_status": (
        "INSUFFICIENT_SAMPLE"
        if len(pnls) < MINIMUM_TRADES_FOR_EVALUATION
        else "EVALUATION_AVAILABLE"
    ),
    "limitations": [
        "Results depend on the quality of supplied trade records.",
        "Net PnL must already include commissions, swaps, and slippage.",
        "Drawdown is calculated from trade-by-trade equity, not intratrade prices.",
        "No annualized return or Sharpe ratio is calculated without time-series and risk-free-rate assumptions.",
        "No future performance is guaranteed.",
    ],
}

def compare_out_of_sample(
training_trades: Sequence[Mapping[str, Any]],
testing_trades: Sequence[Mapping[str, Any]],
initial_capital: float = 10000.0,
) -> dict[str, Any]:

training = evaluate_strategy(
    training_trades,
    initial_capital,
)

testing = evaluate_strategy(
    testing_trades,
    initial_capital,
)

training_expectancy = (
    training["expectancy_per_trade"]
)

testing_expectancy = (
    testing["expectancy_per_trade"]
)

if training_expectancy > 0:

    expectancy_retention = (
        testing_expectancy
        / training_expectancy
        * 100.0
    )

else:
    expectancy_retention = None

if (
    training["research_status"] == "INSUFFICIENT_SAMPLE"
    or testing["research_status"] == "INSUFFICIENT_SAMPLE"
):
    status = "INSUFFICIENT_SAMPLE"

elif (
    training_expectancy <= 0
    or testing_expectancy <= 0
):
    status = "OUT_OF_SAMPLE_FAILURE"

elif testing_expectancy < training_expectancy * 0.5:
    status = "SIGNIFICANT_PERFORMANCE_DECAY"

else:
    status = "OUT_OF_SAMPLE_POSITIVE"

return {
    "module": "strategy_research_lab",
    "training": training,
    "out_of_sample": testing,
    "expectancy_retention_percent": (
        expectancy_retention
    ),
    "validation_status": status,
    "limitations": [
        "Training and testing data must be chronologically separated.",
        "Testing data must not influence strategy parameter selection.",
        "Positive out-of-sample results do not establish future profitability.",
    ],
}

def generate_walk_forward_windows(
total_observations: int,
training_size: int,
testing_size: int,
step_size: int | None = None,
) -> list[dict[str, int]]:

values = {
    "total_observations": total_observations,
    "training_size": training_size,
    "testing_size": testing_size,
}

for name, value in values.items():

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise StrategyResearchError(
            f"{name} must be a positive integer."
        )

if step_size is None:
    step_size = testing_size

if (
    isinstance(step_size, bool)
    or not isinstance(step_size, int)
    or step_size <= 0
):
    raise StrategyResearchError(
        "step_size must be a positive integer."
    )

if training_size + testing_size > total_observations:
    raise StrategyResearchError(
        "Not enough observations for the first walk-forward window."
    )

windows = []

train_start = 0

while True:

    train_end = (
        train_start + training_size
    )

    test_end = (
        train_end + testing_size
    )

    if test_end > total_observations:
        break

    windows.append({
        "training_start": train_start,
        "training_end_exclusive": train_end,
        "testing_start": train_end,
        "testing_end_exclusive": test_end,
    })

    train_start += step_size

return windows

def rank_parameter_experiments(
experiments: Sequence[Mapping[str, Any]],
minimum_trades: int = 30,
) -> dict[str, Any]:

if (
    isinstance(experiments, (str, bytes))
    or not isinstance(experiments, Sequence)
):
    raise StrategyResearchError(
        "Experiments must be a sequence."
    )

if not (
    1 <= len(experiments) <= MAXIMUM_EXPERIMENTS
):
    raise StrategyResearchError(
        "Invalid number of experiments."
    )

if (
    isinstance(minimum_trades, bool)
    or not isinstance(minimum_trades, int)
    or minimum_trades < 1
):
    raise StrategyResearchError(
        "minimum_trades must be positive."
    )

ranked = []

for index, experiment in enumerate(experiments):

    if not isinstance(experiment, Mapping):
        raise StrategyResearchError(
            f"Experiment {index} must be a mapping."
        )

    parameters = experiment.get(
        "parameters",
        {},
    )

    if not isinstance(parameters, Mapping):
        raise StrategyResearchError(
            "Experiment parameters must be a mapping."
        )

    metrics = evaluate_strategy(
        experiment.get("trades", [])
    )

    eligible = (
        metrics["trade_count"] >= minimum_trades
        and metrics["expectancy_per_trade"] > 0
        and metrics["maximum_drawdown_percent"] < 50.0
        and metrics["profit_factor"] is not None
        and metrics["profit_factor"] > 1.0
    )

    ranked.append({
        "experiment_id": str(
            experiment.get(
                "experiment_id",
                f"EXPERIMENT_{index + 1}",
            )
        ),
        "parameters": dict(parameters),
        "eligible": eligible,
        "metrics": metrics,
    })

ranked.sort(
    key=lambda item: (
        item["eligible"],
        item["metrics"]["expectancy_per_trade"],
        item["metrics"]["profit_factor"] or 0.0,
    ),
    reverse=True,
)

return {
    "module": "strategy_research_lab",
    "experiments_received": len(ranked),
    "eligible_experiments": sum(
        1 for item in ranked
        if item["eligible"]
    ),
    "ranked_experiments": ranked,
    "limitations": [
        "Ranking does not replace out-of-sample validation.",
        "Repeated parameter searching can create selection bias.",
        "The eligibility thresholds are screening rules, not universal trading standards.",
    ],
}

def monte_carlo_trade_sequences(
trades: Sequence[Mapping[str, Any]],
initial_capital: float = 10000.0,
simulations: int = 1000,
seed: int | None = None,
) -> dict[str, Any]:

capital = _finite_number(
    initial_capital,
    "initial_capital",
)

if capital <= 0:
    raise StrategyResearchError(
        "Initial capital must be positive."
    )

if (
    isinstance(simulations, bool)
    or not isinstance(simulations, int)
    or not 100 <= simulations <= MAXIMUM_MONTE_CARLO_SIMULATIONS
):
    raise StrategyResearchError(
        "Simulations must be between 100 and 10000."
    )

validated = _validate_trades(
    trades
)

pnls = [
    item["net_pnl"]
    for item in validated
]

if len(pnls) < MINIMUM_TRADES_FOR_EVALUATION:
    raise StrategyResearchError(
        "Monte Carlo requires at least 10 trades."
    )

if seed is not None and (
    isinstance(seed, bool)
    or not isinstance(seed, int)
):
    raise StrategyResearchError(
        "seed must be an integer or None."
    )

generator = random.Random(seed)

final_equities = []
maximum_drawdowns = []
ruin_count = 0

for _ in range(simulations):

    shuffled = list(pnls)

    generator.shuffle(shuffled)

    curve = _equity_curve(
        shuffled,
        capital,
    )

    final_equity = curve[-1]

    drawdown = _drawdown_statistics(
        curve
    )

    final_equities.append(
        final_equity
    )

    maximum_drawdowns.append(
        drawdown["maximum_drawdown_percent"]
    )

    if final_equity <= 0:
        ruin_count += 1

final_equities.sort()
maximum_drawdowns.sort()

def percentile(
    values: Sequence[float],
    percentage: float,
) -> float:

    position = (
        len(values) - 1
    ) * percentage

    lower = int(position)
    upper = min(
        lower + 1,
        len(values) - 1,
    )

    fraction = position - lower

    return (
        values[lower] * (1.0 - fraction)
        + values[upper] * fraction
    )

return {
    "module": "strategy_research_lab",
    "simulation_count": simulations,
    "trade_count": len(pnls),
    "seed": seed,
    "final_equity": {
        "p05": percentile(final_equities, 0.05),
        "p25": percentile(final_equities, 0.25),
        "median": percentile(final_equities, 0.50),
        "p75": percentile(final_equities, 0.75),
        "p95": percentile(final_equities, 0.95),
    },
    "maximum_drawdown_percent": {
        "p50": percentile(maximum_drawdowns, 0.50),
        "p75": percentile(maximum_drawdowns, 0.75),
        "p95": percentile(maximum_drawdowns, 0.95),
    },
    "non_positive_final_equity_count": ruin_count,
    "non_positive_final_equity_rate_percent": (
        ruin_count / simulations * 100.0
    ),
    "limitations": [
        "Monte Carlo randomly reorders observed trade outcomes.",
        "It does not model changing market regimes or new market behavior.",
        "It does not prove the strategy has a positive expected return.",
        "It does not simulate intratrade drawdown or execution failures.",
        "Results depend on the historical trade sample.",
    ],
}
