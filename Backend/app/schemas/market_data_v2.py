
import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.market_data import (
    MarketTimeframe,
    StrictModel,
    VolumeSource,
    reject_future_datetime,
    validate_source_identifier,
)


class BrokerTimeMetadataV2(StrictModel):
    """Broker clock metadata; it does not claim that broker time is UTC."""

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

    # Informational only. Never use this alone for historical conversion.
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
    # Exact broker wall-clock time, deliberately without timezone.
    broker_open_time: datetime

    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)

    tick_volume: int = Field(ge=0)
    real_volume: int = Field(ge=0)
    spread_points: int = Field(ge=0)

    is_closed: Literal[True]

    @field_validator(
        "broker_open_time",
        mode="before",
    )
    @classmethod
    def require_exact_broker_time_format(cls, value):
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
            value,
        ):
            raise ValueError(
                "broker_open_time must use "
                "YYYY-MM-DDTHH:MM:SS "
                "without a timezone or UTC suffix"
            )

        return value

    @field_validator("broker_open_time")
    @classmethod
    def validate_broker_open_time(cls, value):
        if value.tzinfo is not None and value.utcoffset() is not None:
            raise ValueError(
                "broker_open_time must be timezone-naive"
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

    broker_time: BrokerTimeMetadataV2

    candles: list[MarketCandleV2] = Field(
        min_length=2,
        max_length=2000,
    )

    @field_validator("sent_at")
    @classmethod
    def validate_sent_at(cls, value):
        return reject_future_datetime(
            value,
            "sent_at",
        )

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value):
        return validate_source_identifier(value)

    @model_validator(mode="after")
    def validate_candle_sequence(self):
        timestamps = [
            candle.broker_open_time
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

        # Never compare broker wall-clock time with UTC sent_at.
        # Conversion requires a separately verified timezone mapping.
        return self
