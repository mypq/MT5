
import math

import pytest

from app.analysis.indicator_analyzer import (
    IndicatorAnalysisError,
    MINIMUM_CANDLES,
    analyze_indicators,
)


def make_candles(closes):
    return [
        {
            "open": float(close),
            "high": float(close) + 1.0,
            "low": float(close) - 1.0,
            "close": float(close),
        }
        for close in closes
    ]


def indicator_values(candles):
    return analyze_indicators(candles)["indicators"]


def test_flat_market_returns_neutral_values():
    values = indicator_values(make_candles([100.0] * 60))

    assert values["rsi_14"] == pytest.approx(50.0)
    assert values["macd"] == pytest.approx(0.0)
    assert values["macd_signal"] == pytest.approx(0.0)
    assert values["macd_histogram"] == pytest.approx(0.0)
    assert values["bollinger_middle"] == pytest.approx(100.0)
    assert values["bollinger_upper"] == pytest.approx(100.0)
    assert values["bollinger_lower"] == pytest.approx(100.0)
    assert values["atr_14"] == pytest.approx(2.0)
    assert values["stochastic_fast_k"] == pytest.approx(50.0)
    assert values["stochastic_k"] == pytest.approx(50.0)
    assert values["stochastic_d"] == pytest.approx(50.0)


def test_rising_market():
    values = indicator_values(
        make_candles([100.0 + i for i in range(60)])
    )

    assert values["rsi_14"] == pytest.approx(100.0)
    assert values["macd"] > 0.0
    assert values["atr_14"] > 0.0


def test_falling_market():
    values = indicator_values(
        make_candles([160.0 - i for i in range(60)])
    )

    assert values["rsi_14"] == pytest.approx(0.0)
    assert values["macd"] < 0.0
    assert values["atr_14"] > 0.0


def test_stochastic_smoothing_and_bounds():
    closes = [
        100.0 + math.sin(i * 0.7) * 5.0
        for i in range(80)
    ]
    values = indicator_values(make_candles(closes))

    fast_k = values["stochastic_fast_k"]
    slow_k = values["stochastic_k"]
    slow_d = values["stochastic_d"]

    assert 0.0 <= fast_k <= 100.0
    assert 0.0 <= slow_k <= 100.0
    assert 0.0 <= slow_d <= 100.0
    assert slow_k != pytest.approx(fast_k)
    assert slow_d != pytest.approx(fast_k)


def test_all_numeric_results_are_finite():
    closes = [
        100.0 + math.sin(i / 3.0)
        for i in range(80)
    ]
    values = indicator_values(make_candles(closes))

    assert all(
        isinstance(value, (int, float))
        and math.isfinite(value)
        for value in values.values()
    )


def test_rejects_too_few_candles():
    candles = make_candles(
        [100.0] * (MINIMUM_CANDLES - 1)
    )

    with pytest.raises(IndicatorAnalysisError):
        analyze_indicators(candles)


@pytest.mark.parametrize(
    "field,value",
    [
        ("open", float("nan")),
        ("high", float("inf")),
        ("low", 0.0),
        ("close", -1.0),
    ],
)
def test_rejects_invalid_prices(field, value):
    candles = make_candles([100.0] * 60)
    candles[10][field] = value

    with pytest.raises(IndicatorAnalysisError):
        analyze_indicators(candles)


def test_rejects_inconsistent_ohlc():
    candles = make_candles([100.0] * 60)
    candles[10]["high"] = 99.0

    with pytest.raises(
        IndicatorAnalysisError,
        match="Invalid candle high",
    ):
        analyze_indicators(candles)


def test_rejects_non_mapping_candle():
    candles = make_candles([100.0] * 60)
    candles[10] = None

    with pytest.raises(IndicatorAnalysisError):
        analyze_indicators(candles)


def test_rejects_string_instead_of_candle_sequence():
    with pytest.raises(IndicatorAnalysisError):
        analyze_indicators("not candles")


def test_indicators_match_reference_values():
    closes = [
        100.0 + math.sin(i / 3.0)
        for i in range(80)
    ]

    candles = []

    for i, close in enumerate(closes):
        open_price = close + 0.05 * math.cos(i / 2.0)

        candles.append({
            "open": open_price,
            "high": max(open_price, close) + 0.35 + (i % 3) * 0.07,
            "low": min(open_price, close) - 0.28 - (i % 4) * 0.06,
            "close": close,
        })

    values = indicator_values(candles)

    expected = {
        "rsi_14": 66.05992928860223,
        "macd": 0.10917737603814714,
        "macd_signal": -0.04418999002931408,
        "macd_histogram": 0.1533673660674612,
        "bollinger_upper": 101.4914789109283,
        "bollinger_middle": 100.05300701160692,
        "bollinger_lower": 98.61453511228555,
        "atr_14": 0.8271622798193119,
        "stochastic_fast_k": 85.19126696340554,
        "stochastic_k": 83.06840980724819,
        "stochastic_d": 73.34552999782962,
    }

    for name, reference in expected.items():
        assert values[name] == pytest.approx(
            reference,
            rel=1e-8,
            abs=1e-8,
        )
