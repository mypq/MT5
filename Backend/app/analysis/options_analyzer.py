"""
GOLD BOT - Options Intelligence

Analyzes supplied gold options market data.

Features:

- Put / Call ratios
- Open interest concentration
- Call and Put walls
- Max Pain estimation
- Implied volatility
- ATM volatility
- Implied volatility skew
- Volatility term structure

IMPORTANT:

- Requires genuine options market data.
- Open interest is not the same as dealer positioning.
- Max Pain is a mathematical estimate, not a price prediction.
- This module does not execute trades.
  """

from collections.abc import Mapping, Sequence
from datetime import date
from math import isfinite
from typing import Any

SUPPORTED_SYMBOL = "XAUUSD"

MAXIMUM_EXPIRIES = 100
MAXIMUM_OPTIONS_PER_EXPIRY = 20000

MINIMUM_OPTIONS_PER_EXPIRY = 2

DEFAULT_WALL_TOP_PERCENT = 0.10

class OptionsAnalysisError(ValueError):
"""Raised when options input data is invalid."""

def _number(value: Any, field: str) -> float:

if isinstance(value, bool):
    raise OptionsAnalysisError(
        f"{field} must be numeric."
    )

try:
    result = float(value)
except (TypeError, ValueError):
    raise OptionsAnalysisError(
        f"{field} must be numeric."
    )

if not isfinite(result):
    raise OptionsAnalysisError(
        f"{field} must be finite."
    )

return result

def _date(value: Any, field: str) -> date:

if not isinstance(value, str):
    raise OptionsAnalysisError(
        f"{field} must use YYYY-MM-DD format."
    )

try:
    return date.fromisoformat(value)
except ValueError:
    raise OptionsAnalysisError(
        f"{field} must use YYYY-MM-DD format."
    )

def _validate_option(
option: Mapping[str, Any],
expiry_index: int,
option_index: int,
) -> dict[str, Any]:

prefix = (
    f"expiries[{expiry_index}].options[{option_index}]"
)

if not isinstance(option, Mapping):
    raise OptionsAnalysisError(
        f"{prefix} must be an object."
    )

strike = _number(
    option.get("strike"),
    f"{prefix}.strike",
)

open_interest = _number(
    option.get("open_interest"),
    f"{prefix}.open_interest",
)

volume = _number(
    option.get("volume", 0),
    f"{prefix}.volume",
)

implied_volatility = _number(
    option.get("implied_volatility"),
    f"{prefix}.implied_volatility",
)

option_type = option.get("type")

if not isinstance(option_type, str):
    raise OptionsAnalysisError(
        f"{prefix}.type must be CALL or PUT."
    )

option_type = option_type.upper().strip()

if option_type not in ("CALL", "PUT"):
    raise OptionsAnalysisError(
        f"{prefix}.type must be CALL or PUT."
    )

if strike <= 0:
    raise OptionsAnalysisError(
        f"{prefix}.strike must be positive."
    )

if open_interest < 0 or volume < 0:
    raise OptionsAnalysisError(
        f"{prefix} volumes cannot be negative."
    )

if not 0 <= implied_volatility <= 5:
    raise OptionsAnalysisError(
        f"{prefix}.implied_volatility must be between 0 and 5."
    )

return {
    "strike": strike,
    "type": option_type,
    "open_interest": open_interest,
    "volume": volume,
    "implied_volatility": implied_volatility,
}

def _validate_expiry(
expiry: Mapping[str, Any],
index: int,
as_of_date: date,
) -> dict[str, Any]:

if not isinstance(expiry, Mapping):
    raise OptionsAnalysisError(
        f"expiries[{index}] must be an object."
    )

expiry_date = _date(
    expiry.get("expiry_date"),
    f"expiries[{index}].expiry_date",
)

if expiry_date <= as_of_date:
    raise OptionsAnalysisError(
        f"Expiry {expiry_date.isoformat()} must be after the analysis date."
    )

options = expiry.get("options")

if (
    not isinstance(options, Sequence)
    or isinstance(options, (str, bytes))
):
    raise OptionsAnalysisError(
        f"expiries[{index}].options must be a sequence."
    )

if not (
    MINIMUM_OPTIONS_PER_EXPIRY
    <= len(options)
    <= MAXIMUM_OPTIONS_PER_EXPIRY
):
    raise OptionsAnalysisError(
        f"Expiry {index} must contain between "
        f"{MINIMUM_OPTIONS_PER_EXPIRY} and "
        f"{MAXIMUM_OPTIONS_PER_EXPIRY} options."
    )

contract_multiplier = _number(
    expiry.get("contract_multiplier", 1),
    f"expiries[{index}].contract_multiplier",
)

if contract_multiplier <= 0:
    raise OptionsAnalysisError(
        "Contract multiplier must be positive."
    )

validated_options = [
    _validate_option(option, index, option_index)
    for option_index, option in enumerate(options)
]

identities = set()

for option in validated_options:

    identity = (
        option["strike"],
        option["type"],
    )

    if identity in identities:
        raise OptionsAnalysisError(
            f"Duplicate option contract at expiry {index}."
        )

    identities.add(identity)

return {
    "expiry_date": expiry_date,
    "contract_multiplier": contract_multiplier,
    "options": validated_options,
}

def _calculate_max_pain(
options: Sequence[Mapping[str, Any]],
contract_multiplier: float,
) -> dict[str, Any]:

strikes = sorted({
    option["strike"]
    for option in options
})

results = []

for settlement_price in strikes:

    call_payout = 0.0
    put_payout = 0.0

    for option in options:

        strike = option["strike"]
        open_interest = option["open_interest"]

        if option["type"] == "CALL":

            intrinsic_value = max(
                settlement_price - strike,
                0.0,
            )

            call_payout += (
                intrinsic_value
                * open_interest
                * contract_multiplier
            )

        else:

            intrinsic_value = max(
                strike - settlement_price,
                0.0,
            )

            put_payout += (
                intrinsic_value
                * open_interest
                * contract_multiplier
            )

    total_payout = call_payout + put_payout

    results.append({
        "settlement_price": settlement_price,
        "call_payout": call_payout,
        "put_payout": put_payout,
        "total_payout": total_payout,
    })

minimum_payout = min(
    item["total_payout"]
    for item in results
)

max_pain_prices = [
    item["settlement_price"]
    for item in results
    if item["total_payout"] == minimum_payout
]

return {
    "price": max_pain_prices[0],
    "equivalent_minimum_prices": max_pain_prices,
    "estimated_minimum_payout": minimum_payout,
    "calculation": results,
}

def _calculate_walls(
options: Sequence[Mapping[str, Any]],
wall_top_percent: float,
) -> dict[str, Any]:

call_interest = {}
put_interest = {}

for option in options:

    target = (
        call_interest
        if option["type"] == "CALL"
        else put_interest
    )

    strike = option["strike"]

    target[strike] = (
        target.get(strike, 0.0)
        + option["open_interest"]
    )

def rank_levels(
    interest: Mapping[float, float],
) -> list[dict[str, float]]:

    return [
        {
            "strike": strike,
            "open_interest": amount,
        }
        for strike, amount in sorted(
            interest.items(),
            key=lambda item: (
                -item[1],
                item[0],
            ),
        )
    ]

call_levels = rank_levels(call_interest)
put_levels = rank_levels(put_interest)

total_call = sum(call_interest.values())
total_put = sum(put_interest.values())

return {
    "call_wall": (
        call_levels[0]
        if call_levels and call_levels[0]["open_interest"] > 0
        else None
    ),
    "put_wall": (
        put_levels[0]
        if put_levels and put_levels[0]["open_interest"] > 0
        else None
    ),
    "top_call_levels": [
        item for item in call_levels
        if total_call > 0
        and item["open_interest"] / total_call >= wall_top_percent
    ],
    "top_put_levels": [
        item for item in put_levels
        if total_put > 0
        and item["open_interest"] / total_put >= wall_top_percent
    ],
    "call_open_interest_by_strike": call_levels,
    "put_open_interest_by_strike": put_levels,
}

def _calculate_volatility(
options: Sequence[Mapping[str, Any]],
underlying_price: float,
) -> dict[str, Any]:

calls = [
    option for option in options
    if option["type"] == "CALL"
]

puts = [
    option for option in options
    if option["type"] == "PUT"
]

def weighted_iv(
    selected: Sequence[Mapping[str, Any]],
) -> float | None:

    valid = [
        option for option in selected
        if option["open_interest"] > 0
        and option["implied_volatility"] > 0
    ]

    total_weight = sum(
        option["open_interest"]
        for option in valid
    )

    if total_weight <= 0:
        return None

    return sum(
        option["implied_volatility"]
        * option["open_interest"]
        for option in valid
    ) / total_weight

nearest_call = min(
    calls,
    key=lambda option: abs(
        option["strike"] - underlying_price
    ),
    default=None,
)

nearest_put = min(
    puts,
    key=lambda option: abs(
        option["strike"] - underlying_price
    ),
    default=None,
)

atm_values = [
    option["implied_volatility"]
    for option in (nearest_call, nearest_put)
    if option is not None
    and option["implied_volatility"] > 0
]

atm_iv = (
    sum(atm_values) / len(atm_values)
    if atm_values
    else None
)

otm_calls = [
    option for option in calls
    if option["strike"] > underlying_price
    and option["implied_volatility"] > 0
]

otm_puts = [
    option for option in puts
    if option["strike"] < underlying_price
    and option["implied_volatility"] > 0
]

nearest_otm_call = min(
    otm_calls,
    key=lambda option: abs(
        option["strike"] - underlying_price
    ),
    default=None,
)

nearest_otm_put = min(
    otm_puts,
    key=lambda option: abs(
        option["strike"] - underlying_price
    ),
    default=None,
)

if (
    nearest_otm_call is not None
    and nearest_otm_put is not None
):

    iv_skew = (
        nearest_otm_put["implied_volatility"]
        - nearest_otm_call["implied_volatility"]
    )

else:
    iv_skew = None

return {
    "call_weighted_iv": weighted_iv(calls),
    "put_weighted_iv": weighted_iv(puts),
    "atm_iv_approximation": atm_iv,
    "nearest_otm_call": (
        {
            "strike": nearest_otm_call["strike"],
            "implied_volatility": nearest_otm_call["implied_volatility"],
        }
        if nearest_otm_call
        else None
    ),
    "nearest_otm_put": (
        {
            "strike": nearest_otm_put["strike"],
            "implied_volatility": nearest_otm_put["implied_volatility"],
        }
        if nearest_otm_put
        else None
    ),
    "nearest_otm_iv_skew": iv_skew,
    "iv_skew_interpretation": (
        "PUT_VOLATILITY_PREMIUM"
        if iv_skew is not None and iv_skew > 0
        else "CALL_VOLATILITY_PREMIUM"
        if iv_skew is not None and iv_skew < 0
        else "UNAVAILABLE_OR_BALANCED"
    ),
}

def _analyze_expiry(
expiry: Mapping[str, Any],
underlying_price: float,
wall_top_percent: float,
) -> dict[str, Any]:

options = expiry["options"]

calls = [
    option for option in options
    if option["type"] == "CALL"
]

puts = [
    option for option in options
    if option["type"] == "PUT"
]

call_oi = sum(
    option["open_interest"]
    for option in calls
)

put_oi = sum(
    option["open_interest"]
    for option in puts
)

call_volume = sum(
    option["volume"]
    for option in calls
)

put_volume = sum(
    option["volume"]
    for option in puts
)

total_oi = call_oi + put_oi

total_volume = call_volume + put_volume

walls = _calculate_walls(
    options,
    wall_top_percent,
)

volatility = _calculate_volatility(
    options,
    underlying_price,
)

max_pain = _calculate_max_pain(
    options,
    expiry["contract_multiplier"],
)

return {
    "expiry_date": expiry["expiry_date"].isoformat(),
    "days_to_expiry": None,
    "contract_multiplier": expiry["contract_multiplier"],
    "call_open_interest": call_oi,
    "put_open_interest": put_oi,
    "total_open_interest": total_oi,
    "put_call_oi_ratio": (
        put_oi / call_oi
        if call_oi > 0
        else None
    ),
    "call_volume": call_volume,
    "put_volume": put_volume,
    "total_volume": total_volume,
    "put_call_volume_ratio": (
        put_volume / call_volume
        if call_volume > 0
        else None
    ),
    "call_oi_share_percent": (
        call_oi / total_oi * 100.0
        if total_oi > 0
        else 0.0
    ),
    "put_oi_share_percent": (
        put_oi / total_oi * 100.0
        if total_oi > 0
        else 0.0
    ),
    "walls": walls,
    "volatility": volatility,
    "max_pain": max_pain,
}

def analyze_options(
expiries: Sequence[Mapping[str, Any]],
underlying_price: float,
as_of_date: str,
wall_top_percent: float = DEFAULT_WALL_TOP_PERCENT,
market_name: str = "GOLD",
) -> dict[str, Any]:

if (
    isinstance(expiries, (str, bytes))
    or not isinstance(expiries, Sequence)
):
    raise OptionsAnalysisError(
        "Expiries must be a sequence."
    )

if not 1 <= len(expiries) <= MAXIMUM_EXPIRIES:
    raise OptionsAnalysisError(
        f"Expiries must contain between 1 and {MAXIMUM_EXPIRIES} entries."
    )

underlying = _number(
    underlying_price,
    "underlying_price",
)

if underlying <= 0:
    raise OptionsAnalysisError(
        "Underlying price must be positive."
    )

analysis_date = _date(
    as_of_date,
    "as_of_date",
)

if (
    isinstance(wall_top_percent, bool)
    or not isinstance(wall_top_percent, (int, float))
    or not isfinite(wall_top_percent)
    or not 0 < wall_top_percent <= 1
):
    raise OptionsAnalysisError(
        "wall_top_percent must be greater than 0 and at most 1."
    )

if not isinstance(market_name, str) or not market_name.strip():
    raise OptionsAnalysisError(
        "market_name must be a non-empty string."
    )

validated = [
    _validate_expiry(expiry, index, analysis_date)
    for index, expiry in enumerate(expiries)
]

validated.sort(
    key=lambda item: item["expiry_date"]
)

seen_expiries = set()

results = []

for expiry in validated:

    expiry_date = expiry["expiry_date"]

    if expiry_date in seen_expiries:
        raise OptionsAnalysisError(
            "Duplicate expiry dates are not allowed."
        )

    seen_expiries.add(expiry_date)

    result = _analyze_expiry(
        expiry,
        underlying,
        float(wall_top_percent),
    )

    result["days_to_expiry"] = (
        expiry_date - analysis_date
    ).days

    results.append(result)

term_structure = [
    {
        "expiry_date": result["expiry_date"],
        "days_to_expiry": result["days_to_expiry"],
        "atm_iv_approximation": (
            result["volatility"]["atm_iv_approximation"]
        ),
    }
    for result in results
]

return {
    "module": "options_intelligence",
    "symbol": SUPPORTED_SYMBOL,
    "market_name": market_name.upper().strip(),
    "data_available": True,
    "analysis_date": analysis_date.isoformat(),
    "underlying_price": underlying,
    "expiries": results,
    "volatility_term_structure": term_structure,
    "settings": {
        "wall_top_percent": wall_top_percent,
    },
    "limitations": [
        "Requires genuine options open interest, volume and implied volatility.",
        "Options data may represent futures options rather than spot gold options.",
        "Max Pain assumes all listed open interest remains unchanged until settlement.",
        "Max Pain does not predict the future settlement price.",
        "Call and Put walls describe open interest concentration, not confirmed dealer hedging levels.",
        "Nearest out-of-the-money IV skew is a simple strike-based approximation, not a standardized 25-delta skew.",
        "ATM IV is approximated using the nearest available call and put contracts.",
        "No Gamma Exposure is calculated because dealer positioning and contract-specific Greeks are not supplied.",
        "This module does not download options data or execute orders.",
    ],
}
