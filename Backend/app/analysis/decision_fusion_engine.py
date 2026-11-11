"""
GOLD BOT - Decision Fusion Engine

Combines validated analyzer evidence into a transparent analytical
classification. This module never executes or authorizes trades.

Safety:
- XAUUSD only.
- Rejects unverified, invalid, stale, future-dated, or low-quality evidence.
- Requires a minimum number of accepted analyzer results.
- Detects directional disagreement.
- BUY / SELL are analytical classifications only.
- execution_authorized is always False.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from math import isfinite
from typing import Any


class DecisionFusionError(ValueError):
    """Raised when decision fusion input is invalid."""


DEFAULT_WEIGHTS = {
    "trend": 1.4,
    "price_action": 1.3,
    "market_structure": 1.4,
    "liquidity": 1.2,
    "supply_demand": 1.2,
    "volume": 1.0,
    "indicators": 0.9,
    "market_regime": 1.1,
    "volume_profile": 0.9,
    "order_flow": 1.2,
    "multi_timeframe": 1.5,
    "quantitative": 1.0,
    "intermarket": 0.8,
    "macro": 0.8,
    "news": 0.6,
    "execution": 1.0,
    "market_data": 1.3,
    "session": 0.6,
    "anomaly": 0.9,
    "cot": 0.7,
    "options": 0.7,
    "strategy_research": 0.8,
}

MINIMUM_EVIDENCE_COUNT = 3
MINIMUM_DIRECTIONAL_AGREEMENT = 65.0
MINIMUM_CONFIDENCE_FOR_ACTION = 60.0
MINIMUM_DATA_QUALITY = 60.0
MAXIMUM_EVIDENCE_ITEMS = 100
MAXIMUM_REFERENCE_FUTURE_SECONDS = 60.0
DEFAULT_MAXIMUM_EVIDENCE_AGE_SECONDS = 300.0


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise DecisionFusionError(f"{field} must be numeric.")

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise DecisionFusionError(f"{field} must be numeric.") from None

    if not isfinite(number):
        raise DecisionFusionError(f"{field} must be finite.")

    return number


def _parse_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise DecisionFusionError(f"{field} must be an ISO-8601 string.")

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise DecisionFusionError(f"{field} is invalid.") from None

    if parsed.tzinfo is None:
        raise DecisionFusionError(f"{field} must include a timezone.")

    return parsed.astimezone(timezone.utc)


def _parse_reference_time(value: Any) -> datetime:
    reference = _parse_datetime(value, "reference_time")
    now = datetime.now(timezone.utc)

    if (reference - now).total_seconds() > MAXIMUM_REFERENCE_FUTURE_SECONDS:
        raise DecisionFusionError("reference_time is too far in the future.")

    return reference


def _normalize_direction(value: Any) -> str:
    if not isinstance(value, str):
        return "NEUTRAL"

    normalized = value.strip().upper()

    mapping = {
        "BUY": "BULLISH",
        "LONG": "BULLISH",
        "UP": "BULLISH",
        "BULLISH": "BULLISH",
        "SELL": "BEARISH",
        "SHORT": "BEARISH",
        "DOWN": "BEARISH",
        "BEARISH": "BEARISH",
        "SIDEWAYS": "NEUTRAL",
        "RANGING": "NEUTRAL",
        "MIXED": "NEUTRAL",
        "NEUTRAL": "NEUTRAL",
    }

    return mapping.get(normalized, "NEUTRAL")


def _validate_weights(
    weights: Mapping[str, Any] | None,
) -> dict[str, float]:
    configured = dict(DEFAULT_WEIGHTS)

    if weights is None:
        return configured

    if not isinstance(weights, Mapping):
        raise DecisionFusionError("weights must be a mapping.")

    for name, value in weights.items():
        if name not in configured:
            raise DecisionFusionError(f"Unknown analyzer weight: {name}")

        weight = _finite_number(value, f"weights.{name}")

        if weight <= 0 or weight > 10:
            raise DecisionFusionError(
                f"Weight for {name} must be greater than 0 and at most 10."
            )

        configured[name] = weight

    return configured


def _validate_evidence(
    name: str,
    evidence: Mapping[str, Any],
    reference_time: datetime,
) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        raise DecisionFusionError("Evidence must be a mapping.")

    symbol = evidence.get("symbol")

    if not isinstance(symbol, str) or symbol.strip().upper() != "XAUUSD":
        raise DecisionFusionError("Evidence symbol must be XAUUSD.")

    if evidence.get("valid") is not True:
        raise DecisionFusionError("Evidence is not marked valid.")

    if evidence.get("source_verified") is not True:
        raise DecisionFusionError("Evidence source is not verified.")

    confidence = _finite_number(
        evidence.get("confidence"),
        f"{name}.confidence",
    )
    quality = _finite_number(
        evidence.get("data_quality"),
        f"{name}.data_quality",
    )

    if not 0 <= confidence <= 100:
        raise DecisionFusionError("Confidence must be between 0 and 100.")

    if not 0 <= quality <= 100:
        raise DecisionFusionError("Data quality must be between 0 and 100.")

    if quality < MINIMUM_DATA_QUALITY:
        raise DecisionFusionError("Data quality is below the minimum.")

    timestamp = _parse_datetime(
        evidence.get("timestamp"),
        f"{name}.timestamp",
    )

    now = datetime.now(timezone.utc)

    if timestamp > now:
        raise DecisionFusionError("Evidence timestamp is in the future.")

    age_seconds = (reference_time - timestamp).total_seconds()

    if age_seconds < 0:
        raise DecisionFusionError(
            "Evidence timestamp is later than reference_time."
        )

    maximum_age = _finite_number(
        evidence.get(
            "max_age_seconds",
            DEFAULT_MAXIMUM_EVIDENCE_AGE_SECONDS,
        ),
        f"{name}.max_age_seconds",
    )

    if maximum_age <= 0:
        raise DecisionFusionError("Maximum evidence age must be positive.")

    if age_seconds > maximum_age:
        raise DecisionFusionError("Evidence is stale.")

    direction = _normalize_direction(evidence.get("direction"))

    return {
        "name": name,
        "symbol": "XAUUSD",
        "direction": direction,
        "confidence": confidence,
        "data_quality": quality,
        "timestamp": timestamp.isoformat(),
        "age_seconds": round(age_seconds, 2),
        "reason": str(evidence.get("reason", ""))[:500],
        "eligible": True,
    }


def fuse_decisions(
    evidence_items: Mapping[str, Mapping[str, Any]],
    reference_time: str,
    weights: Mapping[str, Any] | None = None,
    minimum_confidence: float = MINIMUM_CONFIDENCE_FOR_ACTION,
) -> dict[str, Any]:
    """
    Fuse analyzer evidence into BUY, SELL, WAIT, or NO_TRADE.

    Every evidence item must include:
    - symbol: XAUUSD
    - direction
    - confidence: 0..100
    - data_quality: 0..100
    - timestamp: timezone-aware ISO-8601
    - valid: True
    - source_verified: True

    source_verified must come from a trusted upstream data adapter.
    It must never be set merely because an analyzer produced a result.
    """

    if not isinstance(evidence_items, Mapping):
        raise DecisionFusionError("evidence_items must be a mapping.")

    if not evidence_items:
        raise DecisionFusionError("At least one evidence item is required.")

    if len(evidence_items) > MAXIMUM_EVIDENCE_ITEMS:
        raise DecisionFusionError("Too many evidence items.")

    minimum_confidence = _finite_number(
        minimum_confidence,
        "minimum_confidence",
    )

    if not 0 <= minimum_confidence <= 100:
        raise DecisionFusionError(
            "minimum_confidence must be between 0 and 100."
        )

    reference = _parse_reference_time(reference_time)
    configured_weights = _validate_weights(weights)

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []

    for raw_name, evidence in evidence_items.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            rejected.append({
                "analyzer": "UNKNOWN",
                "reason": "INVALID_ANALYZER_NAME",
            })
            continue

        name = raw_name.strip()

        if name not in configured_weights:
            rejected.append({
                "analyzer": name,
                "reason": "NO_CONFIGURED_WEIGHT",
            })
            continue

        try:
            item = _validate_evidence(name, evidence, reference)
            accepted.append(item)
        except DecisionFusionError as exc:
            rejected.append({
                "analyzer": name,
                "reason": str(exc),
            })

    bullish_score = 0.0
    bearish_score = 0.0
    bullish_weight = 0.0
    bearish_weight = 0.0
    total_directional_weight = 0.0

    directional_items = [
        item for item in accepted
        if item["direction"] in {"BULLISH", "BEARISH"}
    ]

    for item in directional_items:
        weight = configured_weights[item["name"]]
        quality_factor = item["data_quality"] / 100.0

        contribution = (
            weight
            * item["confidence"]
            * quality_factor
        )

        total_directional_weight += weight

        if item["direction"] == "BULLISH":
            bullish_score += contribution
            bullish_weight += weight
        else:
            bearish_score += contribution
            bearish_weight += weight

    if bullish_score > bearish_score:
        dominant = "BULLISH"
    elif bearish_score > bullish_score:
        dominant = "BEARISH"
    else:
        dominant = "NEUTRAL"

    total_directional_score = bullish_score + bearish_score

    directional_strength = (
        max(bullish_score, bearish_score)
        / (total_directional_weight * 100.0)
        * 100.0
        if total_directional_weight > 0
        else 0.0
    )

    agreement_percent = (
        max(bullish_weight, bearish_weight)
        / total_directional_weight
        * 100.0
        if total_directional_weight > 0
        else 0.0
    )

    opposing_percent = (
        min(bullish_weight, bearish_weight)
        / total_directional_weight
        * 100.0
        if total_directional_weight > 0
        else 0.0
    )

    average_quality = (
        sum(item["data_quality"] for item in accepted) / len(accepted)
        if accepted
        else 0.0
    )

    confidence = (
        (
            directional_strength
            + agreement_percent
        ) / 2.0
        * (average_quality / 100.0)
        if directional_items
        else 0.0
    )

    reasons: list[str] = []

    if len(accepted) < MINIMUM_EVIDENCE_COUNT:
        reasons.append("INSUFFICIENT_EVIDENCE_COUNT")

    if not directional_items:
        reasons.append("NO_DIRECTIONAL_EVIDENCE")

    if dominant == "NEUTRAL":
        reasons.append("NO_CLEAR_DIRECTION")

    if agreement_percent < MINIMUM_DIRECTIONAL_AGREEMENT:
        reasons.append("DIRECTIONAL_DISAGREEMENT")

    if opposing_percent >= 30.0:
        reasons.append("SIGNIFICANT_OPPOSING_EVIDENCE")

    if average_quality < MINIMUM_DATA_QUALITY:
        reasons.append("INSUFFICIENT_DATA_QUALITY")

    if confidence < minimum_confidence:
        reasons.append("CONFIDENCE_BELOW_THRESHOLD")

    if rejected:
        reasons.append("SOME_EVIDENCE_REJECTED")

    blocked = (
        len(accepted) < MINIMUM_EVIDENCE_COUNT
        or not directional_items
        or dominant == "NEUTRAL"
        or agreement_percent < MINIMUM_DIRECTIONAL_AGREEMENT
        or opposing_percent >= 30.0
        or average_quality < MINIMUM_DATA_QUALITY
        or confidence < minimum_confidence
    )

    if not accepted:
        action = "NO_TRADE"
    elif blocked:
        action = "WAIT"
    elif dominant == "BULLISH":
        action = "BUY"
    elif dominant == "BEARISH":
        action = "SELL"
    else:
        action = "WAIT"

    return {
        "module": "decision_fusion_engine",
        "symbol": "XAUUSD",
        "action": action,
        "direction": dominant,
        "confidence_percent": round(confidence, 2),
        "directional_strength_percent": round(
            directional_strength,
            2,
        ),
        "directional_agreement_percent": round(
            agreement_percent,
            2,
        ),
        "opposing_evidence_percent": round(
            opposing_percent,
            2,
        ),
        "average_data_quality": round(average_quality, 2),
        "accepted_evidence_count": len(accepted),
        "rejected_evidence_count": len(rejected),
        "bullish_score": round(bullish_score, 4),
        "bearish_score": round(bearish_score, 4),
        "accepted_evidence": accepted,
        "rejected_evidence": rejected,
        "reasons": reasons,
        "execution_authorized": False,
        "trading_signal_allowed": False,
        "limitations": [
            "Analyzer confidence values are not calibrated probabilities.",
            "Configured weights are engineering defaults, not optimized parameters.",
            "Correlated analyzers may count the same market evidence more than once.",
            "source_verified must be supplied by a trusted upstream adapter.",
            "Evidence must use compatible symbols, timestamps, and market conventions.",
            "A minimum evidence count does not prove statistical independence.",
            "BUY and SELL are analytical classifications, not trade orders.",
            "Independent risk and account-mode validation is mandatory.",
            "This module does not guarantee profitable decisions.",
        ],
    }
