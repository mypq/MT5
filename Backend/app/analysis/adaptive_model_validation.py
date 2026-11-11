"""
GOLD BOT - Adaptive Model Validation

Evaluates predictive models using chronological observations.

Features:
- Accuracy, precision, recall and F1
- Brier score and log loss
- Confidence calibration
- Expanding-window walk-forward evaluation
- Performance drift detection
- Regime-specific evaluation
- Fair model comparison
- Overfitting risk assessment
- Conservative automation validation gate

IMPORTANT:
- This module evaluates existing predictions; it does not train models.
- Walk-forward evaluation does not independently fit a model.
- Observations must come from an independent prediction process.
- Passing validation never authorizes live trading.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from math import isfinite, log
from statistics import mean
from typing import Any


class ModelValidationError(ValueError):
    """Raised when model validation input is invalid."""


def _numeric(
    value: Any,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
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
        ) from None

    if not isfinite(number):
        raise ModelValidationError(
            f"{field} must be finite."
        )

    if minimum is not None and number < minimum:
        raise ModelValidationError(
            f"{field} is below its minimum."
        )

    if maximum is not None and number > maximum:
        raise ModelValidationError(
            f"{field} exceeds its maximum."
        )

    return number


def _positive_integer(
    value: Any,
    field: str,
    minimum: int = 1,
    maximum: int = 1000000,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ModelValidationError(
            f"{field} must be an integer between "
            f"{minimum} and {maximum}."
        )

    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ModelValidationError(
            "timestamp must be an ISO-8601 string."
        )

    try:
        parsed = datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )
    except ValueError:
        raise ModelValidationError(
            "Invalid timestamp."
        ) from None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ModelValidationError(
            "Timestamp must include timezone information."
        )

    return parsed.astimezone(timezone.utc)


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

    if len(observations) > 1000000:
        raise ModelValidationError(
            "Too many observations."
        )

    result = []
    seen_ids = set()

    for index, item in enumerate(observations):
        if not isinstance(item, Mapping):
            raise ModelValidationError(
                f"Invalid observation at index {index}."
            )

        observation_id = item.get("observation_id")

        if (
            not isinstance(observation_id, str)
            or not observation_id.strip()
        ):
            raise ModelValidationError(
                f"Observation {index} requires an ID."
            )

        observation_id = observation_id.strip()

        if observation_id in seen_ids:
            raise ModelValidationError(
                f"Duplicate observation ID: {observation_id}."
            )

        seen_ids.add(observation_id)

        actual = item.get("actual")

        if (
            isinstance(actual, bool)
            and actual not in (True, False)
        ):
            raise ModelValidationError(
                "actual must be binary."
            )

        if (
            isinstance(actual, bool)
        ):
            actual_value = int(actual)
        elif (
            isinstance(actual, int)
            and actual in (0, 1)
        ):
            actual_value = actual
        else:
            raise ModelValidationError(
                "actual must be integer 0 or 1, or boolean."
            )

        probability = _numeric(
            item.get("predicted_probability"),
            "predicted_probability",
            0.0,
            1.0,
        )

        regime = item.get("regime", "UNKNOWN")
        model_id = item.get("model_id", "DEFAULT")

        if (
            not isinstance(regime, str)
            or not regime.strip()
            or len(regime) > 100
        ):
            raise ModelValidationError(
                "regime must be a non-empty string "
                "of at most 100 characters."
            )

        if (
            not isinstance(model_id, str)
            or not model_id.strip()
            or len(model_id) > 128
        ):
            raise ModelValidationError(
                "model_id must be a non-empty string "
                "of at most 128 characters."
            )

        result.append({
            "observation_id": observation_id,
            "timestamp": _timestamp(
                item.get("timestamp")
            ),
            "actual": actual_value,
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
    data = _validate_observations(observations)

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
        item["actual"] for item in data
    ]

    predicted = [
        item["predicted_class"] for item in data
    ]

    probabilities = [
        item["predicted_probability"] for item in data
    ]

    tp = sum(
        a == 1 and p == 1
        for a, p in zip(actual, predicted)
    )

    tn = sum(
        a == 0 and p == 0
        for a, p in zip(actual, predicted)
    )

    fp = sum(
        a == 0 and p == 1
        for a, p in zip(actual, predicted)
    )

    fn = sum(
        a == 1 and p == 0
        for a, p in zip(actual, predicted)
    )

    accuracy = (tp + tn) / len(data)

    brier = mean(
        (probability - outcome) ** 2
        for probability, outcome in zip(
            probabilities,
            actual,
        )
    )

    epsilon = 1e-15

    loss = mean(
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

    precision = (
        tp / (tp + fp)
        if tp + fp
        else None
    )

    recall = (
        tp / (tp + fn)
        if tp + fn
        else None
    )

    f1 = (
        2 * precision * recall / (precision + recall)
        if (
            precision is not None
            and recall is not None
            and precision + recall > 0
        )
        else None
    )

    return {
        "sample_count": len(data),
        "status": "CALCULATED",
        "accuracy": accuracy,
        "brier_score": brier,
        "log_loss": loss,
        "precision": precision,
        "recall": recall,
        "f1_score": f1,
        "confusion_matrix": {
            "true_positive": tp,
            "true_negative": tn,
            "false_positive": fp,
            "false_negative": fn,
        },
        "positive_rate": mean(actual),
        "predicted_positive_rate": mean(predicted),
    }


def evaluate_calibration(
    observations: Sequence[Mapping[str, Any]],
    bins: int = 10,
    minimum_bin_samples: int = 5,
) -> dict[str, Any]:
    data = _validate_observations(observations)

    _positive_integer(
        bins,
        "bins",
        2,
        100,
    )

    _positive_integer(
        minimum_bin_samples,
        "minimum_bin_samples",
    )

    groups = defaultdict(list)

    for item in data:
        probability = item["predicted_probability"]

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

        error = abs(confidence - frequency)

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
    data = _validate_observations(observations)

    initial = _positive_integer(
        initial_training_size,
        "initial_training_size",
    )

    window = _positive_integer(
        test_window_size,
        "test_window_size",
    )

    step = (
        window
        if step_size is None
        else _positive_integer(step_size, "step_size")
    )

    if len(data) <= initial:
        return {
            "status": "INSUFFICIENT_DATA",
            "folds": [],
            "fold_count": 0,
        }

    folds = []
    test_start = initial

    while test_start < len(data):
        test_end = min(
            test_start + window,
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
        "initial_training_size": initial,
        "test_window_size": window,
        "step_size": step,
        "training_data_used_for_model_fitting": False,
        "note": (
            "This evaluates pre-generated predictions only. "
            "It does not fit or retrain a model."
        ),
        "folds": folds,
    }


def detect_performance_drift(
    observations: Sequence[Mapping[str, Any]],
    window_size: int = 50,
    minimum_accuracy_drop: float = 0.10,
) -> dict[str, Any]:
    data = _validate_observations(observations)

    window = _positive_integer(
        window_size,
        "window_size",
        2,
    )

    drop = _numeric(
        minimum_accuracy_drop,
        "minimum_accuracy_drop",
        0.0,
        1.0,
    )

    if len(data) < window * 2:
        return {
            "status": "INSUFFICIENT_DATA",
            "drift_detected": False,
        }

    previous = data[-(window * 2):-window]
    recent = data[-window:]

    previous_accuracy = calculate_metrics(
        previous
    )["accuracy"]

    recent_accuracy = calculate_metrics(
        recent
    )["accuracy"]

    difference = previous_accuracy - recent_accuracy
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
    data = _validate_observations(observations)

    minimum = _positive_integer(
        minimum_samples,
        "minimum_samples",
    )

    groups = defaultdict(list)

    for item in data:
        groups[item["regime"]].append(item)

    results = {}

    for regime, items in groups.items():
        results[regime] = {
            "sample_count": len(items),
            "status": (
                "SUFFICIENT"
                if len(items) >= minimum
                else "INSUFFICIENT_DATA"
            ),
            "metrics": calculate_metrics(items),
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

    minimum = _positive_integer(
        minimum_samples,
        "minimum_samples",
    )

    threshold = _numeric(
        maximum_accuracy_gap,
        "maximum_accuracy_gap",
        0.0,
        1.0,
    )

    train_ids = {
        item["observation_id"] for item in training
    }

    test_ids = {
        item["observation_id"] for item in testing
    }

    if train_ids.intersection(test_ids):
        raise ModelValidationError(
            "Training and testing observations overlap."
        )

    if training and testing:
        if training[-1]["timestamp"] >= testing[0]["timestamp"]:
            raise ModelValidationError(
                "Training data must strictly precede testing data."
            )

    if (
        len(training) < minimum
        or len(testing) < minimum
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

    gap = training_accuracy - testing_accuracy

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
        Sequence[Mapping[str, Any]],
    ],
    minimum_common_samples: int = 30,
) -> dict[str, Any]:
    if not isinstance(model_observations, Mapping):
        raise ModelValidationError(
            "model_observations must be a mapping."
        )

    minimum = _positive_integer(
        minimum_common_samples,
        "minimum_common_samples",
    )

    if len(model_observations) < 2:
        raise ModelValidationError(
            "At least two models are required."
        )

    validated = {}

    for model_id, observations in model_observations.items():
        if (
            not isinstance(model_id, str)
            or not model_id.strip()
        ):
            raise ModelValidationError(
                "Model IDs cannot be empty."
            )

        data = _validate_observations(observations)

        indexed = {
            item["observation_id"]: item
            for item in data
        }

        for item in data:
            if item["model_id"] != model_id.strip():
                raise ModelValidationError(
                    f"Observation model_id mismatch for {model_id}."
                )

        validated[model_id.strip()] = indexed

    common_ids = set.intersection(
        *[
            set(items.keys())
            for items in validated.values()
        ]
    )

    if len(common_ids) < minimum:
        return {
            "status": "INSUFFICIENT_COMMON_SAMPLES",
            "common_sample_count": len(common_ids),
            "models": {},
            "ranking": [],
        }

    reference_model = next(iter(validated))
    reference = validated[reference_model]

    for model_id, indexed in validated.items():
        for observation_id in common_ids:
            reference_item = reference[observation_id]
            candidate = indexed[observation_id]

            if candidate["timestamp"] != reference_item["timestamp"]:
                raise ModelValidationError(
                    "Models must use identical timestamps "
                    "for common observations."
                )

            if candidate["actual"] != reference_item["actual"]:
                raise ModelValidationError(
                    "Models must use identical actual outcomes."
                )

    results = {}

    for model_id, indexed in validated.items():
        common = [
            indexed[item_id]
            for item_id in sorted(
                common_ids,
                key=lambda item_id: reference[item_id]["timestamp"],
            )
        ]

        results[model_id] = calculate_metrics(common)

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
        "comparison_basis": "IDENTICAL_OBSERVATIONS_AND_OUTCOMES",
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
    inputs = {
        "metrics": metrics,
        "calibration": calibration,
        "drift": drift,
        "overfitting": overfitting,
    }

    for name, value in inputs.items():
        if not isinstance(value, Mapping):
            raise ModelValidationError(
                f"{name} must be a mapping."
            )

    minimum = _positive_integer(
        minimum_samples,
        "minimum_samples",
    )

    accuracy_threshold = _numeric(
        minimum_accuracy,
        "minimum_accuracy",
        0.0,
        1.0,
    )

    brier_threshold = _numeric(
        maximum_brier_score,
        "maximum_brier_score",
        0.0,
        1.0,
    )

    calibration_threshold = _numeric(
        maximum_calibration_error,
        "maximum_calibration_error",
        0.0,
        1.0,
    )

    failures = []

    sample_count = metrics.get("sample_count")

    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count < minimum
    ):
        failures.append(
            "INSUFFICIENT_VALIDATION_SAMPLES"
        )

    accuracy = metrics.get("accuracy")

    if accuracy is None:
        failures.append("ACCURACY_UNAVAILABLE")
    else:
        try:
            accuracy = _numeric(
                accuracy,
                "accuracy",
                0.0,
                1.0,
            )
            if accuracy < accuracy_threshold:
                failures.append(
                    "ACCURACY_BELOW_THRESHOLD"
                )
        except ModelValidationError:
            failures.append("INVALID_ACCURACY")

    brier = metrics.get("brier_score")

    if brier is None:
        failures.append("BRIER_SCORE_UNAVAILABLE")
    else:
        try:
            brier = _numeric(
                brier,
                "brier_score",
                0.0,
                1.0,
            )
            if brier > brier_threshold:
                failures.append(
                    "BRIER_SCORE_ABOVE_THRESHOLD"
                )
        except ModelValidationError:
            failures.append("INVALID_BRIER_SCORE")

    calibration_error = calibration.get(
        "expected_calibration_error"
    )

    if calibration_error is None:
        failures.append("CALIBRATION_UNAVAILABLE")
    else:
        try:
            calibration_error = _numeric(
                calibration_error,
                "expected_calibration_error",
                0.0,
                1.0,
            )
            if calibration_error > calibration_threshold:
                failures.append(
                    "CALIBRATION_ERROR_TOO_HIGH"
                )
        except ModelValidationError:
            failures.append("INVALID_CALIBRATION_ERROR")

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
    elif overfitting.get("overfitting_risk") not in (
        "LOWER",
        "MODERATE",
    ):
        failures.append(
            "INVALID_OVERFITTING_ASSESSMENT"
        )

    return {
        "validation_status": (
            "CONDITIONAL_PASS"
            if not failures
            else "REJECTED"
        ),
        "checks_passed": not failures,
        "failure_reasons": failures,
        "minimum_samples": minimum,
        "minimum_accuracy": accuracy_threshold,
        "maximum_brier_score": brier_threshold,
        "maximum_calibration_error": calibration_threshold,
        "live_trading_authorized": False,
        "manual_review_required": True,
        "note": (
            "This validation is not execution authorization "
            "and does not guarantee future performance."
        ),
  }
