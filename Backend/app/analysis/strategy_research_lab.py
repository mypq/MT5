"""
GOLD BOT - Strategy Research Lab

Quantitative strategy evaluation and research utilities.

Capabilities:
- Net trade performance statistics
- Equity curve and drawdown analysis
- Profit factor and expectancy
- Win/loss distribution
- Per-trade risk-adjusted metrics
- Chronological out-of-sample validation
- Rolling walk-forward windows
- Parameter experiment screening
- Bootstrap Monte Carlo simulation
- Reproducible random simulations

IMPORTANT:
- This module does not execute trades.
- Net PnL must include commissions, swaps and slippage.
- Historical performance does not guarantee future results.
- Research eligibility is not trading authorization.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite, sqrt
from statistics import mean, median
from typing import Any
import random


MAXIMUM_TRADES = 100000
MAXIMUM_EXPERIMENTS = 10000
MAXIMUM_MONTE_CARLO_SIMULATIONS = 10000
MAXIMUM_MONTE_CARLO_OPERATIONS = 10_000_000
MAXIMUM_WALK_FORWARD_WINDOWS = 100000

MINIMUM_TRADES_FOR_EVALUATION = 10
MINIMUM_TRADES_FOR_MONTE_CARLO = 10

DEFAULT_INITIAL_CAPITAL = 10000.0

MAXIMUM_DRAWDOWN_ELIGIBILITY_PERCENT = 50.0
MINIMUM_PROFIT_FACTOR_FOR_ELIGIBILITY = 1.0

SUPPORTED_SYMBOL = "XAUUSD"


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
    except (TypeError, ValueError, OverflowError) as exc:
        raise StrategyResearchError(
            f"{field} must be numeric."
        ) from exc

    if not isfinite(result):
        raise StrategyResearchError(
            f"{field} must be finite."
        )

    return result


def _parse_timestamp(
    value: Any,
    field: str,
) -> datetime:
    if isinstance(value, datetime):
        parsed = value

    elif isinstance(value, str):
        normalized = (
            value[:-1] + "+00:00"
            if value.endswith("Z")
            else value
        )

        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise StrategyResearchError(
                f"{field} is not a valid ISO-8601 timestamp."
            ) from exc

    else:
        raise StrategyResearchError(
            f"{field} must be a datetime or ISO-8601 string."
        )

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StrategyResearchError(
            f"{field} must include a timezone."
        )

    return parsed.astimezone(timezone.utc)


def _validate_trades(
    trades: Sequence[Mapping[str, Any]],
    require_timestamps: bool = False,
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
    previous_timestamp = None

    for index, trade in enumerate(trades):
        if not isinstance(trade, Mapping):
            raise StrategyResearchError(
                f"Trade {index} must be a mapping."
            )

        pnl = _finite_number(
            trade.get("net_pnl"),
            f"trades[{index}].net_pnl",
        )

        item = {
            "net_pnl": pnl,
            "strategy": str(
                trade.get("strategy", "UNSPECIFIED")
            ),
        }

        timestamp_value = trade.get("timestamp")

        if timestamp_value is None:
            if require_timestamps:
                raise StrategyResearchError(
                    f"Trade {index} requires a timestamp."
                )

        else:
            timestamp = _parse_timestamp(
                timestamp_value,
                f"trades[{index}].timestamp",
            )

            if (
                previous_timestamp is not None
                and timestamp < previous_timestamp
            ):
                raise StrategyResearchError(
                    "Trade timestamps must be chronological."
                )

            previous_timestamp = timestamp
            item["timestamp"] = timestamp

        validated.append(item)

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
) -> dict[str, Any]:
    if not equity_curve:
        raise StrategyResearchError(
            "Equity curve cannot be empty."
        )

    peak = equity_curve[0]

    maximum_drawdown = 0.0
    maximum_drawdown_percent = 0.0
    maximum_drawdown_percent_available = True

    current_drawdown = 0.0
    longest_drawdown_period = 0
    current_drawdown_period = 0

    for equity in equity_curve:
        if equity > peak:
            peak = equity
            current_drawdown_period = 0

        drawdown = max(
            0.0,
            peak - equity,
        )

        maximum_drawdown = max(
            maximum_drawdown,
            drawdown,
        )

        if drawdown > 0:
            current_drawdown_period += 1
            longest_drawdown_period = max(
                longest_drawdown_period,
                current_drawdown_period,
            )
        else:
            current_drawdown_period = 0

        if peak > 0:
            drawdown_percent = (
                drawdown / peak * 100.0
            )

            maximum_drawdown_percent = max(
                maximum_drawdown_percent,
                drawdown_percent,
            )

        elif drawdown > 0:
            maximum_drawdown_percent_available = False

    return {
        "maximum_drawdown": maximum_drawdown,
        "maximum_drawdown_percent": (
            maximum_drawdown_percent
            if maximum_drawdown_percent_available
            else None
        ),
        "maximum_drawdown_percent_status": (
            "AVAILABLE"
            if maximum_drawdown_percent_available
            else "UNAVAILABLE_NON_POSITIVE_EQUITY_PEAK"
        ),
        "longest_drawdown_period_trades": (
            longest_drawdown_period
        ),
    }


def _return_dispersion(
    values: Sequence[float],
) -> dict[str, Any]:
    if len(values) < 2:
        return {
            "standard_deviation": None,
            "downside_deviation": None,
            "sharpe_like_per_trade": None,
            "sortino_like_per_trade": None,
        }

    average = mean(values)

    variance = sum(
        (value - average) ** 2
        for value in values
    ) / (len(values) - 1)

    standard_deviation = sqrt(
        max(variance, 0.0)
    )

    downside_values = [
        min(value, 0.0)
        for value in values
    ]

    downside_variance = sum(
        value ** 2
        for value in downside_values
    ) / len(values)

    downside_deviation = sqrt(
        max(downside_variance, 0.0)
    )

    sharpe_like = (
        average / standard_deviation
        if standard_deviation > 0
        else None
    )

    sortino_like = (
        average / downside_deviation
        if downside_deviation > 0
        else None
    )

    return {
        "standard_deviation": standard_deviation,
        "downside_deviation": downside_deviation,
        "sharpe_like_per_trade": sharpe_like,
        "sortino_like_per_trade": sortino_like,
    }


def evaluate_strategy(
    trades: Sequence[Mapping[str, Any]],
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
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

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
        profit_factor_status = "AVAILABLE"

    elif gross_profit > 0:
        profit_factor = None
        profit_factor_status = "NO_LOSING_TRADES"

    else:
        profit_factor = None
        profit_factor_status = "NO_PROFIT_OR_LOSS"

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

    average_trade_percent = mean(
        [
            pnl / capital * 100.0
            for pnl in pnls
        ]
    )

    dispersion = _return_dispersion(pnls)

    timestamps_available = all(
        "timestamp" in trade
        for trade in validated
    )

    return {
        "module": "strategy_research_lab",
        "trade_count": len(pnls),
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "breakeven_trades": len(breakeven),
        "initial_capital": capital,
        "final_capital": final_capital,
        "account_ruined": final_capital <= 0,
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
        "profit_factor_status": profit_factor_status,
        "maximum_drawdown": (
            drawdown["maximum_drawdown"]
        ),
        "maximum_drawdown_percent": (
            drawdown["maximum_drawdown_percent"]
        ),
        "maximum_drawdown_percent_status": (
            drawdown["maximum_drawdown_percent_status"]
        ),
        "longest_drawdown_period_trades": (
            drawdown["longest_drawdown_period_trades"]
        ),
        "risk_metrics": dispersion,
        "equity_curve": equity_curve,
        "median_trade_pnl": median(pnls),
        "timestamps_available": timestamps_available,
        "research_status": (
            "INSUFFICIENT_SAMPLE"
            if len(pnls) < MINIMUM_TRADES_FOR_EVALUATION
            else "EVALUATION_AVAILABLE"
        ),
        "limitations": [
            "Net PnL must already include commissions, swaps and slippage.",
            "Drawdown uses trade-by-trade equity, not intratrade prices.",
            "Trade ordering must reflect actual chronological execution.",
            "Per-trade Sharpe-like and Sortino-like values are not annualized.",
            "A risk-free rate and consistent time intervals are not modeled.",
            "Profit factor is unavailable when there are no losing trades.",
            "Historical performance does not guarantee future results.",
        ],
    }


def compare_out_of_sample(
    training_trades: Sequence[Mapping[str, Any]],
    testing_trades: Sequence[Mapping[str, Any]],
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
) -> dict[str, Any]:
    training_records = _validate_trades(
        training_trades,
        require_timestamps=True,
    )

    testing_records = _validate_trades(
        testing_trades,
        require_timestamps=True,
    )

    training_last = training_records[-1]["timestamp"]
    testing_first = testing_records[0]["timestamp"]

    if testing_first <= training_last:
        raise StrategyResearchError(
            "Out-of-sample trades must start strictly after "
            "the final training trade."
        )

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

    expectancy_retention = (
        testing_expectancy / training_expectancy * 100.0
        if training_expectancy > 0
        else None
    )

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
        "chronology_verified": True,
        "training_last_timestamp": training_last.isoformat(),
        "testing_first_timestamp": testing_first.isoformat(),
        "expectancy_retention_percent": (
            expectancy_retention
        ),
        "validation_status": status,
        "trading_signal_allowed": False,
        "limitations": [
            "Chronological separation is verified using trade timestamps.",
            "The caller must still prevent testing data from influencing parameter selection.",
            "A single out-of-sample period does not establish robustness.",
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
        train_end = train_start + training_size
        test_end = train_end + testing_size

        if test_end > total_observations:
            break

        windows.append({
            "training_start": train_start,
            "training_end_exclusive": train_end,
            "testing_start": train_end,
            "testing_end_exclusive": test_end,
        })

        if len(windows) >= MAXIMUM_WALK_FORWARD_WINDOWS:
            raise StrategyResearchError(
                "Walk-forward window limit exceeded."
            )

        train_start += step_size

    return windows


def rank_parameter_experiments(
    experiments: Sequence[Mapping[str, Any]],
    minimum_trades: int = 30,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
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

        training_metrics = evaluate_strategy(
            experiment.get("trades", []),
            initial_capital,
        )

        out_of_sample_records = experiment.get(
            "out_of_sample_trades"
        )

        out_of_sample_metrics = None
        chronology_verified = False

        if out_of_sample_records is not None:
            comparison = compare_out_of_sample(
                experiment["trades"],
                out_of_sample_records,
                initial_capital,
            )

            out_of_sample_metrics = comparison[
                "out_of_sample"
            ]

            chronology_verified = comparison[
                "chronology_verified"
            ]

        training_eligible = (
            training_metrics["trade_count"] >= minimum_trades
            and training_metrics["expectancy_per_trade"] > 0
            and training_metrics["maximum_drawdown_percent"] is not None
            and training_metrics["maximum_drawdown_percent"]
            < MAXIMUM_DRAWDOWN_ELIGIBILITY_PERCENT
            and training_metrics["profit_factor"] is not None
            and training_metrics["profit_factor"]
            > MINIMUM_PROFIT_FACTOR_FOR_ELIGIBILITY
        )

        out_of_sample_eligible = (
            out_of_sample_metrics is not None
            and chronology_verified
            and out_of_sample_metrics["trade_count"] >= minimum_trades
            and out_of_sample_metrics["expectancy_per_trade"] > 0
            and out_of_sample_metrics["maximum_drawdown_percent"] is not None
            and out_of_sample_metrics["maximum_drawdown_percent"]
            < MAXIMUM_DRAWDOWN_ELIGIBILITY_PERCENT
            and out_of_sample_metrics["profit_factor"] is not None
            and out_of_sample_metrics["profit_factor"]
            > MINIMUM_PROFIT_FACTOR_FOR_ELIGIBILITY
        )

        eligible = (
            training_eligible
            and out_of_sample_eligible
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
            "training_eligible": training_eligible,
            "out_of_sample_eligible": out_of_sample_eligible,
            "chronology_verified": chronology_verified,
            "training_metrics": training_metrics,
            "out_of_sample_metrics": out_of_sample_metrics,
            "ranking_expectancy": (
                out_of_sample_metrics["expectancy_per_trade"]
                if out_of_sample_metrics is not None
                else training_metrics["expectancy_per_trade"]
            ),
            "ranking_basis": (
                "OUT_OF_SAMPLE"
                if out_of_sample_metrics is not None
                else "TRAINING_ONLY_NOT_ELIGIBLE"
            ),
        })

    ranked.sort(
        key=lambda item: (
            item["eligible"],
            item["ranking_expectancy"],
            item["training_metrics"]["expectancy_per_trade"],
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
        "trading_signal_allowed": False,
        "limitations": [
            "Training-only experiments cannot become eligible.",
            "Out-of-sample trades must be chronologically separated.",
            "Repeated parameter searches can create selection bias.",
            "A separate final holdout period is recommended.",
            "Eligibility thresholds are screening rules, not universal standards.",
            "Experiment ranking is not trading authorization.",
        ],
    }


def _percentile(
    values: Sequence[float],
    percentage: float,
) -> float:
    if not values:
        raise StrategyResearchError(
            "Cannot calculate percentile from empty values."
        )

    if not 0.0 <= percentage <= 1.0:
        raise StrategyResearchError(
            "Percentile must be between zero and one."
        )

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


def _simulate_bootstrap_path(
    pnls: Sequence[float],
    initial_capital: float,
    generator: random.Random,
) -> dict[str, Any]:
    equity = initial_capital
    peak = initial_capital

    maximum_drawdown = 0.0
    maximum_drawdown_percent = 0.0
    ruined = False
    ruin_trade_index = None

    for trade_index in range(len(pnls)):
        sampled_pnl = generator.choice(pnls)

        equity += sampled_pnl

        if equity > peak:
            peak = equity

        drawdown = max(
            0.0,
            peak - equity,
        )

        maximum_drawdown = max(
            maximum_drawdown,
            drawdown,
        )

        if peak > 0:
            maximum_drawdown_percent = max(
                maximum_drawdown_percent,
                drawdown / peak * 100.0,
            )

        if equity <= 0:
            ruined = True

            if ruin_trade_index is None:
                ruin_trade_index = trade_index + 1

    return {
        "final_equity": equity,
        "maximum_drawdown": maximum_drawdown,
        "maximum_drawdown_percent": maximum_drawdown_percent,
        "ruined": ruined,
        "ruin_trade_index": ruin_trade_index,
    }


def monte_carlo_trade_sequences(
    trades: Sequence[Mapping[str, Any]],
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
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

    validated = _validate_trades(trades)

    pnls = [
        item["net_pnl"]
        for item in validated
    ]

    if len(pnls) < MINIMUM_TRADES_FOR_MONTE_CARLO:
        raise StrategyResearchError(
            "Monte Carlo requires at least 10 trades."
        )

    if (
        simulations * len(pnls)
        > MAXIMUM_MONTE_CARLO_OPERATIONS
    ):
        raise StrategyResearchError(
            "Monte Carlo operation limit exceeded."
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
    maximum_drawdowns_absolute = []

    ruin_count = 0
    ruin_trade_indices = []

    for _ in range(simulations):
        result = _simulate_bootstrap_path(
            pnls,
            capital,
            generator,
        )

        final_equities.append(
            result["final_equity"]
        )

        maximum_drawdowns.append(
            result["maximum_drawdown_percent"]
        )

        maximum_drawdowns_absolute.append(
            result["maximum_drawdown"]
        )

        if result["ruined"]:
            ruin_count += 1

            if result["ruin_trade_index"] is not None:
                ruin_trade_indices.append(
                    result["ruin_trade_index"]
                )

    final_equities.sort()
    maximum_drawdowns.sort()
    maximum_drawdowns_absolute.sort()

    return {
        "module": "strategy_research_lab",
        "simulation_method": "BOOTSTRAP_WITH_REPLACEMENT",
        "simulation_count": simulations,
        "trade_count": len(pnls),
        "seed": seed,
        "final_equity": {
            "p05": _percentile(final_equities, 0.05),
            "p25": _percentile(final_equities, 0.25),
            "median": _percentile(final_equities, 0.50),
            "p75": _percentile(final_equities, 0.75),
            "p95": _percentile(final_equities, 0.95),
        },
        "maximum_drawdown_percent": {
            "p50": _percentile(maximum_drawdowns, 0.50),
            "p75": _percentile(maximum_drawdowns, 0.75),
            "p95": _percentile(maximum_drawdowns, 0.95),
        },
        "maximum_drawdown_absolute": {
            "p50": _percentile(
                maximum_drawdowns_absolute,
                0.50,
            ),
            "p95": _percentile(
                maximum_drawdowns_absolute,
                0.95,
            ),
        },
        "non_positive_equity_count": ruin_count,
        "non_positive_equity_rate_percent": (
            ruin_count / simulations * 100.0
        ),
        "median_ruin_trade_index": (
            median(ruin_trade_indices)
            if ruin_trade_indices
            else None
        ),
        "trading_signal_allowed": False,
        "limitations": [
            "Bootstrap samples observed trade outcomes with replacement.",
            "The simulation assumes historical trade outcomes are representative.",
            "It does not model changing market regimes.",
            "It does not simulate intratrade drawdown or margin liquidation.",
            "It does not model execution failures or changing transaction costs.",
            "It does not prove positive expected future returns.",
            "Results depend on the quality and size of the historical trade sample.",
        ],
}
