"""
GOLD BOT - Market Microstructure Intelligence

Analyzes order book and bid/ask market quality.

Features:

- Bid/ask spread analysis
- Order book depth
- Bid/ask volume imbalance
- Price-level liquidity distribution
- Abnormal spread detection
- Liquidity deterioration
- Market quality scoring
- Execution condition assessment

IMPORTANT:

- Requires genuine broker-provided market data.
- Order book snapshots are not proof of executable liquidity.
- Displayed orders can be cancelled or changed.
- This module does not execute trades.
  """

from collections.abc import Mapping, Sequence
from math import isfinite
from statistics import median
from typing import Any

class MicrostructureError(ValueError):
"""Raised when market microstructure data is invalid."""

def _positive(
value: Any,
field: str,
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
    )

if not isfinite(number) or number <= 0:
    raise MicrostructureError(
        f"{field} must be positive and finite."
    )

return number

def _non_negative(
value: Any,
field: str,
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
    )

if not isfinite(number) or number < 0:
    raise MicrostructureError(
        f"{field} must be non-negative and finite."
    )

return number

def _validate_book(
book: Mapping[str, Any],
) -> dict[str, Any]:

if not isinstance(book, Mapping):
    raise MicrostructureError(
        "Order book must be a mapping."
    )

bids = book.get("bids")
asks = book.get("asks")

if (
    isinstance(bids, (str, bytes))
    or not isinstance(bids, Sequence)
    or not bids
):
    raise MicrostructureError(
        "At least one bid level is required."
    )

if (
    isinstance(asks, (str, bytes))
    or not isinstance(asks, Sequence)
    or not asks
):
    raise MicrostructureError(
        "At least one ask level is required."
    )

validated_bids = []
validated_asks = []

for side_name, levels, output in (
    ("bids", bids, validated_bids),
    ("asks", asks, validated_asks),
):

    if len(levels) > 10000:
        raise MicrostructureError(
            "Too many order book levels."
        )

    for index, level in enumerate(levels):

        if not isinstance(level, Mapping):
            raise MicrostructureError(
                f"Invalid {side_name} level."
            )

        price = _positive(
            level.get("price"),
            f"{side_name}[{index}].price",
        )

        volume = _non_negative(
            level.get("volume"),
            f"{side_name}[{index}].volume",
        )

        if volume <= 0:
            continue

        output.append({
            "price": price,
            "volume": volume,
        })

if not validated_bids or not validated_asks:
    raise MicrostructureError(
        "Both sides must contain positive volume."
    )

if validated_bids[0]["price"] <= 0:
    raise MicrostructureError(
        "Invalid best bid."
    )

if validated_asks[0]["price"] <= 0:
    raise MicrostructureError(
        "Invalid best ask."
    )

if max(
    item["price"]
    for item in validated_bids
) >= min(
    item["price"]
    for item in validated_asks
):
    raise MicrostructureError(
        "Crossed or locked order book is not accepted."
    )

return {
    "bids": validated_bids,
    "asks": validated_asks,
}

def analyze_spread(
best_bid: float,
best_ask: float,
reference_price: float | None = None,
) -> dict[str, Any]:

bid = _positive(
    best_bid,
    "best_bid",
)

ask = _positive(
    best_ask,
    "best_ask",
)

if ask <= bid:
    raise MicrostructureError(
        "Ask must be greater than bid."
    )

spread = ask - bid
midpoint = (ask + bid) / 2.0

spread_bps = (
    spread / midpoint
) * 10000.0

result = {
    "best_bid": bid,
    "best_ask": ask,
    "spread": spread,
    "midpoint": midpoint,
    "spread_basis_points": spread_bps,
    "spread_percent": (
        spread / midpoint
    ) * 100.0,
}

if reference_price is not None:

    reference = _positive(
        reference_price,
        "reference_price",
    )

    result["spread_points"] = (
        spread / reference
    )

return result

def analyze_book_depth(
book: Mapping[str, Any],
levels_to_analyze: int = 5,
) -> dict[str, Any]:

validated = _validate_book(
    book
)

if (
    isinstance(levels_to_analyze, bool)
    or not isinstance(levels_to_analyze, int)
    or levels_to_analyze < 1
):
    raise MicrostructureError(
        "levels_to_analyze must be positive."
    )

bids = sorted(
    validated["bids"],
    key=lambda item: item["price"],
    reverse=True,
)[:levels_to_analyze]

asks = sorted(
    validated["asks"],
    key=lambda item: item["price"],
)[:levels_to_analyze]

bid_depth = sum(
    item["volume"]
    for item in bids
)

ask_depth = sum(
    item["volume"]
    for item in asks
)

total = bid_depth + ask_depth

imbalance = (
    (bid_depth - ask_depth) / total
    if total > 0
    else 0.0
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
    "levels_analyzed": levels_to_analyze,
    "bid_levels_used": len(bids),
    "ask_levels_used": len(asks),
}

def calculate_liquidity_distribution(
book: Mapping[str, Any],
levels_to_analyze: int = 10,
) -> dict[str, Any]:

validated = _validate_book(
    book
)

if (
    isinstance(levels_to_analyze, bool)
    or not isinstance(levels_to_analyze, int)
    or levels_to_analyze < 1
):
    raise MicrostructureError(
        "levels_to_analyze must be positive."
    )

bids = sorted(
    validated["bids"],
    key=lambda item: item["price"],
    reverse=True,
)[:levels_to_analyze]

asks = sorted(
    validated["asks"],
    key=lambda item: item["price"],
)[:levels_to_analyze]

bid_total = sum(
    item["volume"]
    for item in bids
)

ask_total = sum(
    item["volume"]
    for item in asks
)

return {
    "bid_distribution": [
        {
            "price": item["price"],
            "volume": item["volume"],
            "share_percent": (
                item["volume"] / bid_total * 100.0
                if bid_total > 0
                else 0.0
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
                if ask_total > 0
                else 0.0
            ),
        }
        for item in asks
    ],
    "bid_total": bid_total,
    "ask_total": ask_total,
}

def detect_spread_anomaly(
current_spread: float,
historical_spreads: Sequence[Any],
minimum_ratio: float = 2.0,
) -> dict[str, Any]:

current = _non_negative(
    current_spread,
    "current_spread",
)

history = []

if (
    isinstance(historical_spreads, (str, bytes))
    or not isinstance(historical_spreads, Sequence)
):
    raise MicrostructureError(
        "historical_spreads must be a sequence."
    )

for value in historical_spreads:

    history.append(
        _non_negative(
            value,
            "historical_spread",
        )
    )

if (
    isinstance(minimum_ratio, bool)
    or not isinstance(minimum_ratio, (int, float))
    or not isfinite(minimum_ratio)
    or minimum_ratio <= 1
):
    raise MicrostructureError(
        "minimum_ratio must be greater than 1."
    )

if len(history) < 5:

    return {
        "detected": False,
        "status": "INSUFFICIENT_HISTORY",
        "historical_median": None,
        "current_spread": current,
    }

baseline = median(history)

if baseline <= 0:

    return {
        "detected": False,
        "status": "ZERO_BASELINE",
        "historical_median": baseline,
        "current_spread": current,
    }

ratio = current / baseline

return {
    "detected": ratio >= minimum_ratio,
    "status": (
        "ABNORMAL_SPREAD"
        if ratio >= minimum_ratio
        else "NORMAL_SPREAD"
    ),
    "current_spread": current,
    "historical_median": baseline,
    "spread_ratio": ratio,
    "threshold": minimum_ratio,
}

def detect_depth_deterioration(
current_depth: float,
historical_depths: Sequence[Any],
minimum_retention: float = 0.50,
) -> dict[str, Any]:

current = _non_negative(
    current_depth,
    "current_depth",
)

if (
    isinstance(historical_depths, (str, bytes))
    or not isinstance(historical_depths, Sequence)
):
    raise MicrostructureError(
        "historical_depths must be a sequence."
    )

history = [
    _non_negative(
        value,
        "historical_depth",
    )
    for value in historical_depths
]

if (
    isinstance(minimum_retention, bool)
    or not isinstance(minimum_retention, (int, float))
    or not isfinite(minimum_retention)
    or not 0 < minimum_retention <= 1
):
    raise MicrostructureError(
        "minimum_retention must be between 0 and 1."
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
        "detected": False,
        "status": "ZERO_BASELINE",
        "retention_ratio": None,
    }

retention = current / baseline

return {
    "detected": retention < minimum_retention,
    "status": (
        "LIQUIDITY_DETERIORATION"
        if retention < minimum_retention
        else "DEPTH_ACCEPTABLE"
    ),
    "current_depth": current,
    "historical_median_depth": baseline,
    "retention_ratio": retention,
    "retention_percent": retention * 100.0,
    "minimum_retention": minimum_retention,
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

spread = _positive(
    spread_analysis.get("spread"),
    "spread",
)

imbalance = depth_analysis.get(
    "depth_imbalance"
)

if (
    isinstance(imbalance, bool)
    or not isinstance(imbalance, (int, float))
    or not isfinite(imbalance)
    or not -1 <= imbalance <= 1
):
    raise MicrostructureError(
        "Invalid depth imbalance."
    )

score = 100.0
warnings = []

if spread_anomaly.get("status") == "ABNORMAL_SPREAD":

    score -= 30
    warnings.append(
        "ABNORMAL_SPREAD"
    )

elif spread_anomaly.get("status") == "INSUFFICIENT_HISTORY":

    score -= 10
    warnings.append(
        "SPREAD_BASELINE_UNAVAILABLE"
    )

if depth_deterioration.get("status") == "LIQUIDITY_DETERIORATION":

    score -= 30
    warnings.append(
        "LIQUIDITY_DETERIORATION"
    )

elif depth_deterioration.get("status") == "INSUFFICIENT_HISTORY":

    score -= 10
    warnings.append(
        "DEPTH_BASELINE_UNAVAILABLE"
    )

if spread <= 0:
    score -= 50
    warnings.append(
        "INVALID_SPREAD"
    )

if abs(imbalance) >= 0.80:

    score -= 10
    warnings.append(
        "EXTREME_BOOK_IMBALANCE"
    )

score = max(
    0.0,
    min(100.0, score),
)

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
    "execution_authorized": False,
    "score_is_calibrated": False,
}

def analyze_microstructure(
book: Mapping[str, Any],
historical_spreads: Sequence[Any],
historical_depths: Sequence[Any],
levels_to_analyze: int = 5,
) -> dict[str, Any]:

validated = _validate_book(
    book
)

best_bid = max(
    item["price"]
    for item in validated["bids"]
)

best_ask = min(
    item["price"]
    for item in validated["asks"]
)

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

total_depth = depth["total_depth"]

depth_deterioration = detect_depth_deterioration(
    total_depth,
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
