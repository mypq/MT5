"""
GOLD BOT - Adaptive Model Validation

Evaluates predictive model quality using chronological
out-of-sample observations.

Features:

- Walk-forward evaluation
- Out-of-sample metrics
- Accuracy and Brier score
- Log loss
- Confidence calibration
- Performance drift detection
- Regime-specific evaluation
- Model comparison
- Overfitting risk assessment
- Automation validation gate

IMPORTANT:

- This module evaluates predictions; it does not train models.
- Historical performance does not guarantee future performance.
- Validation requires chronological, independently generated observations.
- Passing validation does not independently authorize live trading.
  """

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite, log
from statistics import mean
from typing import Any

class ModelValidationError(ValueError):
"""Raised when model validation input is invalid."""

def _timestamp(value: Any) -> datetime:

if not isinstance(value, str):
    raise ModelValidationError(
        "timestamp must be an ISO-8601 string."
    )

try:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
except ValueError:
    raise ModelValidationError(
        "Invalid timestamp."
    )

if parsed.tzinfo is None:
    raise ModelValidationError(
        "Timestamp must include timezone information."
    )

return parsed.astimezone(timezone.utc)

def _probability(
value: Any,
field: str,
) -> float:

if isinstance(value, bool):
    raise ModelValidationError(
        f"{field} must be numeric."
    )

try:
    number = float(value)
except (TypeError, ValueError):
    raise ModelValidationError(
        f"{field} must be numeric."
    )

if not isfinite(number) or not 0 <= number <= 1:
    raise ModelValidationError(
        f"{field} must be between 0 and 1."
    )

return number

def _validate_observations(
observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:

if (
    isinstance(observations, (str, bytes))
    or not isinstance(observations, Sequence)
):
    raise ModelValidationError(
        "observations must be a sequence."
    )

result = []
seen_ids = set()

for index, item in enumerate(observations):

    if not isinstance(item, Mapping):
        raise ModelValidationError(
            f"Invalid observation at index {index}."
        )

    observation_id = item.get(
        "observation_id"
    )

    if (
        not isinstance(observation_id, str)
        or not observation_id.strip()
    ):
        raise ModelValidationError(
            "Every observation requires an ID."
        )

    if observation_id in seen_ids:
        raise ModelValidationError(
            f"Duplicate observation ID: {observation_id}."
        )

    seen_ids.add(observation_id)

    timestamp = _timestamp(
        item.get("timestamp")
    )

    actual = item.get("actual")

    if actual not in (0, 1, False, True):
        raise ModelValidationError(
            "actual must be binary: 0 or 1."
        )

    probability = _probability(
        item.get("predicted_probability"),
        "predicted_probability",
    )

    regime = item.get(
        "regime",
        "UNKNOWN",
    )

    if not isinstance(regime, str) or not regime.strip():
        raise ModelValidationError(
            "regime must be a non-empty string."
        )

    model_id = item.get(
        "model_id",
        "DEFAULT",
    )

    if not isinstance(model_id, str) or not model_id.strip():
        raise ModelValidationError(
            "model_id must be a non-empty string."
        )

    result.append({
        "observation_id": observation_id,
        "timestamp": timestamp,
        "actual": int(actual),
        "predicted_probability": probability,
        "predicted_class": int(probability >= 0.5),
        "regime": regime.strip().upper(),
        "model_id": model_id.strip(),
    })

result.sort(
    key=lambda item: item["timestamp"]
)

return result

def calculate_metrics(
observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:

data = _validate_observations(
    observations
)

if not data:
    return {
        "sample_count": 0,
        "status": "INSUFFICIENT_DATA",
        "accuracy": None,
        "brier_score": None,
        "log_loss": None,
        "precision": None,
        "recall": None,
        "f1_score": None,
    }

actual = [
    item["actual"]
    for item in data
]

predicted = [
    item["predicted_class"]
    for item in data
]

probabilities = [
    item["predicted_probability"]
    for item in data
]

true_positive = sum(
    1 for a, p in zip(actual, predicted)
    if a == 1 and p == 1
)

true_negative = sum(
    1 for a, p in zip(actual, predicted)
    if a == 0 and p == 0
)

false_positive = sum(
    1 for a, p in zip(actual, predicted)
    if a == 0 and p == 1
)

false_negative = sum(
    1 for a, p in zip(actual, predicted)
    if a == 1 and p == 0
)

accuracy = (
    true_positive + true_negative
) / len(data)

brier = mean(
    (probability - outcome) ** 2
    for probability, outcome in zip(
        probabilities,
        actual,
    )
)

epsilon = 1e-15

log_loss = mean(
    -(
        outcome * log(
            max(epsilon, probability)
        )
        + (1 - outcome) * log(
            max(epsilon, 1 - probability)
        )
    )
    for probability, outcome in zip(
        probabilities,
        actual,
    )
)

precision_denominator = (
    true_positive + false_positive
)

recall_denominator = (
    true_positive + false_negative
)

precision = (
    true_positive / precision_denominator
    if precision_denominator
    else None
)

recall = (
    true_positive / recall_denominator
    if recall_denominator
    else None
)

f1 = (
    2 * precision * recall / (precision + recall)
    if precision is not None
    and recall is not None
    and precision + recall > 0
    else None
)

return {
    "sample_count": len(data),
    "status": "CALCULATED",
    "accuracy": accuracy,
    "brier_score": brier,
    "log_loss": log_loss,
    "precision": precision,
    "recall": recall,
    "f1_score": f1,
    "confusion_matrix": {
        "true_positive": true_positive,
        "true_negative": true_negative,
        "false_positive": false_positive,
        "false_negative": false_negative,
    },
    "positive_rate": mean(actual),
    "predicted_positive_rate": mean(predicted),
}

def evaluate_calibration(
observations: Sequence[Mapping[str, Any]],
bins: int = 10,
minimum_bin_samples: int = 5,
) -> dict[str, Any]:

data = _validate_observations(
    observations
)

if (
    isinstance(bins, bool)
    or not isinstance(bins, int)
    or bins < 2
    or bins > 100
):
    raise ModelValidationError(
        "bins must be between 2 and 100."
    )

if (
    isinstance(minimum_bin_samples, bool)
    or not isinstance(minimum_bin_samples, int)
    or minimum_bin_samples < 1
):
    raise ModelValidationError(
        "minimum_bin_samples must be positive."
    )

groups = defaultdict(list)

for item in data:

    probability = item[
        "predicted_probability"
    ]

    index = min(
        bins - 1,
        int(probability * bins),
    )

    groups[index].append(item)

results = []
weighted_error = 0.0
usable_samples = 0

for index in range(bins):

    items = groups[index]

    if len(items) < minimum_bin_samples:

        results.append({
            "bin": index,
            "sample_count": len(items),
            "status": "INSUFFICIENT_BIN_DATA",
            "average_confidence": None,
            "observed_frequency": None,
            "calibration_error": None,
        })

        continue

    confidence = mean(
        item["predicted_probability"]
        for item in items
    )

    frequency = mean(
        item["actual"]
        for item in items
    )

    error = abs(
        confidence - frequency
    )

    weighted_error += error * len(items)
    usable_samples += len(items)

    results.append({
        "bin": index,
        "sample_count": len(items),
        "status": "CALCULATED",
        "average_confidence": confidence,
        "observed_frequency": frequency,
        "calibration_error": error,
    })

return {
    "bin_count": bins,
    "total_samples": len(data),
    "usable_samples": usable_samples,
    "expected_calibration_error": (
        weighted_error / usable_samples
        if usable_samples
        else None
    ),
    "bins": results,
}

def walk_forward_validation(
observations: Sequence[Mapping[str, Any]],
initial_training_size: int = 100,
test_window_size: int = 30,
step_size: int | None = None,
) -> dict[str, Any]:

data = _validate_observations(
    observations
)

for name, value in {
    "initial_training_size": initial_training_size,
    "test_window_size": test_window_size,
}.items():

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
    ):
        raise ModelValidationError(
            f"{name} must be positive."
        )

step = (
    test_window_size
    if step_size is None
    else step_size
)

if (
    isinstance(step, bool)
    or not isinstance(step, int)
    or step < 1
):
    raise ModelValidationError(
        "step_size must be positive."
    )

if len(data) <= initial_training_size:
    return {
        "status": "INSUFFICIENT_DATA",
        "folds": [],
        "fold_count": 0,
    }

folds = []
test_start = initial_training_size

while test_start < len(data):

    test_end = min(
        test_start + test_window_size,
        len(data),
    )

    training = data[:test_start]
    testing = data[test_start:test_end]

    folds.append({
        "fold": len(folds) + 1,
        "training_count": len(training),
        "testing_count": len(testing),
        "training_start": training[0]["timestamp"].isoformat(),
        "training_end": training[-1]["timestamp"].isoformat(),
        "testing_start": testing[0]["timestamp"].isoformat(),
        "testing_end": testing[-1]["timestamp"].isoformat(),
        "metrics": calculate_metrics(testing),
    })

    test_start += step

return {
    "status": "CALCULATED",
    "method": "EXPANDING_WINDOW_WALK_FORWARD",
    "fold_count": len(folds),
    "initial_training_size": initial_training_size,
    "test_window_size": test_window_size,
    "step_size": step,
    "training_data_used_for_model_fitting": False,
    "folds": folds,
}

def detect_performance_drift(
observations: Sequence[Mapping[str, Any]],
window_size: int = 50,
minimum_accuracy_drop: float = 0.10,
) -> dict[str, Any]:

data = _validate_observations(
    observations
)

if (
    isinstance(window_size, bool)
    or not isinstance(window_size, int)
    or window_size < 2
):
    raise ModelValidationError(
        "window_size must be at least 2."
    )

drop = _probability(
    minimum_accuracy_drop,
    "minimum_accuracy_drop",
)

if len(data) < window_size * 2:

    return {
        "status": "INSUFFICIENT_DATA",
        "drift_detected": False,
    }

previous = data[
    -(window_size * 2):-window_size
]

recent = data[
    -window_size:
]

previous_accuracy = calculate_metrics(
    previous
)["accuracy"]

recent_accuracy = calculate_metrics(
    recent
)["accuracy"]

difference = (
    previous_accuracy - recent_accuracy
)

detected = difference >= drop

return {
    "status": (
        "PERFORMANCE_DRIFT_CANDIDATE"
        if detected
        else "NO_SIGNIFICANT_ACCURACY_DROP"
    ),
    "drift_detected": detected,
    "previous_accuracy": previous_accuracy,
    "recent_accuracy": recent_accuracy,
    "accuracy_drop": difference,
    "threshold": drop,
    "previous_sample_count": len(previous),
    "recent_sample_count": len(recent),
}

def evaluate_regime_performance(
observations: Sequence[Mapping[str, Any]],
minimum_samples: int = 20,
) -> dict[str, Any]:

data = _validate_observations(
    observations
)

if (
    isinstance(minimum_samples, bool)
    or not isinstance(minimum_samples, int)
    or minimum_samples < 1
):
    raise ModelValidationError(
        "minimum_samples must be positive."
    )

groups = defaultdict(list)

for item in data:
    groups[item["regime"]].append(item)

results = {}

for regime, items in groups.items():

    metrics = calculate_metrics(
        items
    )

    results[regime] = {
        "sample_count": len(items),
        "status": (
            "SUFFICIENT"
            if len(items) >= minimum_samples
            else "INSUFFICIENT_DATA"
        ),
        "metrics": metrics,
    }

return {
    "regime_count": len(results),
    "regimes": results,
}

def assess_overfitting_risk(
training_observations: Sequence[Mapping[str, Any]],
testing_observations: Sequence[Mapping[str, Any]],
minimum_samples: int = 30,
maximum_accuracy_gap: float = 0.15,
) -> dict[str, Any]:

training = _validate_observations(
    training_observations
)

testing = _validate_observations(
    testing_observations
)

if (
    isinstance(minimum_samples, bool)
    or not isinstance(minimum_samples, int)
    or minimum_samples < 1
):
    raise ModelValidationError(
        "minimum_samples must be positive."
    )

threshold = _probability(
    maximum_accuracy_gap,
    "maximum_accuracy_gap",
)

train_ids = {
    item["observation_id"]
    for item in training
}

test_ids = {
    item["observation_id"]
    for item in testing
}

overlap = train_ids.intersection(
    test_ids
)

if overlap:
    raise ModelValidationError(
        "Training and testing observations overlap."
    )

if training and testing:

    if training[-1]["timestamp"] >= testing[0]["timestamp"]:
        raise ModelValidationError(
            "Training data must precede testing data."
        )

if (
    len(training) < minimum_samples
    or len(testing) < minimum_samples
):

    return {
        "status": "INSUFFICIENT_DATA",
        "overfitting_risk": "UNKNOWN",
        "accuracy_gap": None,
    }

training_accuracy = calculate_metrics(
    training
)["accuracy"]

testing_accuracy = calculate_metrics(
    testing
)["accuracy"]

gap = (
    training_accuracy - testing_accuracy
)

risk = (
    "HIGH"
    if gap >= threshold
    else "LOWER"
    if gap <= 0
    else "MODERATE"
)

return {
    "status": "CALCULATED",
    "overfitting_risk": risk,
    "training_accuracy": training_accuracy,
    "testing_accuracy": testing_accuracy,
    "accuracy_gap": gap,
    "maximum_acceptable_gap": threshold,
    "independent_observation_ids": True,
}

def compare_models(
model_observations: Mapping[
str,
Sequence[Mapping[str, Any]]
],
minimum_common_samples: int = 30,
) -> dict[str, Any]:

if not isinstance(model_observations, Mapping):
    raise ModelValidationError(
        "model_observations must be a mapping."
    )

if (
    isinstance(minimum_common_samples, bool)
    or not isinstance(minimum_common_samples, int)
    or minimum_common_samples < 1
):
    raise ModelValidationError(
        "minimum_common_samples must be positive."
    )

if len(model_observations) < 2:
    raise ModelValidationError(
        "At least two models are required."
    )

validated = {}

for model_id, observations in model_observations.items():

    if not isinstance(model_id, str) or not model_id.strip():
        raise ModelValidationError(
            "Model IDs cannot be empty."
        )

    data = _validate_observations(
        observations
    )

    indexed = {
        item["observation_id"]: item
        for item in data
    }

    validated[model_id] = indexed

common_ids = set.intersection(
    *[
        set(items.keys())
        for items in validated.values()
    ]
)

if len(common_ids) < minimum_common_samples:

    return {
        "status": "INSUFFICIENT_COMMON_SAMPLES",
        "common_sample_count": len(common_ids),
        "models": {},
        "ranking": [],
    }

results = {}

for model_id, indexed in validated.items():

    common = [
        indexed[item_id]
        for item_id in sorted(
            common_ids,
            key=lambda item_id: indexed[item_id]["timestamp"],
        )
    ]

    results[model_id] = calculate_metrics(
        common
    )

ranking = sorted(
    results.keys(),
    key=lambda model_id: (
        results[model_id]["brier_score"],
        results[model_id]["log_loss"],
    ),
)

return {
    "status": "CALCULATED",
    "common_sample_count": len(common_ids),
    "comparison_basis": "IDENTICAL_OBSERVATION_IDS",
    "ranking": ranking,
    "models": results,
    "statistical_significance_established": False,
}

def evaluate_automation_validation_gate(
metrics: Mapping[str, Any],
calibration: Mapping[str, Any],
drift: Mapping[str, Any],
overfitting: Mapping[str, Any],
minimum_samples: int = 200,
minimum_accuracy: float = 0.55,
maximum_brier_score: float = 0.25,
maximum_calibration_error: float = 0.10,
) -> dict[str, Any]:

for name, value in {
    "metrics": metrics,
    "calibration": calibration,
    "drift": drift,
    "overfitting": overfitting,
}.items():

    if not isinstance(value, Mapping):
        raise ModelValidationError(
            f"{name} must be a mapping."
        )

if (
    isinstance(minimum_samples, bool)
    or not isinstance(minimum_samples, int)
    or minimum_samples < 1
):
    raise ModelValidationError(
        "minimum_samples must be positive."
    )

accuracy_threshold = _probability(
    minimum_accuracy,
    "minimum_accuracy",
)

brier_threshold = _probability(
    maximum_brier_score,
    "maximum_brier_score",
)

calibration_threshold = _probability(
    maximum_calibration_error,
    "maximum_calibration_error",
)

failures = []

sample_count = metrics.get(
    "sample_count",
    0,
)

accuracy = metrics.get(
    "accuracy"
)

brier = metrics.get(
    "brier_score"
)

calibration_error = calibration.get(
    "expected_calibration_error"
)

if (
    isinstance(sample_count, bool)
    or not isinstance(sample_count, int)
    or sample_count < minimum_samples
):
    failures.append(
        "INSUFFICIENT_VALIDATION_SAMPLES"
    )

if accuracy is None or not 0 <= accuracy <= 1:
    failures.append(
        "ACCURACY_UNAVAILABLE"
    )
elif accuracy < accuracy_threshold:
    failures.append(
        "ACCURACY_BELOW_THRESHOLD"
    )

if brier is None or not 0 <= brier <= 1:
    failures.append(
        "BRIER_SCORE_UNAVAILABLE"
    )
elif brier > brier_threshold:
    failures.append(
        "BRIER_SCORE_ABOVE_THRESHOLD"
    )

if (
    calibration_error is None
    or not 0 <= calibration_error <= 1
):
    failures.append(
        "CALIBRATION_UNAVAILABLE"
    )
elif calibration_error > calibration_threshold:
    failures.append(
        "CALIBRATION_ERROR_TOO_HIGH"
    )

if drift.get("status") != "NO_SIGNIFICANT_ACCURACY_DROP":
    failures.append(
        "PERFORMANCE_DRIFT_NOT_CLEARED"
    )

if overfitting.get("status") != "CALCULATED":
    failures.append(
        "OVERFITTING_ASSESSMENT_UNAVAILABLE"
    )
elif overfitting.get("overfitting_risk") == "HIGH":
    failures.append(
        "HIGH_OVERFITTING_RISK"
    )

return {
    "validation_status": (
        "CONDITIONAL_PASS"
        if not failures
        else "REJECTED"
    ),
    "checks_passed": not failures,
    "failure_reasons": failures,
    "minimum_samples": minimum_samples,
    "minimum_accuracy": accuracy_threshold,
    "maximum_brier_score": brier_threshold,
    "maximum_calibration_error": calibration_threshold,
    "live_trading_authorized": False,
    "manual_review_required": True,
    "note": (
        "This validation result is not an execution "
        "authorization or a guarantee of future performance."
    ),
}
