
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


class MeasurementStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNVERIFIED = "UNVERIFIED"


class PositionTimeStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"


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

    daily_start_equity: float | None = Field(
        default=None,
        gt=0,
    )

    daily_start_equity_status: MeasurementStatus = (
        MeasurementStatus.UNAVAILABLE
    )

    free_margin: float = Field(ge=0)
    used_margin: float = Field(ge=0)

    daily_realized_loss: float | None = Field(
        default=None,
        ge=0,
    )

    daily_realized_loss_status: MeasurementStatus = (
        MeasurementStatus.UNAVAILABLE
    )

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

    @model_validator(mode="after")
    def validate_daily_metrics(self):
        metrics = (
            (
                "daily_start_equity",
                self.daily_start_equity,
                self.daily_start_equity_status,
            ),
            (
                "daily_realized_loss",
                self.daily_realized_loss,
                self.daily_realized_loss_status,
            ),
        )

        for name, value, status in metrics:
            if status == MeasurementStatus.AVAILABLE:
                if value is None:
                    raise ValueError(
                        f"{name} requires a value when AVAILABLE"
                    )

            elif status == MeasurementStatus.UNAVAILABLE:
                if value is not None:
                    raise ValueError(
                        f"{name} must be null when UNAVAILABLE"
                    )

        return self


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

    opened_at_broker_local: datetime

    opened_at_utc: datetime | None = None

    time_resolution_status: PositionTimeStatus = (
        PositionTimeStatus.UNVERIFIED
    )

    @field_validator(
        "opened_at_broker_local",
        mode="before",
    )
    @classmethod
    def require_broker_local_time_format(cls, value):
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
            value,
        ):
            raise ValueError(
                "opened_at_broker_local must use "
                "YYYY-MM-DDTHH:MM:SS without timezone"
            )

        return value

    @field_validator("opened_at_broker_local")
    @classmethod
    def validate_broker_local_open_time(
        cls,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is not None and value.utcoffset() is not None:
            raise ValueError(
                "opened_at_broker_local must be timezone-naive"
            )

        return value.replace(tzinfo=None)

    @field_validator("opened_at_utc")
    @classmethod
    def validate_opened_at_utc(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None

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

    @model_validator(mode="after")
    def validate_position_time_resolution(self):
        if self.time_resolution_status == PositionTimeStatus.VERIFIED:
            if self.opened_at_utc is None:
                raise ValueError(
                    "VERIFIED position time requires opened_at_utc"
                )

        elif self.opened_at_utc is not None:
            raise ValueError(
                "opened_at_utc is only allowed for VERIFIED position time"
            )

        return self


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
            if (
                position.opened_at_utc is not None
                and position.opened_at_utc > self.sent_at
            ):
                raise ValueError(
                    "Position opened_at_utc cannot be after snapshot sent_at"
                )

        return self


class BrokerTimeMetadataV2(StrictModel):
    """Broker clock metadata; it never proves historical UTC mapping."""

    time_source: Literal["MT5_SERVER_TIME"] = "MT5_SERVER_TIME"

    broker_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )

    server_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )

    timezone_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )

    reported_utc_offset_minutes: int | None = Field(
        default=None,
        ge=-720,
        le=840,
    )

    @field_validator(
        "broker_name",
        "server_name",
        "timezone_name",
    )
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return None

        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "Broker time metadata cannot be blank"
            )

        return normalized

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone_name(cls, value):
        if value is None:
            return None

        if (
            value.startswith("/")
            or ".." in value
            or not re.fullmatch(
                r"[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+)*",
                value,
            )
        ):
            raise ValueError(
                "Invalid timezone identifier format"
            )

        return value


class MarketCandleV2(StrictModel):
    broker_local_time: datetime

    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)

    tick_volume: int = Field(ge=0)
    real_volume: int = Field(ge=0)
    spread_points: int = Field(ge=0)

    is_closed: Literal[True]

    @field_validator(
        "broker_local_time",
        mode="before",
    )
    @classmethod
    def require_exact_broker_time_format(cls, value):
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
            value,
        ):
            raise ValueError(
                "broker_local_time must use "
                "YYYY-MM-DDTHH:MM:SS "
                "without timezone or UTC suffix"
            )

        return value

    @field_validator("broker_local_time")
    @classmethod
    def validate_broker_local_time(cls, value):
        if value.tzinfo is not None and value.utcoffset() is not None:
            raise ValueError(
                "broker_local_time must be timezone-naive"
            )

        return value.replace(tzinfo=None)

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


class MarketDataBatchV2(StrictModel):
    schema_version: Literal[2] = 2

    message_id: UUID

    source_id: str = Field(
        min_length=1,
        max_length=128,
    )

    symbol: Literal["XAUUSD"] = "XAUUSD"
    timeframe: MarketTimeframe
    sent_at: datetime

    volume_source: VolumeSource = VolumeSource.UNKNOWN

    broker_time: BrokerTimeMetadataV2 | None = None

    candles: list[MarketCandleV2] = Field(
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
            candle.broker_local_time
            for candle in self.candles
        ]

        if timestamps != sorted(timestamps):
            raise ValueError(
                "Broker candle times must be ordered chronologically"
            )

        if len(timestamps) != len(set(timestamps)):
            raise ValueError(
                "Duplicate broker candle times are not allowed"
            )

        # Broker wall-clock time is never compared with UTC sent_at.
        # Historical UTC conversion requires verified time history.
        return self
