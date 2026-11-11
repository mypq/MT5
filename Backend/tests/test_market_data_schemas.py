from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.market_data import (
    AccountMode,
    AccountSnapshot,
    MarketCandle,
    MarketDataBatch,
    MarketTimeframe,
    OpenPosition,
    OpenPositionsSnapshot,
    PositionSide,
    VolumeSource,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def valid_candle(open_time: datetime) -> dict:
    return {
        "open_time": open_time,
        "open": 2650.0,
        "high": 2660.0,
        "low": 2640.0,
        "close": 2655.0,
        "tick_volume": 120,
        "real_volume": 0,
        "spread_points": 25,
        "is_closed": True,
    }


def valid_market_batch() -> dict:
    now = utc_now()

    return {
        "message_id": str(uuid4()),
        "source_id": "MT5-DEMO-01",
        "symbol": "XAUUSD",
        "timeframe": "M1",
        "sent_at": now,
        "volume_source": "TICK",
        "candles": [
            valid_candle(now - timedelta(minutes=2)),
            valid_candle(now - timedelta(minutes=1)),
        ],
    }


def valid_account_snapshot() -> dict:
    return {
        "message_id": str(uuid4()),
        "source_id": "MT5-DEMO-01",
        "account_mode": "DEMO",
        "account_currency": "USD",
        "balance": 10000.0,
        "equity": 9950.0,
        "daily_start_equity": 10000.0,
        "free_margin": 9000.0,
        "used_margin": 1000.0,
        "daily_realized_loss": 50.0,
        "positions_count": 1,
        "sent_at": utc_now(),
    }


def valid_position() -> dict:
    return {
        "position_id": "123456",
        "symbol": "XAUUSD",
        "side": "BUY",
        "volume": 0.01,
        "entry_price": 2650.0,
        "current_price": 2655.0,
        "stop_loss": 2640.0,
        "take_profit": 2670.0,
        "floating_profit": 5.0,
        "opened_at": utc_now() - timedelta(minutes=5),
    }


def test_valid_market_batch_is_accepted():
    batch = MarketDataBatch.model_validate(
        valid_market_batch()
    )

    assert batch.symbol == "XAUUSD"
    assert batch.timeframe == MarketTimeframe.M1
    assert len(batch.candles) == 2
    assert batch.source_id == "MT5-DEMO-01"


def test_market_batch_rejects_unknown_fields():
    payload = valid_market_batch()
    payload["unexpected"] = "not allowed"

    with pytest.raises(ValidationError):
        MarketDataBatch.model_validate(payload)


def test_market_batch_rejects_invalid_source_id():
    payload = valid_market_batch()
    payload["source_id"] = "INVALID SOURCE ID"

    with pytest.raises(ValidationError):
        MarketDataBatch.model_validate(payload)


def test_market_batch_rejects_unclosed_candle():
    payload = valid_market_batch()
    payload["candles"][0]["is_closed"] = False

    with pytest.raises(ValidationError):
        MarketDataBatch.model_validate(payload)


def test_market_batch_rejects_future_sent_at():
    payload = valid_market_batch()
    payload["sent_at"] = utc_now() + timedelta(minutes=5)

    with pytest.raises(ValidationError):
        MarketDataBatch.model_validate(payload)


def test_market_batch_rejects_duplicate_candle_times():
    payload = valid_market_batch()
    payload["candles"][1]["open_time"] = (
        payload["candles"][0]["open_time"]
    )

    with pytest.raises(
        ValidationError,
        match="Duplicate candle timestamps",
    ):
        MarketDataBatch.model_validate(payload)


def test_market_batch_rejects_unordered_candles():
    payload = valid_market_batch()
    payload["candles"].reverse()

    with pytest.raises(
        ValidationError,
        match="chronologically",
    ):
        MarketDataBatch.model_validate(payload)


def test_candle_rejects_invalid_ohlc():
    payload = valid_candle(utc_now())
    payload["high"] = 2645.0

    with pytest.raises(
        ValidationError,
        match="high cannot be below",
    ):
        MarketCandle.model_validate(payload)


def test_account_snapshot_accepts_demo_account():
    snapshot = AccountSnapshot.model_validate(
        valid_account_snapshot()
    )

    assert snapshot.account_mode == AccountMode.DEMO
    assert snapshot.account_currency == "USD"
    assert snapshot.equity == 9950.0


def test_account_snapshot_rejects_negative_equity():
    payload = valid_account_snapshot()
    payload["equity"] = -1.0

    with pytest.raises(ValidationError):
        AccountSnapshot.model_validate(payload)


def test_account_snapshot_normalizes_currency():
    payload = valid_account_snapshot()
    payload["account_currency"] = "usd"

    snapshot = AccountSnapshot.model_validate(payload)

    assert snapshot.account_currency == "USD"


def test_position_accepts_valid_protective_stop():
    position = OpenPosition.model_validate(
        valid_position()
    )

    assert position.side == PositionSide.BUY
    assert position.stop_loss == 2640.0


def test_position_rejects_zero_volume():
    payload = valid_position()
    payload["volume"] = 0

    with pytest.raises(ValidationError):
        OpenPosition.model_validate(payload)


def test_positions_snapshot_rejects_duplicate_ids():
    now = utc_now()

    payload = {
        "message_id": str(uuid4()),
        "source_id": "MT5-DEMO-01",
        "sent_at": now,
        "positions": [
            valid_position(),
            {
                **valid_position(),
                "opened_at": now - timedelta(minutes=3),
            },
        ],
    }

    with pytest.raises(
        ValidationError,
        match="Duplicate position IDs",
    ):
        OpenPositionsSnapshot.model_validate(payload)


def test_positions_snapshot_rejects_future_open_time():
    now = utc_now()

    position = valid_position()
    position["opened_at"] = now + timedelta(minutes=1)

    payload = {
        "message_id": str(uuid4()),
        "source_id": "MT5-DEMO-01",
        "sent_at": now,
        "positions": [position],
    }

    with pytest.raises(
        ValidationError,
        match="after snapshot sent_at",
    ):
        OpenPositionsSnapshot.model_validate(payload)


def test_market_batch_rejects_unsupported_symbol():
    payload = valid_market_batch()
    payload["symbol"] = "EURUSD"

    with pytest.raises(ValidationError):
        MarketDataBatch.model_validate(payload)


def test_market_batch_rejects_unknown_volume_source():
    payload = valid_market_batch()
    payload["volume_source"] = "BROKER_GUESS"

    with pytest.raises(ValidationError):
        MarketDataBatch.model_validate(payload)
