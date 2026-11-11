"""
GOLD BOT - Technical Indicator Analyzer

Calculates:

- RSI 14
- MACD 12/26/9
- Bollinger Bands 20/2
- ATR 14
- Stochastic 14/3/3

Input:
Chronological OHLC candles, oldest to newest.
Only closed candles should be supplied.

This module performs analysis only.
It never generates or executes orders.
"""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

class IndicatorAnalysisError(ValueError):
"""Raised when market data is invalid."""

MINIMUM_CANDLES = 50

def _price(candle: Mapping[str, Any], field: str) -> float:
value = candle.get(field)

if isinstance(value, bool):
    raise IndicatorAnalysisError(f"Invalid {field} value.")

try:
    result = float(value)
except (TypeError, ValueError):
    raise IndicatorAnalysisError(
        f"Missing or invalid candle field: {field}"
    )

if not isfinite(result) or result <= 0:
    raise IndicatorAnalysisError(
        f"{field} must be finite and positive."
    )

return result

def _validate(
candles: Sequence[Mapping[str, Any]],
) -> list[dict[str, float]]:

if isinstance(candles, (str, bytes)):
    raise IndicatorAnalysisError("Invalid candle collection.")

if len(candles) < MINIMUM_CANDLES:
    raise IndicatorAnalysisError(
        f"At least {MINIMUM_CANDLES} candles are required."
    )

result = []

for index, candle in enumerate(candles):
    if not isinstance(candle, Mapping):
        raise IndicatorAnalysisError(
            f"Invalid candle at index {index}."
        )

    open_price = _price(candle, "open")
    high = _price(candle, "high")
    low = _price(candle, "low")
    close = _price(candle, "close")

    if high < low:
        raise IndicatorAnalysisError("High cannot be below low.")

    if high < max(open_price, close):
        raise IndicatorAnalysisError("Invalid candle high.")

    if low > min(open_price, close):
        raise IndicatorAnalysisError("Invalid candle low.")

    result.append({
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
    })

return result

def _ema(values: Sequence[float], period: int) -> list[float]:
if len(values) < period:
raise IndicatorAnalysisError("Insufficient EMA data.")

multiplier = 2.0 / (period + 1)

current = sum(values[:period]) / period
output = [current]

for value in values[period:]:
    current = (
        value * multiplier
        + current * (1.0 - multiplier)
    )
    output.append(current)

return output

def _rsi(values: Sequence[float], period: int = 14) -> float:
changes = [
values[index] - values[index - 1]
for index in range(1, len(values))
]

gains = [max(change, 0.0) for change in changes]
losses = [max(-change, 0.0) for change in changes]

if len(gains) < period:
    raise IndicatorAnalysisError("Insufficient RSI data.")

average_gain = sum(gains[:period]) / period
average_loss = sum(losses[:period]) / period

for index in range(period, len(gains)):
    average_gain = (
        average_gain * (period - 1) + gains[index]
    ) / period

    average_loss = (
        average_loss * (period - 1) + losses[index]
    ) / period

if average_loss == 0:
    return 100.0 if average_gain > 0 else 50.0

relative_strength = average_gain / average_loss

return 100.0 - (100.0 / (1.0 + relative_strength))

def _atr(
candles: Sequence[dict[str, float]],
period: int = 14,
) -> float:

true_ranges = []

for index, candle in enumerate(candles):
    if index == 0:
        true_range = candle["high"] - candle["low"]
    else:
        previous_close = candles[index - 1]["close"]

        true_range = max(
            candle["high"] - candle["low"],
            abs(candle["high"] - previous_close),
            abs(candle["low"] - previous_close),
        )

    true_ranges.append(true_range)

if len(true_ranges) < period:
    raise IndicatorAnalysisError("Insufficient ATR data.")

value = sum(true_ranges[:period]) / period

for current in true_ranges[period:]:
    value = (
        value * (period - 1) + current
    ) / period

return value

def _stochastic(
candles: Sequence[dict[str, float]],
period: int = 14,
) -> tuple[float, float]:

highs = [candle["high"] for candle in candles]
lows = [candle["low"] for candle in candles]
closes = [candle["close"] for candle in candles]

k_values = []

for index in range(period - 1, len(candles)):
    highest = max(highs[index - period + 1:index + 1])
    lowest = min(lows[index - period + 1:index + 1])

    if highest == lowest:
        k_values.append(50.0)
    else:
        k_values.append(
            100.0
            * (closes[index] - lowest)
            / (highest - lowest)
        )

current_k = k_values[-1]
current_d = sum(k_values[-3:]) / 3

return current_k, current_d

def analyze_indicators(
candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:

data = _validate(candles)

closes = [candle["close"] for candle in data]

# RSI
rsi = _rsi(closes, 14)

# MACD
ema12 = _ema(closes, 12)
ema26 = _ema(closes, 26)

macd_line = [
    fast - slow
    for fast, slow in zip(ema12[-len(ema26):], ema26)
]

signal_line = _ema(macd_line, 9)

macd_current = macd_line[-1]
signal_current = signal_line[-1]
histogram = macd_current - signal_current

# Bollinger Bands: population standard deviation.
period_closes = closes[-20:]
middle = sum(period_closes) / 20

variance = sum(
    (value - middle) ** 2
    for value in period_closes
) / 20

deviation = variance ** 0.5

upper_band = middle + 2.0 * deviation
lower_band = middle - 2.0 * deviation

# ATR
atr = _atr(data, 14)

# Stochastic
stochastic_k, stochastic_d = _stochastic(data, 14)

# Interpretations are observations, not independent trade signals.
observations = []

if rsi >= 70:
    observations.append("RSI_OVERBOUGHT_ZONE")
elif rsi <= 30:
    observations.append("RSI_OVERSOLD_ZONE")
else:
    observations.append("RSI_NEUTRAL_ZONE")

if macd_current > signal_current:
    observations.append("MACD_BULLISH")
elif macd_current < signal_current:
    observations.append("MACD_BEARISH")
else:
    observations.append("MACD_NEUTRAL")

if closes[-1] > upper_band:
    observations.append("PRICE_ABOVE_UPPER_BOLLINGER_BAND")
elif closes[-1] < lower_band:
    observations.append("PRICE_BELOW_LOWER_BOLLINGER_BAND")
else:
    observations.append("PRICE_INSIDE_BOLLINGER_BANDS")

if stochastic_k >= 80:
    observations.append("STOCHASTIC_OVERBOUGHT_ZONE")
elif stochastic_k <= 20:
    observations.append("STOCHASTIC_OVERSOLD_ZONE")
else:
    observations.append("STOCHASTIC_NEUTRAL_ZONE")

return {
    "module": "indicators",
    "data_available": True,
    "candles_analyzed": len(data),
    "indicators": {
        "rsi_14": rsi,
        "macd": macd_current,
        "macd_signal": signal_current,
        "macd_histogram": histogram,
        "bollinger_upper": upper_band,
        "bollinger_middle": middle,
        "bollinger_lower": lower_band,
        "atr_14": atr,
        "stochastic_k": stochastic_k,
        "stochastic_d": stochastic_d,
    },
    "observations": observations,
    "limitations": [
        "Indicator thresholds do not guarantee reversals.",
        "ATR measures volatility, not price direction.",
        "Indicators can lag price movements.",
        "This module does not generate trading orders.",
    ],
}
