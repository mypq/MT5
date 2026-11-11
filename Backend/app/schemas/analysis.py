from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

class AnalysisTimeframe(str, Enum):
M5 = "M5"
M15 = "M15"
M30 = "M30"
H1 = "H1"
H4 = "H4"
D1 = "D1"
W1 = "W1"

class AnalysisMode(str, Enum):
MANUAL = "MANUAL"
AUTOMATED_REVIEW = "AUTOMATED_REVIEW"

class AnalysisDecision(str, Enum):
BUY = "BUY"
SELL = "SELL"
WAIT = "WAIT"
NO_TRADE = "NO_TRADE"

class AnalysisRequest(BaseModel):
model_config = ConfigDict(extra="forbid")

symbol: Literal["XAUUSD"] = "XAUUSD"

primary_timeframe: AnalysisTimeframe = AnalysisTimeframe.H1

confirmation_timeframes: list[AnalysisTimeframe] = Field(
    default_factory=lambda: [
        AnalysisTimeframe.M15,
        AnalysisTimeframe.H4,
    ],
    max_length=6,
)

analysis_mode: AnalysisMode = AnalysisMode.MANUAL

requested_modules: list[str] = Field(
    default_factory=lambda: [
        "trend",
        "price_action",
        "indicators",
        "market_structure",
        "liquidity",
        "supply_demand",
        "volume",
        "economic",
        "news",
        "intermarket",
        "quantitative",
        "risk",
    ],
    min_length=1,
    max_length=12,
)

include_explanation: bool = True

@field_validator("requested_modules")
@classmethod
def validate_modules(cls, modules: list[str]) -> list[str]:
    allowed = {
        "trend",
        "price_action",
        "indicators",
        "market_structure",
        "liquidity",
        "supply_demand",
        "volume",
        "economic",
        "news",
        "intermarket",
        "quantitative",
        "risk",
    }

    normalized = [module.strip().lower() for module in modules]

    if len(normalized) != len(set(normalized)):
        raise ValueError("Duplicate analysis modules are not allowed")

    invalid = set(normalized) - allowed

    if invalid:
        raise ValueError(
            f"Unsupported analysis modules: {sorted(invalid)}"
        )

    return normalized

class AnalysisEvidence(BaseModel):
model_config = ConfigDict(extra="forbid")

module: str
finding: str = Field(min_length=1, max_length=2000)

direction: Literal[
    "BULLISH",
    "BEARISH",
    "NEUTRAL",
    "UNAVAILABLE",
]

data_available: bool
data_timestamp: datetime | None = None

class AnalysisSummary(BaseModel):
model_config = ConfigDict(extra="forbid")

symbol: Literal["XAUUSD"] = "XAUUSD"

decision: AnalysisDecision

confidence_score: float = Field(ge=0, le=100)

confidence_description: str = (
    "Analytical confidence score, not a probability of profit."
)

summary_ar: str = Field(min_length=1, max_length=4000)

supporting_evidence: list[str] = Field(default_factory=list)

opposing_evidence: list[str] = Field(default_factory=list)

risk_warnings: list[str] = Field(default_factory=list)

evidence: list[AnalysisEvidence] = Field(default_factory=list)

generated_at: datetime = Field(
    default_factory=lambda: datetime.now(timezone.utc)
)

data_is_fresh: bool = False

automated_execution_allowed: bool = False
