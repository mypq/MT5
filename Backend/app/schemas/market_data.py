from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID
import re

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class MarketTimeframe(str, Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"


class AccountMode(str, Enum):
    DEMO = "DEMO"
    REAL = "REAL"
    CONTEST = "CONTEST"
    UNKNOWN = "UNKNOWN"


class PositionSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class VolumeSource(str, Enum):
    TICK = "TICK"
    REAL = "REAL"
    UNKNOWN = "UNKNOWN"


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        validate_default=True,
    )


def require_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime must include a timezone")

    return value.astimezone(timezone.utc)


def reject_future_datetime(
    value: datetime,
    field_name: str,
) -> datetime:
    normalized = require_aware_datetime(value)

    if normalized > datetime.now(timezone.utc):
        raise ValueError(
            f"{field_name} cannot be in the future"
        )

    return normalized


def validate_source_identifier(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Source ID must be text")

    normalized = value.strip()

    if (
        not normalized
        or len(normalized) > 128
        or not normalized.isascii()
        or not re.fullmatch(r"[A-Za-z0-9_.:-]+", normalized)
    ):
        raise ValueError("Invalid source identifier")

    return normalized


class MarketCandle(StrictModel):
    open_time: datetime

    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)

    tick_volume: int = Field(ge=0)
    real_volume: int = Field(ge=0)
    spread_points: int = Field(ge=0)

    is_closed: Literal[True]

    @field_validator("open_time")
    @classmethod
    def validate_open_time(
        cls,
        value: datetime,
    ) -> datetime:
        return require_aware_datetime(value)

    @model_validator(mode="after")
    def validate_ohlc(self):
        if self.high < max(self.open, self.close):
            raise ValueError(
                "Candle high cannot be below open or close"
            )

        if self.low > min(self.open, self.close):
            raise ValueError(
                "Candle low cannot be above open or close"
            )

        if self.high < self.low:
            raise ValueError(
                "Candle high cannot be below candle low"
            )

        return self


class MarketDataBatch(StrictModel):
    message_id: UUID

    source_id: str = Field(
        min_length=1,
        max_length=128,
    )

    symbol: Literal["XAUUSD"] = "XAUUSD"
    timeframe: MarketTimeframe
    sent_at: datetime

    volume_source: VolumeSource = VolumeSource.UNKNOWN

    candles: list[MarketCandle] = Field(
        min_length=2,
        max_length=2000,
    )

    @field_validator("sent_at")
    @classmethod
    def validate_sent_at(
        cls,
        value: datetime,
    ) -> datetime:
        return reject_future_datetime(value, "sent_at")

    @field_validator("source_id")
    @classmethod
    def validate_source_id(
        cls,
        value: str,
    ) -> str:
        return validate_source_identifier(value)

    @model_validator(mode="after")
    def validate_candle_sequence(self):
        timestamps = [
            candle.open_time
            for candle in self.candles
        ]

        if timestamps != sorted(timestamps):
            raise ValueError(
                "Candles must be ordered chronologically"
            )

        if len(timestamps) != len(set(timestamps)):
            raise ValueError(
                "Duplicate candle timestamps are not allowed"
            )

        if timestamps[-1] > self.sent_at:
            raise ValueError(
                "Latest candle cannot start after sent_at"
            )

        return self


class AccountSnapshot(StrictModel):
    message_id: UUID

    source_id: str = Field(
        min_length=1,
        max_length=128,
    )

    account_mode: AccountMode

    account_currency: str = Field(
        min_length=3,
        max_length=12,
        pattern=r"^[A-Z]{3,12}$",
    )

    balance: float = Field(ge=0)
    equity: float = Field(ge=0)

    daily_start_equity: float = Field(gt=0)

    free_margin: float = Field(ge=0)
    used_margin: float = Field(ge=0)

    daily_realized_loss: float = Field(ge=0)

    positions_count: int = Field(ge=0, le=1000)

    sent_at: datetime

    @field_validator("sent_at")
    @classmethod
    def validate_sent_at(
        cls,
        value: datetime,
    ) -> datetime:
        return reject_future_datetime(value, "sent_at")

    @field_validator("source_id")
    @classmethod
    def validate_source_id(
        cls,
        value: str,
    ) -> str:
        return validate_source_identifier(value)

    @field_validator("account_currency", mode="before")
    @classmethod
    def normalize_currency(
        cls,
        value: str,
    ) -> str:
        if not isinstance(value, str):
            raise ValueError(
                "Account currency must be text"
            )

        normalized = value.strip().upper()

        if (
            not normalized.isascii()
            or not normalized.isalpha()
            or not 3 <= len(normalized) <= 12
        ):
            raise ValueError(
                "Invalid account currency"
            )

        return normalized


class OpenPosition(StrictModel):
    position_id: str = Field(
        min_length=1,
        max_length=128,
    )

    symbol: Literal["XAUUSD"] = "XAUUSD"

    side: PositionSide

    volume: float = Field(gt=0)

    entry_price: float = Field(gt=0)
    current_price: float = Field(gt=0)

    stop_loss: float | None = Field(
        default=None,
        gt=0,
    )

    take_profit: float | None = Field(
        default=None,
        gt=0,
    )

    floating_profit: float

    opened_at: datetime

    @field_validator("opened_at")
    @classmethod
    def validate_opened_at(
        cls,
        value: datetime,
    ) -> datetime:
        return require_aware_datetime(value)

    @field_validator("position_id")
    @classmethod
    def validate_position_id(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "Position ID cannot be empty"
            )

        return normalized


class OpenPositionsSnapshot(StrictModel):
    message_id: UUID

    source_id: str = Field(
        min_length=1,
        max_length=128,
    )

    sent_at: datetime

    positions: list[OpenPosition] = Field(
        max_length=1000,
    )

    @field_validator("sent_at")
    @classmethod
    def validate_sent_at(
        cls,
        value: datetime,
    ) -> datetime:
        return reject_future_datetime(value, "sent_at")

    @field_validator("source_id")
    @classmethod
    def validate_source_id(
        cls,
        value: str,
    ) -> str:
        return validate_source_identifier(value)

    @model_validator(mode="after")
    def validate_positions(self):
        position_ids = [
            position.position_id
            for position in self.positions
        ]

        if len(position_ids) != len(set(position_ids)):
            raise ValueError(
                "Duplicate position IDs are not allowed"
            )

        for position in self.positions:
            if position.opened_at > self.sent_at:
                raise ValueError(
                    "Position opened_at cannot be after snapshot sent_at"
                )

        return self
