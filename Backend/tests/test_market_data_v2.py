from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.market_data import (
    MarketCandleV2,
    MarketDataBatchV2,
)


def valid_candle(broker_time: str) -> dict:
    return {
        "broker_local_time": broker_time,
        "open": 2650.0,
        "high": 2660.0,
        "low": 2640.0,
        "close": 2655.0,
        "tick_volume": 120,
        "real_volume": 0,
        "spread_points": 25,
        "is_closed": True,
    }


def valid_batch() -> dict:
    return {
        "schema_version": 2,
        "message_id": str(uuid4()),
        "source_id": "MT5-DEMO-01",
        "symbol": "XAUUSD",
        "timeframe": "M1",
        "sent_at": datetime.now(timezone.utc),
        "volume_source": "TICK",
        "broker_time": {
            "time_source": "MT5_SERVER_TIME",
            "broker_name": "Demo Broker",
            "server_name": "Demo Server",
            "timezone_name": "Europe/Athens",
            "reported_utc_offset_minutes": 120,
        },
        "candles": [
            valid_candle("2026-11-12T08:00:00"),
            valid_candle("2026-11-12T08:01:00"),
        ],
    }


def test_valid_v2_batch_is_accepted():
    batch = MarketDataBatchV2.model_validate(valid_batch())

    assert batch.schema_version == 2
    assert batch.symbol == "XAUUSD"
    assert batch.source_id == "MT5-DEMO-01"
    assert len(batch.candles) == 2
    assert batch.candles[0].broker_local_time.tzinfo is None


@pytest.mark.parametrize(
    "invalid_time",
    [
        "2026-11-12T08:00:00Z",
        "2026-11-12T08:00:00+03:00",
        "2026-11-12T08:00",
        "2026-11-12 08:00:00",
        "2026-11-12T08:00:00.123",
        "not-a-date",
    ],
)
def test_v2_rejects_noncanonical_broker_time(invalid_time):
    payload = valid_batch()
    payload["candles"][0]["broker_local_time"] = invalid_time

    with pytest.raises(ValidationError):
        MarketDataBatchV2.model_validate(payload)


def test_v2_rejects_invalid_calendar_date():
    payload = valid_batch()
    payload["candles"][0]["broker_local_time"] = (
        "2026-02-30T08:00:00"
    )

    with pytest.raises(ValidationError):
        MarketDataBatchV2.model_validate(payload)


def test_v2_accepts_duplicate_broker_times():
    payload = valid_batch()
    payload["candles"][1]["broker_local_time"] = (
        payload["candles"][0]["broker_local_time"]
    )

    batch = MarketDataBatchV2.model_validate(payload)

    assert len(batch.candles) == 2
    assert (
        batch.candles[0].broker_local_time
        == batch.candles[1].broker_local_time
    )


def test_v2_accepts_unordered_broker_times():
    payload = valid_batch()
    payload["candles"].reverse()

    batch = MarketDataBatchV2.model_validate(payload)

    assert len(batch.candles) == 2
    assert (
        batch.candles[0].broker_local_time
        > batch.candles[1].broker_local_time
    )


def test_v2_rejects_invalid_ohlc():
    candle = valid_candle("2026-11-12T08:00:00")
    candle["high"] = 2645.0

    with pytest.raises(
        ValidationError,
        match="high cannot be below",
    ):
        MarketCandleV2.model_validate(candle)


def test_v2_rejects_unclosed_candle():
    payload = valid_batch()
    payload["candles"][0]["is_closed"] = False

    with pytest.raises(ValidationError):
        MarketDataBatchV2.model_validate(payload)


def test_v2_rejects_invalid_timezone_name():
    payload = valid_batch()
    payload["broker_time"]["timezone_name"] = "../invalid"

    with pytest.raises(ValidationError):
        MarketDataBatchV2.model_validate(payload)


def test_v2_rejects_out_of_range_reported_offset():
    payload = valid_batch()
    payload["broker_time"]["reported_utc_offset_minutes"] = 900

    with pytest.raises(ValidationError):
        MarketDataBatchV2.model_validate(payload)


def test_v2_does_not_compare_broker_time_with_utc_sent_at():
    payload = valid_batch()
    payload["candles"] = [
        valid_candle("2099-01-01T08:00:00"),
        valid_candle("2099-01-01T08:01:00"),
    ]

    batch = MarketDataBatchV2.model_validate(payload)

    assert batch.candles[-1].broker_local_time.year == 2099


def test_v2_rejects_unknown_fields():
    payload = valid_batch()
    payload["unexpected"] = "not allowed"

    with pytest.raises(ValidationError):
        MarketDataBatchV2.model_validate(payload)


def test_v2_rejects_invalid_source_id():
    payload = valid_batch()
    payload["source_id"] = "INVALID SOURCE"

    with pytest.raises(ValidationError):
        MarketDataBatchV2.model_validate(payload)


def test_v2_rejects_future_sent_at():
    payload = valid_batch()
    payload["sent_at"] = datetime.now(timezone.utc).replace(
        year=2099
    )

    with pytest.raises(ValidationError):
        MarketDataBatchV2.model_validate(payload)
