"""
GOLD BOT - Decision Fusion Engine

Central evidence aggregation and decision classification.

This module:

- Combines independent analyzer outputs.
- Evaluates directional agreement and disagreement.
- Applies configurable evidence weights.
- Rejects invalid, stale, or untrusted evidence.
- Calculates a transparent confidence score.
- Produces BUY / SELL / WAIT / NO_TRADE classifications.

SAFETY:

- This module never executes orders.
- A BUY or SELL classification is not an order authorization.
- Risk management and execution validation must run separately.
  """

from collections.abc import Mapping, Sequence
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
}

VALID_DIRECTIONS = {
"BULLISH",
"BEARISH",
"NEUTRAL",
}

VALID_ACTIONS = {
"BUY",
"SELL",
"WAIT",
"NO_TRADE",
}

MINIMUM_EVIDENCE_COUNT = 3
MINIMUM_DIRECTIONAL_SCORE = 20.0
MINIMUM_CONFIDENCE_FOR_ACTION = 60.0
MINIMUM_DATA_QUALITY = 60.0
MAXIMUM_EVIDENCE_ITEMS = 100

def _finite_number(
value: Any,
field: str,
) -> float:

if isinstance(value, bool):
    raise DecisionFusionError(
        f"{field} must be numeric."
    )

try:
    number = float(value)
except (TypeError, ValueError):
    raise DecisionFusionError(
        f"{field} must be numeric."
    )

if not isfinite(number):
    raise DecisionFusionError(
        f"{field} must be finite."
    )

return number

def _normalize_direction(
value: Any,
) -> str:

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

return mapping.get(
    normalized,
    "NEUTRAL",
)

def _validate_weights(
weights: Mapping[str, Any] | None,
) -> dict[str, float]:

configured = dict(DEFAULT_WEIGHTS)

if weights is None:
    return configured

if not isinstance(weights, Mapping):
    raise DecisionFusionError(
        "weights must be a mapping."
    )

for name, value in weights.items():

    if name not in configured:
        raise DecisionFusionError(
            f"Unknown analyzer weight: {name}"
        )

    weight = _finite_number(
        value,
        f"weights.{name}",
    )

    if weight <= 0 or weight > 10:
        raise DecisionFusionError(
            f"Weight for {name} must be > 0 and <= 10."
        )

    configured[name] = weight

return configured

def _validate_evidence(
evidence: Mapping[str, Any],
name: str,
reference_time: datetime,
) -> dict[str, Any]:

if not isinstance(evidence, Mapping):
    raise DecisionFusionError(
        f"Evidence for {name} must be a mapping."
    )

direction = _normalize_direction(
    evidence.get("direction")
)

confidence = _finite_number(
    evidence.get("confidence", 50),
    f"{name}.confidence",
)

quality = _finite_number(
    evidence.get("data_quality", 100),
    f"{name}.data_quality",
)

if not 0 <= confidence <= 100:
    raise DecisionFusionError(
        f"{name}.confidence must be between 0 and 100."
    )

if not 0 <= quality <= 100:
    raise DecisionFusionError(
        f"{name}.data_quality must be between 0 and 100."
    )

verified = (
    evidence.get("source_verified") is True
)

valid = (
    evidence.get("valid") is True
)

timestamp_value = evidence.get(
    "timestamp"
)

if not isinstance(timestamp_value, str):
    raise DecisionFusionError(
        f"{name}.timestamp must be ISO-8601."
    )

try:
    timestamp = datetime.fromisoformat(
        timestamp_value.replace("Z", "+00:00")
    )
except ValueError:
    raise DecisionFusionError(
        f"{name}.timestamp is invalid."
    )

if timestamp.tzinfo is None:
    raise DecisionFusionError(
        f"{name}.timestamp must include a timezone."
    )

timestamp = timestamp.astimezone(
    timezone.utc
)

age_seconds = (
    reference_time - timestamp
).total_seconds()

maximum_age = _finite_number(
    evidence.get("max_age_seconds", 300),
    f"{name}.max_age_seconds",
)

if maximum_age <= 0:
    raise DecisionFusionError(
        f"{name}.max_age_seconds must be positive."
    )

if age_seconds < 0:
    raise DecisionFusionError(
        f"{name} has a future timestamp."
    )

fresh = age_seconds <= maximum_age

return {
    "name": name,
    "direction": direction,
    "confidence": confidence,
    "data_quality": quality,
    "source_verified": verified,
    "valid": valid,
    "fresh": fresh,
    "age_seconds": age_seconds,
    "eligible": (
        valid
        and verified
        and fresh
        and quality >= MINIMUM_DATA_QUALITY
    ),
    "reason": evidence.get(
        "reason",
        "",
    ),
}

def _parse_reference_time(
value: str,
) -> datetime:

if not isinstance(value, str):
    raise DecisionFusionError(
        "reference_time must be ISO-8601."
    )

try:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
except ValueError:
    raise DecisionFusionError(
        "reference_time is invalid."
    )

if parsed.tzinfo is None:
    raise DecisionFusionError(
        "reference_time must include a timezone."
    )

return parsed.astimezone(
    timezone.utc
)

def fuse_decisions(
evidence_items: Mapping[str, Mapping[str, Any]],
reference_time: str,
weights: Mapping[str, Any] | None = None,
minimum_confidence: float = MINIMUM_CONFIDENCE_FOR_ACTION,
) -> dict[str, Any]:

if not isinstance(evidence_items, Mapping):
    raise DecisionFusionError(
        "evidence_items must be a mapping."
    )

if not evidence_items:
    raise DecisionFusionError(
        "At least one evidence item is required."
    )

if len(evidence_items) > MAXIMUM_EVIDENCE_ITEMS:
    raise DecisionFusionError(
        "Too many evidence items."
    )

minimum_confidence = _finite_number(
    minimum_confidence,
    "minimum_confidence",
)

if not 0 <= minimum_confidence <= 100:
    raise DecisionFusionError(
        "minimum_confidence must be between 0 and 100."
    )

reference = _parse_reference_time(
    reference_time
)

configured_weights = _validate_weights(
    weights
)

accepted = []
rejected = []

for name, evidence in evidence_items.items():

    if name not in configured_weights:
        rejected.append({
            "analyzer": name,
            "reason": "NO_CONFIGURED_WEIGHT",
        })
        continue

    item = _validate_evidence(
        evidence,
        name,
        reference,
    )

    if not item["eligible"]:

        reasons = []

        if not item["valid"]:
            reasons.append("INVALID_EVIDENCE")

        if not item["source_verified"]:
            reasons.append("SOURCE_NOT_VERIFIED")

        if not item["fresh"]:
            reasons.append("STALE_EVIDENCE")

        if item["data_quality"] < MINIMUM_DATA_QUALITY:
            reasons.append("LOW_DATA_QUALITY")

        rejected.append({
            "analyzer": name,
            "reasons": reasons,
        })

        continue

    accepted.append(item)

directional = [
    item for item in accepted
    if item["direction"] in {
        "BULLISH",
        "BEARISH",
    }
]

bullish_score = 0.0
bearish_score = 0.0

bullish_weight = 0.0
bearish_weight = 0.0

total_directional_weight = 0.0

for item in directional:

    weight = configured_weights[
        item["name"]
    ]

    contribution = (
        weight
        * item["confidence"]
        * (item["data_quality"] / 100.0)
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

total_score = (
    bullish_score + bearish_score
)

directional_confidence = (
    max(
        bullish_score,
        bearish_score,
    )
    / total_score
    * 100.0
    if total_score > 0
    else 0.0
)

agreement_percent = (
    max(
        bullish_weight,
        bearish_weight,
    )
    / total_directional_weight
    * 100.0
    if total_directional_weight > 0
    else 0.0
)

conflict_percent = (
    min(
        bullish_weight,
        bearish_weight,
    )
    / total_directional_weight
    * 100.0
    if total_directional_weight > 0
    else 0.0
)

average_quality = (
    sum(
        item["data_quality"]
        for item in accepted
    ) / len(accepted)
    if accepted
    else 0.0
)

effective_count = len(accepted)

confidence = (
    directional_confidence
    * (average_quality / 100.0)
    if directional
    else 0.0
)

reasons = []

if effective_count < MINIMUM_EVIDENCE_COUNT:
    reasons.append(
        "INSUFFICIENT_INDEPENDENT_EVIDENCE"
    )

if not directional:
    reasons.append(
        "NO_DIRECTIONAL_EVIDENCE"
    )

if dominant == "NEUTRAL":
    reasons.append(
        "NO_CLEAR_DIRECTION"
    )

if agreement_percent < 65:
    reasons.append(
        "STRONG_ANALYZER_CONFLICT"
    )

if conflict_percent >= 30:
    reasons.append(
        "SIGNIFICANT_OPPOSING_EVIDENCE"
    )

if average_quality < MINIMUM_DATA_QUALITY:
    reasons.append(
        "INSUFFICIENT_DATA_QUALITY"
    )

if confidence < minimum_confidence:
    reasons.append(
        "CONFIDENCE_BELOW_REQUIRED_THRESHOLD"
    )

if rejected:
    reasons.append(
        "SOME_EVIDENCE_REJECTED"
    )

if (
    effective_count < MINIMUM_EVIDENCE_COUNT
    or not directional
    or dominant == "NEUTRAL"
    or agreement_percent < 65
    or conflict_percent >= 30
    or average_quality < MINIMUM_DATA_QUALITY
    or confidence < minimum_confidence
):

    action = (
        "NO_TRADE"
        if not accepted
        else "WAIT"
    )

elif dominant == "BULLISH":
    action = "BUY"

elif dominant == "BEARISH":
    action = "SELL"

else:
    action = "WAIT"

return {
    "module": "decision_fusion_engine",
    "action": action,
    "direction": dominant,
    "confidence_percent": round(
        confidence,
        2,
    ),
    "directional_agreement_percent": round(
        agreement_percent,
        2,
    ),
    "opposing_evidence_percent": round(
        conflict_percent,
        2,
    ),
    "average_data_quality": round(
        average_quality,
        2,
    ),
    "accepted_evidence_count": effective_count,
    "rejected_evidence_count": len(rejected),
    "bullish_score": round(
        bullish_score,
        4,
    ),
    "bearish_score": round(
        bearish_score,
        4,
    ),
    "accepted_evidence": accepted,
    "rejected_evidence": rejected,
    "reasons": reasons,
    "execution_authorized": False,
    "limitations": [
        "Analyzer confidence values are not calibrated probabilities.",
        "Weights are initial configurable engineering weights, not optimized parameters.",
        "Correlated analyzers may count the same market evidence more than once.",
        "Source verification must be performed by trusted adapters outside this module.",
        "All analyzers must use compatible symbols, timestamps, and market conventions.",
        "BUY and SELL are analytical classifications, not trade orders.",
        "Independent risk validation is mandatory before any automated execution.",
        "This module does not guarantee profitable decisions.",
    ],
}
