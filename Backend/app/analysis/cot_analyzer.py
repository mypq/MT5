"""
GOLD BOT - COT Intelligence

Analyzes Commitments of Traders positioning reports.

Supported trader categories:

- Managed Money
- Producer / Merchant
- Swap Dealers
- Other Reportables
- Nonreportable

Calculates:

- Long / short positioning
- Net positioning
- Weekly positioning changes
- Open-interest percentages
- Historical COT index
- Positioning extremes
- Category comparisons

IMPORTANT:
This module analyzes supplied COT report data.
It does not download reports from the CFTC.
It does not execute trading orders.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from math import isfinite
from typing import Any

SUPPORTED_SYMBOL = "XAUUSD"

MINIMUM_REPORTS_FOR_INDEX = 2
MAXIMUM_REPORTS = 520

DEFAULT_EXTREME_THRESHOLD = 90.0

SUPPORTED_CATEGORIES = (
"managed_money",
"producer_merchant",
"swap_dealers",
"other_reportables",
"nonreportable",
)

class COTAnalysisError(ValueError):
"""Raised when COT input data is invalid."""

def _number(value: Any, field: str) -> float:

if isinstance(value, bool):
    raise COTAnalysisError(
        f"{field} must be numeric."
    )

try:
    result = float(value)
except (TypeError, ValueError):
    raise COTAnalysisError(
        f"{field} must be numeric."
    )

if not isfinite(result):
    raise COTAnalysisError(
        f"{field} must be finite."
    )

return result

def _report_date(value: Any, field: str) -> date:

if not isinstance(value, str):
    raise COTAnalysisError(
        f"{field} must use YYYY-MM-DD format."
    )

try:
    parsed = date.fromisoformat(value)
except ValueError:
    raise COTAnalysisError(
        f"{field} must use YYYY-MM-DD format."
    )

return parsed

def _validate_category(
category: Any,
category_name: str,
open_interest: float,
) -> dict[str, float]:

if not isinstance(category, Mapping):
    raise COTAnalysisError(
        f"{category_name} must be an object."
    )

long_positions = _number(
    category.get("long"),
    f"{category_name}.long",
)

short_positions = _number(
    category.get("short"),
    f"{category_name}.short",
)

spreading = _number(
    category.get("spreading", 0),
    f"{category_name}.spreading",
)

if min(
    long_positions,
    short_positions,
    spreading,
) < 0:
    raise COTAnalysisError(
        f"{category_name} positions cannot be negative."
    )

if (
    long_positions > open_interest
    or short_positions > open_interest
    or spreading > open_interest
):
    raise COTAnalysisError(
        f"{category_name} positions exceed open interest."
    )

net = long_positions - short_positions

return {
    "long": long_positions,
    "short": short_positions,
    "spreading": spreading,
    "net": net,
    "gross_directional": (
        long_positions + short_positions
    ),
    "net_percent_of_open_interest": (
        net / open_interest * 100.0
        if open_interest > 0
        else 0.0
    ),
    "long_percent_of_open_interest": (
        long_positions / open_interest * 100.0
        if open_interest > 0
        else 0.0
    ),
    "short_percent_of_open_interest": (
        short_positions / open_interest * 100.0
        if open_interest > 0
        else 0.0
    ),
}

def _validate_report(
report: Mapping[str, Any],
index: int,
) -> dict[str, Any]:

if not isinstance(report, Mapping):
    raise COTAnalysisError(
        f"Report at index {index} must be an object."
    )

report_date = _report_date(
    report.get("report_date"),
    f"reports[{index}].report_date",
)

open_interest = _number(
    report.get("open_interest"),
    f"reports[{index}].open_interest",
)

if open_interest <= 0:
    raise COTAnalysisError(
        f"Open interest must be positive in report {index}."
    )

categories = report.get("categories")

if not isinstance(categories, Mapping):
    raise COTAnalysisError(
        f"reports[{index}].categories must be an object."
    )

missing = [
    name
    for name in SUPPORTED_CATEGORIES
    if name not in categories
]

if missing:
    raise COTAnalysisError(
        f"Report {index} is missing categories: "
        + ", ".join(missing)
    )

validated_categories = {}

for name in SUPPORTED_CATEGORIES:

    validated_categories[name] = _validate_category(
        categories[name],
        f"reports[{index}].categories.{name}",
        open_interest,
    )

return {
    "report_date": report_date,
    "open_interest": open_interest,
    "categories": validated_categories,
}

def _calculate_cot_index(
current_net: float,
historical_net_values: Sequence[float],
) -> float | None:

if len(historical_net_values) < MINIMUM_REPORTS_FOR_INDEX:
    return None

minimum = min(historical_net_values)
maximum = max(historical_net_values)

if maximum == minimum:
    return 50.0

return (
    (current_net - minimum)
    / (maximum - minimum)
) * 100.0

def _positioning_condition(
cot_index: float | None,
net_position: float,
extreme_threshold: float,
) -> str:

if cot_index is None:
    return "INSUFFICIENT_HISTORY"

if (
    cot_index >= extreme_threshold
    and net_position > 0
):
    return "EXTREME_NET_LONG"

if (
    cot_index <= 100.0 - extreme_threshold
    and net_position < 0
):
    return "EXTREME_NET_SHORT"

if net_position > 0:
    return "NET_LONG"

if net_position < 0:
    return "NET_SHORT"

return "NEUTRAL"

def _analyze_category(
current: Mapping[str, float],
previous: Mapping[str, float] | None,
historical_net_values: Sequence[float],
extreme_threshold: float,
) -> dict[str, Any]:

cot_index = _calculate_cot_index(
    current["net"],
    historical_net_values,
)

if previous is None:
    weekly_change = None
else:
    weekly_change = {
        "long": (
            current["long"] - previous["long"]
        ),
        "short": (
            current["short"] - previous["short"]
        ),
        "spreading": (
            current["spreading"] - previous["spreading"]
        ),
        "net": (
            current["net"] - previous["net"]
        ),
    }

return {
    "long": current["long"],
    "short": current["short"],
    "spreading": current["spreading"],
    "net": current["net"],
    "gross_directional": current["gross_directional"],
    "net_percent_of_open_interest": (
        current["net_percent_of_open_interest"]
    ),
    "long_percent_of_open_interest": (
        current["long_percent_of_open_interest"]
    ),
    "short_percent_of_open_interest": (
        current["short_percent_of_open_interest"]
    ),
    "weekly_change": weekly_change,
    "cot_index": cot_index,
    "positioning_condition": _positioning_condition(
        cot_index,
        current["net"],
        extreme_threshold,
    ),
}

def analyze_cot(
reports: Sequence[Mapping[str, Any]],
extreme_threshold: float = DEFAULT_EXTREME_THRESHOLD,
report_type: str = "DISAGGREGATED",
market_name: str = "GOLD",
) -> dict[str, Any]:

if isinstance(reports, (str, bytes)) or not isinstance(
    reports,
    Sequence,
):
    raise COTAnalysisError(
        "Reports must be a sequence."
    )

if not 2 <= len(reports) <= MAXIMUM_REPORTS:
    raise COTAnalysisError(
        f"Reports must contain between 2 and {MAXIMUM_REPORTS} entries."
    )

if (
    isinstance(extreme_threshold, bool)
    or not isinstance(extreme_threshold, (int, float))
    or not isfinite(extreme_threshold)
    or not 70.0 <= extreme_threshold <= 99.0
):
    raise COTAnalysisError(
        "extreme_threshold must be between 70 and 99."
    )

if not isinstance(report_type, str) or not report_type.strip():
    raise COTAnalysisError(
        "report_type must be a non-empty string."
    )

if not isinstance(market_name, str) or not market_name.strip():
    raise COTAnalysisError(
        "market_name must be a non-empty string."
    )

validated = [
    _validate_report(report, index)
    for index, report in enumerate(reports)
]

validated.sort(
    key=lambda report: report["report_date"]
)

for index in range(1, len(validated)):

    if (
        validated[index]["report_date"]
        == validated[index - 1]["report_date"]
    ):
        raise COTAnalysisError(
            "Duplicate COT report dates are not allowed."
        )

current_report = validated[-1]
previous_report = validated[-2]

historical_reports = validated[:-1]

categories_result = {}

for category_name in SUPPORTED_CATEGORIES:

    current_category = (
        current_report["categories"][category_name]
    )

    previous_category = (
        previous_report["categories"][category_name]
    )

    historical_net = [
        report["categories"][category_name]["net"]
        for report in historical_reports
    ]

    categories_result[category_name] = _analyze_category(
        current_category,
        previous_category,
        historical_net,
        float(extreme_threshold),
    )

current_open_interest = current_report["open_interest"]

previous_open_interest = previous_report["open_interest"]

open_interest_change = (
    current_open_interest - previous_open_interest
)

open_interest_change_percent = (
    open_interest_change / previous_open_interest * 100.0
)

managed_money = categories_result["managed_money"]

producer_merchant = categories_result["producer_merchant"]

swap_dealers = categories_result["swap_dealers"]

if managed_money["net"] > 0:
    managed_money_bias = "NET_LONG"
elif managed_money["net"] < 0:
    managed_money_bias = "NET_SHORT"
else:
    managed_money_bias = "NEUTRAL"

if producer_merchant["net"] > 0:
    producer_bias = "NET_LONG"
elif producer_merchant["net"] < 0:
    producer_bias = "NET_SHORT"
else:
    producer_bias = "NEUTRAL"

return {
    "module": "cot_intelligence",
    "symbol": SUPPORTED_SYMBOL,
    "market_name": market_name.upper().strip(),
    "report_type": report_type.upper().strip(),
    "data_available": True,
    "current_report_date": (
        current_report["report_date"].isoformat()
    ),
    "previous_report_date": (
        previous_report["report_date"].isoformat()
    ),
    "open_interest": {
        "current": current_open_interest,
        "previous": previous_open_interest,
        "change": open_interest_change,
        "change_percent": open_interest_change_percent,
    },
    "categories": categories_result,
    "positioning_summary": {
        "managed_money_bias": managed_money_bias,
        "producer_merchant_bias": producer_bias,
        "swap_dealers_net": swap_dealers["net"],
        "managed_money_net_change": (
            managed_money["weekly_change"]["net"]
        ),
        "managed_money_cot_index": (
            managed_money["cot_index"]
        ),
    },
    "historical_reports_used": len(validated),
    "settings": {
        "extreme_threshold": extreme_threshold,
    },
    "limitations": [
        "Reports must be supplied by a trusted COT data source.",
        "This module does not download CFTC reports.",
        "COT reports are periodic and do not represent live positions.",
        "Report positioning is not proof of future price direction.",
        "COT index depends on the supplied historical window.",
        "The latest report may describe positions from an earlier reporting date.",
        "Categories must match the selected COT report format.",
        "This module does not execute trading orders.",
    ],
}
