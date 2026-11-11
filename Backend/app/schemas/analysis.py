from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ALLOWED_ANALYSIS_MODULES = frozenset({
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
    "market_regime",
    "volume_profile",
    "order_flow",
    "cot",
    "options",
    "session",
    "anomaly_detection",
    "strategy_research",
    "execution_intelligence",
    "market_data_intelligence",
    "multi_timeframe_intelligence",
    "decision_fusion",
    "portfolio_exposure",
    "macro_event_intelligence",
    "realistic_backtesting",
    "signal_lifecycle",
    "trading_audit",
    "correlation_dollar",
    "liquidity_sweep",
    "market_microstructure",
    "adaptive_model_validation",
})


class AnalysisTimeframe(str, Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"


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
        min_length=1,
        max_length=8,
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
            "market_regime",
            "volume_profile",
            "order_flow",
            "cot",
            "options",
            "session",
            "anomaly_detection",
            "strategy_research",
            "execution_intelligence",
            "market_data_intelligence",
            "multi_timeframe_intelligence",
            "decision_fusion",
            "portfolio_exposure",
            "macro_event_intelligence",
            "realistic_backtesting",
            "signal_lifecycle",
            "trading_audit",
            "correlation_dollar",
            "liquidity_sweep",
            "market_microstructure",
            "adaptive_model_validation",
        ],
        min_length=1,
        max_length=33,
    )

    include_explanation: bool = True

    @field_validator("requested_modules")
    @classmethod
    def validate_modules(cls, modules: list[str]) -> list[str]:
        normalized = []

        for module in modules:
            if not isinstance(module, str):
                raise ValueError("Module names must be strings")

            name = module.strip().lower()

            if not name:
                raise ValueError("Module names cannot be empty")

            normalized.append(name)

        if len(normalized) != len(set(normalized)):
            raise ValueError(
                "Duplicate analysis modules are not allowed"
            )

        invalid = set(normalized) - ALLOWED_ANALYSIS_MODULES

        if invalid:
            raise ValueError(
                f"Unsupported analysis modules: {sorted(invalid)}"
            )

        return normalized


class AnalysisEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: str = Field(min_length=1, max_length=100)

    finding: str = Field(
        min_length=1,
        max_length=2000,
    )

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

    confidence_score: float = Field(
        ge=0,
        le=100,
    )

    confidence_description: str = (
        "Analytical confidence score, not a probability of profit."
    )

    summary_ar: str = Field(
        min_length=1,
        max_length=4000,
    )

    supporting_evidence: list[str] = Field(
        default_factory=list,
        max_length=100,
    )

    opposing_evidence: list[str] = Field(
        default_factory=list,
        max_length=100,
    )

    risk_warnings: list[str] = Field(
        default_factory=list,
        max_length=100,
    )

    evidence: list[AnalysisEvidence] = Field(
        default_factory=list,
        max_length=100,
    )

    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    data_is_fresh: bool = False

    automated_execution_allowed: bool = False
