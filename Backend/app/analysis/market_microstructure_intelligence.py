"""
GOLD BOT - Market Microstructure Intelligence

Analyzes genuine broker-provided order book snapshots.

Features:
- Bid/ask spread analysis
- Order book depth
- Bid/ask volume imbalance
- Price-level liquidity distribution
- Abnormal spread detection
- Liquidity deterioration
- Market quality assessment

IMPORTANT:
- Requires genuine broker market data.
- Displayed liquidity is not guaranteed executable liquidity.
- This module does not execute trades.
"""

from collections.abc import Mapping, Sequence
from math import isfinite
from statistics import median
from typing import Any


class MicrostructureError(ValueError):
    """Raised when market microstructure data is invalid."""


def _number(
    value: Any,
    field: str,
    allow_zero: bool = False,
) -> float:
    if isinstance(value, bool):
        raise MicrostructureError(
            f"{field} must be numeric."
        )

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MicrostructureError(
            f"{field} must be numeric."
        ) from None

    if not isfinite(number):
        raise MicrostructureError(
            f"{field} must be finite."
        )

    if allow_zero:
        if number < 0:
            raise MicrostructureError(
                f"{field} cannot be negative."
            )
    elif number <= 0:
        raise MicrostructureError(
            f"{field} must be positive."
        )

    return number


def _validate_book(
    book: Mapping[str, Any],
) -> dict[str, list[dict[str, float]]]:
    if not isinstance(book, Mapping):
        raise MicrostructureError(
            "Order book must be a mapping."
        )

    bids = book.get("bids")
    asks = book.get("asks")

    for name, levels in (
        ("bids", bids),
        ("asks", asks),
    ):
        if (
            isinstance(levels, (str, bytes))
            or not isinstance(levels, Sequence)
            or not levels
        ):
            raise MicrostructureError(
                f"{name} must contain at least one level."
            )

        if len(levels) > 10000:
            raise MicrostructureError(
                f"Too many {name} levels."
            )

    validated = {}

    for side_name, levels in (
        ("bids", bids),
        ("asks", asks),
    ):
        output = []
        seen_prices = set()

        for index, level in enumerate(levels):
            if not isinstance(level, Mapping):
                raise MicrostructureError(
                    f"{side_name}[{index}] must be a mapping."
                )

            price = _number(
                level.get("price"),
                f"{side_name}[{index}].price",
            )

            volume = _number(
                level.get("volume"),
                f"{side_name}[{index}].volume",
                allow_zero=True,
            )

            if price in seen_prices:
                raise MicrostructureError(
                    f"Duplicate price in {side_name}."
                )

            seen_prices.add(price)

            if volume == 0:
                continue

            output.append({
                "price": price,
                "volume": volume,
            })

        if not output:
            raise MicrostructureError(
                f"{side_name} has no positive-volume levels."
            )

        validated[side_name] = output

    validated["bids"].sort(
        key=lambda item: item["price"],
        reverse=True,
    )

    validated["asks"].sort(
        key=lambda item: item["price"],
    )

    best_bid = validated["bids"][0]["price"]
    best_ask = validated["asks"][0]["price"]

    if best_bid >= best_ask:
        raise MicrostructureError(
            "Crossed or locked order book is not accepted."
        )

    return validated


def analyze_spread(
    best_bid: float,
    best_ask: float,
    reference_price: float | None = None,
) -> dict[str, Any]:
    bid = _number(best_bid, "best_bid")
    ask = _number(best_ask, "best_ask")

    if ask <= bid:
        raise MicrostructureError(
            "Ask must be greater than bid."
        )

    spread = ask - bid
    midpoint = (ask + bid) / 2.0

    result = {
        "best_bid": bid,
        "best_ask": ask,
        "spread": spread,
        "midpoint": midpoint,
        "spread_basis_points": (
            spread / midpoint * 10000.0
        ),
        "spread_percent": (
            spread / midpoint * 100.0
        ),
    }

    if reference_price is not None:
        reference = _number(
            reference_price,
            "reference_price",
        )

        result["spread_reference_ratio"] = (
            spread / reference
        )

    return result


def analyze_book_depth(
    book: Mapping[str, Any],
    levels_to_analyze: int = 5,
) -> dict[str, Any]:
    if (
        isinstance(levels_to_analyze, bool)
        or not isinstance(levels_to_analyze, int)
        or levels_to_analyze < 1
        or levels_to_analyze > 10000
    ):
        raise MicrostructureError(
            "levels_to_analyze must be between 1 and 10000."
        )

    validated = _validate_book(book)

    bids = validated["bids"][:levels_to_analyze]
    asks = validated["asks"][:levels_to_analyze]

    bid_depth = sum(
        item["volume"] for item in bids
    )

    ask_depth = sum(
        item["volume"] for item in asks
    )

    total = bid_depth + ask_depth

    if total <= 0:
        raise MicrostructureError(
            "Total order book depth must be positive."
        )

    imbalance = (
        (bid_depth - ask_depth) / total
    )

    return {
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "total_depth": total,
        "depth_imbalance": imbalance,
        "depth_imbalance_percent": imbalance * 100.0,
        "dominant_side": (
            "BID"
            if imbalance > 0
            else "ASK"
            if imbalance < 0
            else "BALANCED"
        ),
        "levels_requested": levels_to_analyze,
        "bid_levels_used": len(bids),
        "ask_levels_used": len(asks),
    }


def calculate_liquidity_distribution(
    book: Mapping[str, Any],
    levels_to_analyze: int = 10,
) -> dict[str, Any]:
    if (
        isinstance(levels_to_analyze, bool)
        or not isinstance(levels_to_analyze, int)
        or not 1 <= levels_to_analyze <= 10000
    ):
        raise MicrostructureError(
            "levels_to_analyze must be between 1 and 10000."
        )

    validated = _validate_book(book)

    bids = validated["bids"][:levels_to_analyze]
    asks = validated["asks"][:levels_to_analyze]

    bid_total = sum(
        item["volume"] for item in bids
    )

    ask_total = sum(
        item["volume"] for item in asks
    )

    return {
        "bid_distribution": [
            {
                "price": item["price"],
                "volume": item["volume"],
                "share_percent": (
                    item["volume"] / bid_total * 100.0
                ),
            }
            for item in bids
        ],
        "ask_distribution": [
            {
                "price": item["price"],
                "volume": item["volume"],
                "share_percent": (
                    item["volume"] / ask_total * 100.0
                ),
            }
            for item in asks
        ],
        "bid_total": bid_total,
        "ask_total": ask_total,
    }


def _validate_history(
    values: Sequence[Any],
    field: str,
) -> list[float]:
    if (
        isinstance(values, (str, bytes))
        or not isinstance(values, Sequence)
    ):
        raise MicrostructureError(
            f"{field} must be a sequence."
        )

    if len(values) > 100000:
        raise MicrostructureError(
            f"{field} contains too many observations."
        )

    return [
        _number(
            value,
            f"{field}[{index}]",
            allow_zero=True,
        )
        for index, value in enumerate(values)
    ]


def detect_spread_anomaly(
    current_spread: float,
    historical_spreads: Sequence[Any],
    minimum_ratio: float = 2.0,
) -> dict[str, Any]:
    current = _number(
        current_spread,
        "current_spread",
        allow_zero=True,
    )

    history = _validate_history(
        historical_spreads,
        "historical_spreads",
    )

    ratio_threshold = _number(
        minimum_ratio,
        "minimum_ratio",
    )

    if ratio_threshold <= 1:
        raise MicrostructureError(
            "minimum_ratio must be greater than 1."
        )

    if len(history) < 5:
        return {
            "detected": False,
            "status": "INSUFFICIENT_HISTORY",
            "historical_median": None,
            "current_spread": current,
            "spread_ratio": None,
        }

    baseline = median(history)

    if baseline <= 0:
        return {
            "detected": current > 0,
            "status": (
                "ABNORMAL_SPREAD"
                if current > 0
                else "ZERO_BASELINE"
            ),
            "historical_median": baseline,
            "current_spread": current,
            "spread_ratio": None,
        }

    ratio = current / baseline
    detected = ratio >= ratio_threshold

    return {
        "detected": detected,
        "status": (
            "ABNORMAL_SPREAD"
            if detected
            else "NORMAL_SPREAD"
        ),
        "current_spread": current,
        "historical_median": baseline,
        "spread_ratio": ratio,
        "threshold": ratio_threshold,
    }


def detect_depth_deterioration(
    current_depth: float,
    historical_depths: Sequence[Any],
    minimum_retention: float = 0.50,
) -> dict[str, Any]:
    current = _number(
        current_depth,
        "current_depth",
        allow_zero=True,
    )

    history = _validate_history(
        historical_depths,
        "historical_depths",
    )

    retention_threshold = _number(
        minimum_retention,
        "minimum_retention",
    )

    if retention_threshold > 1:
        raise MicrostructureError(
            "minimum_retention must not exceed 1."
        )

    if len(history) < 5:
        return {
            "detected": False,
            "status": "INSUFFICIENT_HISTORY",
            "retention_ratio": None,
        }

    baseline = median(history)

    if baseline <= 0:
        return {
            "detected": current == 0,
            "status": (
                "LIQUIDITY_DETERIORATION"
                if current == 0
                else "ZERO_BASELINE"
            ),
            "historical_median_depth": baseline,
            "retention_ratio": None,
        }

    retention = current / baseline
    detected = retention < retention_threshold

    return {
        "detected": detected,
        "status": (
            "LIQUIDITY_DETERIORATION"
            if detected
            else "DEPTH_ACCEPTABLE"
        ),
        "current_depth": current,
        "historical_median_depth": baseline,
        "retention_ratio": retention,
        "retention_percent": retention * 100.0,
        "minimum_retention": retention_threshold,
    }


def evaluate_market_quality(
    spread_analysis: Mapping[str, Any],
    depth_analysis: Mapping[str, Any],
    spread_anomaly: Mapping[str, Any],
    depth_deterioration: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = {
        "spread_analysis": spread_analysis,
        "depth_analysis": depth_analysis,
        "spread_anomaly": spread_anomaly,
        "depth_deterioration": depth_deterioration,
    }

    for name, value in inputs.items():
        if not isinstance(value, Mapping):
            raise MicrostructureError(
                f"{name} must be a mapping."
            )

    spread = _number(
        spread_analysis.get("spread"),
        "spread",
        allow_zero=True,
    )

    imbalance = _number(
        depth_analysis.get("depth_imbalance"),
        "depth_imbalance",
        allow_zero=True,
    )

    if imbalance > 1:
        raise MicrostructureError(
            "depth_imbalance must not exceed 1."
        )

    if spread <= 0:
        raise MicrostructureError(
            "A zero spread is invalid for this assessment."
        )

    spread_status = spread_anomaly.get("status")
    depth_status = depth_deterioration.get("status")

    allowed_spread = {
        "ABNORMAL_SPREAD",
        "NORMAL_SPREAD",
        "INSUFFICIENT_HISTORY",
    }

    allowed_depth = {
        "LIQUIDITY_DETERIORATION",
        "DEPTH_ACCEPTABLE",
        "INSUFFICIENT_HISTORY",
    }

    if spread_status not in allowed_spread:
        raise MicrostructureError(
            "Unknown spread anomaly status."
        )

    if depth_status not in allowed_depth:
        raise MicrostructureError(
            "Unknown depth deterioration status."
        )

    score = 100.0
    warnings = []

    if spread_status == "ABNORMAL_SPREAD":
        score -= 30
        warnings.append("ABNORMAL_SPREAD")

    elif spread_status == "INSUFFICIENT_HISTORY":
        score -= 10
        warnings.append("SPREAD_BASELINE_UNAVAILABLE")

    if depth_status == "LIQUIDITY_DETERIORATION":
        score -= 30
        warnings.append("LIQUIDITY_DETERIORATION")

    elif depth_status == "INSUFFICIENT_HISTORY":
        score -= 10
        warnings.append("DEPTH_BASELINE_UNAVAILABLE")

    if abs(imbalance) >= 0.80:
        score -= 10
        warnings.append("EXTREME_BOOK_IMBALANCE")

    score = max(0.0, min(100.0, score))

    if score >= 80:
        quality = "GOOD"
    elif score >= 60:
        quality = "MODERATE"
    elif score >= 40:
        quality = "POOR"
    else:
        quality = "VERY_POOR"

    return {
        "market_quality_score": score,
        "quality": quality,
        "warnings": warnings,
        "execution_conditions": (
            "FAVORABLE"
            if score >= 80
            else "CAUTION"
            if score >= 60
            else "UNFAVORABLE"
        ),
        "score_is_calibrated": False,
        "execution_authorized": False,
    }


def analyze_microstructure(
    book: Mapping[str, Any],
    historical_spreads: Sequence[Any],
    historical_depths: Sequence[Any],
    levels_to_analyze: int = 5,
) -> dict[str, Any]:
    validated = _validate_book(book)

    best_bid = validated["bids"][0]["price"]
    best_ask = validated["asks"][0]["price"]

    spread = analyze_spread(
        best_bid,
        best_ask,
    )

    depth = analyze_book_depth(
        validated,
        levels_to_analyze,
    )

    distribution = calculate_liquidity_distribution(
        validated,
        levels_to_analyze,
    )

    spread_anomaly = detect_spread_anomaly(
        spread["spread"],
        historical_spreads,
    )

    depth_deterioration = detect_depth_deterioration(
        depth["total_depth"],
        historical_depths,
    )

    quality = evaluate_market_quality(
        spread,
        depth,
        spread_anomaly,
        depth_deterioration,
    )

    return {
        "spread": spread,
        "depth": depth,
        "liquidity_distribution": distribution,
        "spread_anomaly": spread_anomaly,
        "depth_deterioration": depth_deterioration,
        "market_quality": quality,
        "data_source_verified": False,
        "actual_executable_liquidity_verified": False,
        "execution_authorized": False,
    }
