"""
GOLD BOT - Multi-Timeframe Trend Analyzer

Analyzes chronological OHLC candles using EMA 20, EMA 50 and EMA 200.
Input candles must be ordered from oldest to newest.
This module does not generate trading orders.
"""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any


MINIMUM_CANDLES = 205

SUPPORTED_TIMEFRAMES = {
    "M5",
    "M15",
    "M30",
    "H1",
    "H4",
    "D1",
    "W1",
}


class TrendAnalysisError(ValueError):
    """Raised when market candle data is invalid."""


def _read_price(candle: Mapping[str, Any], field: str) -> float:
    value = candle.get(field)

    if isinstance(value, bool):
        raise TrendAnalysisError(
            f"Invalid {field} price: boolean values are not accepted."
        )

    try:
        price = float(value)
    except (TypeError, ValueError):
        raise TrendAnalysisError(
            f"Invalid or missing candle field: {field}"
        ) from None

    if not isfinite(price) or price <= 0:
        raise TrendAnalysisError(
            f"Invalid {field} price: value must be finite and positive."
        )

    return price


def _validate_candles(
    candles: Sequence[Mapping[str, Any]],
) -> list[dict[str, float]]:
    if (
        not isinstance(candles, Sequence)
        or isinstance(candles, (str, bytes))
    ):
        raise TrendAnalysisError(
            "Candles must be a sequence of objects."
        )

    if len(candles) < MINIMUM_CANDLES:
        raise TrendAnalysisError(
            f"Insufficient candles: at least {MINIMUM_CANDLES} required."
        )

    validated = []

    for index, candle in enumerate(candles):
        if not isinstance(candle, Mapping):
            raise TrendAnalysisError(
                f"Candle at index {index} is not a valid object."
            )

        open_price = _read_price(candle, "open")
        high = _read_price(candle, "high")
        low = _read_price(candle, "low")
        close = _read_price(candle, "close")

        if high < low:
            raise TrendAnalysisError(
                f"Invalid candle at index {index}: high is below low."
            )

        if high < max(open_price, close):
            raise TrendAnalysisError(
                f"Invalid candle at index {index}: high is inconsistent."
            )

        if low > min(open_price, close):
            raise TrendAnalysisError(
                f"Invalid candle at index {index}: low is inconsistent."
            )

        validated.append({
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
        })

    return validated


def _calculate_ema(
    prices: Sequence[float],
    period: int,
) -> list[float]:
    if period <= 0:
        raise TrendAnalysisError("EMA period must be positive.")

    if len(prices) < period:
        raise TrendAnalysisError(
            f"Not enough prices for EMA {period}."
        )

    multiplier = 2.0 / (period + 1)

    initial_ema = sum(prices[:period]) / period
    values = [initial_ema]

    for price in prices[period:]:
        next_ema = (
            price * multiplier
            + values[-1] * (1.0 - multiplier)
        )
        values.append(next_ema)

    return values


def analyze_trend(
    candles: Sequence[Mapping[str, Any]],
    timeframe: str,
) -> dict[str, Any]:
    """
    Analyze one timeframe.

    strength_score is a directional alignment score,
    not a probability of profit.
    """

    if not isinstance(timeframe, str):
        raise TrendAnalysisError("Timeframe must be a string.")

    timeframe = timeframe.upper().strip()

    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise TrendAnalysisError(
            f"Unsupported timeframe: {timeframe}"
        )

    validated = _validate_candles(candles)

    closes = [candle["close"] for candle in validated]

    ema20_series = _calculate_ema(closes, 20)
    ema50_series = _calculate_ema(closes, 50)
    ema200_series = _calculate_ema(closes, 200)

    ema20 = ema20_series[-1]
    ema50 = ema50_series[-1]
    ema200 = ema200_series[-1]

    close = closes[-1]

    ema50_slope = (
        ema50_series[-1] - ema50_series[-6]
    )

    bullish_conditions = [
        close > ema20,
        ema20 > ema50,
        ema50 > ema200,
        ema50_slope > 0,
    ]

    bearish_conditions = [
        close < ema20,
        ema20 < ema50,
        ema50 < ema200,
        ema50_slope < 0,
    ]

    weights = [20, 25, 30, 25]

    bullish_score = sum(
        weight
        for condition, weight in zip(
            bullish_conditions,
            weights,
        )
        if condition
    )

    bearish_score = sum(
        weight
        for condition, weight in zip(
            bearish_conditions,
            weights,
        )
        if condition
    )

    if bullish_score >= 90 and bullish_score > bearish_score:
        direction = "BULLISH"
        strength_score = bullish_score

    elif bearish_score >= 90 and bearish_score > bullish_score:
        direction = "BEARISH"
        strength_score = bearish_score

    else:
        direction = "NEUTRAL"
        strength_score = max(
            bullish_score,
            bearish_score,
        )

    evidence = [
        {
            "condition": "CLOSE_ABOVE_EMA20",
            "result": close > ema20,
        },
        {
            "condition": "EMA20_ABOVE_EMA50",
            "result": ema20 > ema50,
        },
        {
            "condition": "EMA50_ABOVE_EMA200",
            "result": ema50 > ema200,
        },
        {
            "condition": "EMA50_SLOPE_POSITIVE",
            "result": ema50_slope > 0,
        },
    ]

    return {
        "module": "trend",
        "timeframe": timeframe,
        "data_available": True,
        "candles_analyzed": len(validated),
        "direction": direction,
        "strength_score": strength_score,
        "score_description": (
            "Directional technical alignment score, "
            "not a probability of profit."
        ),
        "indicators": {
            "close": close,
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "ema50_slope": ema50_slope,
        },
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
        "evidence": evidence,
    }
