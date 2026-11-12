
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from app.api import market_data
from app.core.market_data_auth import (
    MarketDataAuthenticationError,
)
from app.core.market_data_store import (
    MarketDataMessageConflictError,
    MarketDataStoreError,
)
from app.core.market_time_store import MarketTimeStoreError


TEST_SOURCE_ID = "MT5-DEMO-01"


class FakeDeviceRegistry:
    pass


class FakeNonceStore:
    pass


class FakeMarketDataStore:
    def __init__(self, result=True, error=None):
        self.result = result
        self.error = error
        self.saved_messages = []

    async def store_message(self, **kwargs):
        self.saved_messages.append(kwargs)

        if self.error is not None:
            raise self.error

        return self.result


class FakeMarketTimeStore:
    def __init__(self, results=None, error=None):
        self.results = list(results or [])
        self.error = error
        self.calls = []

    async def resolve_broker_local_time(
        self,
        *,
        source_id,
        broker_local_time,
    ):
        self.calls.append((source_id, broker_local_time))

        if self.error is not None:
            raise self.error

        if not self.results:
            return None

        return self.results.pop(0)


def build_request(
    *,
    device_registry=None,
    nonce_store=None,
    market_data_store=None,
    market_time_store=None,
):
    app = FastAPI()

    app.state.device_registry = (
        device_registry or FakeDeviceRegistry()
    )
    app.state.nonce_store = (
        nonce_store or FakeNonceStore()
    )
    app.state.market_data_store = (
        market_data_store or FakeMarketDataStore()
    )
    app.state.market_time_store = (
        market_time_store or FakeMarketTimeStore()
    )

    scope = {
        "type": "http",
        "asgi": {
            "version": "3.0",
            "spec_version": "2.3",
        },
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/v1/market-data/candles",
        "raw_path": b"/api/v1/market-data/candles",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
        "app": app,
    }

    async def receive():
        return {
            "type": "http.request",
            "body": b"",
            "more_body": False,
        }

    return Request(scope, receive)


def build_valid_candles_payload():
    now = datetime.now(timezone.utc)

    first_candle_time = now - timedelta(minutes=2)
    second_candle_time = now - timedelta(minutes=1)

    return {
        "message_id": str(uuid4()),
        "source_id": TEST_SOURCE_ID,
        "symbol": "XAUUSD",
        "timeframe": "M1",
        "sent_at": now.isoformat(),
        "volume_source": "TICK",
        "candles": [
            {
                "open_time": first_candle_time.isoformat(),
                "open": 2650.0,
                "high": 2652.0,
                "low": 2649.0,
                "close": 2651.0,
                "tick_volume": 100,
                "real_volume": 0,
                "spread_points": 20,
                "is_closed": True,
            },
            {
                "open_time": second_candle_time.isoformat(),
                "open": 2651.0,
                "high": 2653.0,
                "low": 2650.0,
                "close": 2652.0,
                "tick_volume": 120,
                "real_volume": 0,
                "spread_points": 20,
                "is_closed": True,
            },
        ],
    }


def build_valid_candles_v2_payload():
    now = datetime.now(timezone.utc)

    return {
        "schema_version": 2,
        "message_id": str(uuid4()),
        "source_id": TEST_SOURCE_ID,
        "symbol": "XAUUSD",
        "timeframe": "M1",
        "sent_at": now.isoformat(),
        "volume_source": "TICK",
        "candles": [
            {
                "broker_local_time": "2026-11-12T09:00:00",
                "open": 2650.0,
                "high": 2652.0,
                "low": 2649.0,
                "close": 2651.0,
                "tick_volume": 100,
                "real_volume": 0,
                "spread_points": 20,
                "is_closed": True,
            },
            {
                "broker_local_time": "2026-11-12T09:01:00",
                "open": 2651.0,
                "high": 2653.0,
                "low": 2650.0,
                "close": 2652.0,
                "tick_volume": 120,
                "real_volume": 0,
                "spread_points": 20,
                "is_closed": True,
            },
        ],
    }


def patch_runtime_types(monkeypatch):
    monkeypatch.setattr(
        market_data,
        "DeviceRegistry",
        FakeDeviceRegistry,
    )
    monkeypatch.setattr(
        market_data,
        "PostgresNonceStore",
        FakeNonceStore,
    )
    monkeypatch.setattr(
        market_data,
        "MarketDataStore",
        FakeMarketDataStore,
    )
    monkeypatch.setattr(
        market_data,
        "MarketTimeStore",
        FakeMarketTimeStore,
    )


@pytest.mark.asyncio
async def test_valid_candles_message_is_stored(monkeypatch):
    patch_runtime_types(monkeypatch)

    payload = build_valid_candles_payload()
    raw_body = json.dumps(payload).encode("utf-8")

    store = FakeMarketDataStore(result=True)

    request = build_request(
        market_data_store=store,
    )

    async def fake_authentication(**kwargs):
        return raw_body

    monkeypatch.setattr(
        market_data,
        "authenticate_market_data_request",
        fake_authentication,
    )

    result = await market_data._ingest_message(
        request=request,
        message_type="candles",
        model_type=market_data.MarketDataBatch,
    )

    assert result["status"] == "stored"
    assert result["message_id"] == payload["message_id"]
    assert result["message_type"] == "candles"
    assert result["source_id"] == TEST_SOURCE_ID
    assert len(store.saved_messages) == 1

    saved = store.saved_messages[0]

    assert saved["raw_body"] == raw_body
    assert saved["source_id"] == TEST_SOURCE_ID
    assert saved["message_type"] == "candles"


@pytest.mark.asyncio
async def test_duplicate_message_returns_duplicate_status(
    monkeypatch,
):
    patch_runtime_types(monkeypatch)

    payload = build_valid_candles_payload()
    raw_body = json.dumps(payload).encode("utf-8")

    store = FakeMarketDataStore(result=False)

    request = build_request(
        market_data_store=store,
    )

    async def fake_authentication(**kwargs):
        return raw_body

    monkeypatch.setattr(
        market_data,
        "authenticate_market_data_request",
        fake_authentication,
    )

    result = await market_data._ingest_message(
        request=request,
        message_type="candles",
        model_type=market_data.MarketDataBatch,
    )

    assert result["status"] == "duplicate"
    assert len(store.saved_messages) == 1


@pytest.mark.asyncio
async def test_unavailable_runtime_dependencies_return_503(
    monkeypatch,
):
    patch_runtime_types(monkeypatch)

    request = build_request(
        device_registry=object(),
    )

    with pytest.raises(HTTPException) as error:
        await market_data._ingest_message(
            request=request,
            message_type="candles",
            model_type=market_data.MarketDataBatch,
        )

    assert error.value.status_code == 503
    assert (
        error.value.detail
        == "Market-data ingestion is unavailable"
    )


@pytest.mark.asyncio
async def test_authentication_failure_returns_401(
    monkeypatch,
):
    patch_runtime_types(monkeypatch)

    request = build_request()

    async def failed_authentication(**kwargs):
        raise MarketDataAuthenticationError(
            "Internal authentication detail"
        )

    monkeypatch.setattr(
        market_data,
        "authenticate_market_data_request",
        failed_authentication,
    )

    with pytest.raises(HTTPException) as error:
        await market_data._ingest_message(
            request=request,
            message_type="candles",
            model_type=market_data.MarketDataBatch,
        )

    assert error.value.status_code == 401
    assert (
        error.value.detail
        == "Market-data authentication failed"
    )


@pytest.mark.asyncio
async def test_invalid_payload_returns_422(
    monkeypatch,
):
    patch_runtime_types(monkeypatch)

    invalid_payload = {
        "message_id": str(uuid4()),
        "source_id": TEST_SOURCE_ID,
        "symbol": "BTCUSD",
        "timeframe": "M1",
        "sent_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "candles": [],
    }

    raw_body = json.dumps(
        invalid_payload
    ).encode("utf-8")

    request = build_request()

    async def fake_authentication(**kwargs):
        return raw_body

    monkeypatch.setattr(
        market_data,
        "authenticate_market_data_request",
        fake_authentication,
    )

    with pytest.raises(HTTPException) as error:
        await market_data._ingest_message(
            request=request,
            message_type="candles",
            model_type=market_data.MarketDataBatch,
        )

    assert error.value.status_code == 422
    assert (
        error.value.detail["error"]
        == "INVALID_MARKET_DATA"
    )


@pytest.mark.asyncio
async def test_message_conflict_returns_409(
    monkeypatch,
):
    patch_runtime_types(monkeypatch)

    payload = build_valid_candles_payload()
    raw_body = json.dumps(payload).encode("utf-8")

    store = FakeMarketDataStore(
        error=MarketDataMessageConflictError(
            "Message conflict"
        )
    )

    request = build_request(
        market_data_store=store,
    )

    async def fake_authentication(**kwargs):
        return raw_body

    monkeypatch.setattr(
        market_data,
        "authenticate_market_data_request",
        fake_authentication,
    )

    with pytest.raises(HTTPException) as error:
        await market_data._ingest_message(
            request=request,
            message_type="candles",
            model_type=market_data.MarketDataBatch,
        )

    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_storage_failure_returns_503(
    monkeypatch,
):
    patch_runtime_types(monkeypatch)

    payload = build_valid_candles_payload()
    raw_body = json.dumps(payload).encode("utf-8")

    store = FakeMarketDataStore(
        error=MarketDataStoreError(
            "Database failure"
        )
    )

    request = build_request(
        market_data_store=store,
    )

    async def fake_authentication(**kwargs):
        return raw_body

    monkeypatch.setattr(
        market_data,
        "authenticate_market_data_request",
        fake_authentication,
    )

    with pytest.raises(HTTPException) as error:
        await market_data._ingest_message(
            request=request,
            message_type="candles",
            model_type=market_data.MarketDataBatch,
        )

    assert error.value.status_code == 503
    assert (
        error.value.detail
        == "Market-data storage is unavailable"
    )


@pytest.mark.asyncio
async def test_v2_verified_candles_are_stored_with_utc_times(
    monkeypatch,
):
    patch_runtime_types(monkeypatch)

    payload = build_valid_candles_v2_payload()
    raw_body = json.dumps(payload).encode("utf-8")

    first_utc = datetime(
        2026, 11, 12, 7, 0, tzinfo=timezone.utc
    )
    second_utc = datetime(
        2026, 11, 12, 7, 1, tzinfo=timezone.utc
    )

    time_store = FakeMarketTimeStore(
        results=[first_utc, second_utc]
    )
    data_store = FakeMarketDataStore()

    request = build_request(
        market_data_store=data_store,
        market_time_store=time_store,
    )

    async def fake_authentication(**kwargs):
        return raw_body

    monkeypatch.setattr(
        market_data,
        "authenticate_market_data_request",
        fake_authentication,
    )

    result = await market_data.ingest_candles_v2(request)

    assert result["status"] == "stored"
    assert result["message_type"] == "candles"
    assert len(data_store.saved_messages) == 1

    saved = data_store.saved_messages[0]
    saved_payload = saved["payload"]

    assert saved["raw_body"] == raw_body
    assert saved_payload["time_resolution_status"] == "VERIFIED"

    assert (
        saved_payload["candles"][0]["open_time_utc"]
        == first_utc.isoformat()
    )

    assert (
        saved_payload["candles"][1]["open_time_utc"]
        == second_utc.isoformat()
    )

    assert len(time_store.calls) == 2


@pytest.mark.asyncio
async def test_v2_unverified_candle_rejects_entire_batch(
    monkeypatch,
):
    patch_runtime_types(monkeypatch)

    payload = build_valid_candles_v2_payload()
    raw_body = json.dumps(payload).encode("utf-8")

    time_store = FakeMarketTimeStore(
        results=[
            datetime(
                2026, 11, 12, 7, 0, tzinfo=timezone.utc
            ),
            None,
        ]
    )

    data_store = FakeMarketDataStore()

    request = build_request(
        market_data_store=data_store,
        market_time_store=time_store,
    )

    async def fake_authentication(**kwargs):
        return raw_body

    monkeypatch.setattr(
        market_data,
        "authenticate_market_data_request",
        fake_authentication,
    )

    with pytest.raises(HTTPException) as error:
        await market_data.ingest_candles_v2(request)

    assert error.value.status_code == 422
    assert (
        error.value.detail["error"]
        == "BROKER_TIME_UNVERIFIED"
    )
    assert error.value.detail["unresolved_candle_indices"] == [1]
    assert data_store.saved_messages == []


@pytest.mark.asyncio
async def test_v2_missing_time_store_returns_503(
    monkeypatch,
):
    patch_runtime_types(monkeypatch)

    request = build_request(
        market_time_store=object(),
    )

    with pytest.raises(HTTPException) as error:
        await market_data.ingest_candles_v2(request)

    assert error.value.status_code == 503
    assert error.value.detail == (
        "Market-data ingestion is unavailable"
    )


@pytest.mark.asyncio
async def test_v2_time_resolution_failure_returns_503(
    monkeypatch,
):
    patch_runtime_types(monkeypatch)

    payload = build_valid_candles_v2_payload()
    raw_body = json.dumps(payload).encode("utf-8")

    time_store = FakeMarketTimeStore(
        error=MarketTimeStoreError("Database failure")
    )

    data_store = FakeMarketDataStore()

    request = build_request(
        market_data_store=data_store,
        market_time_store=time_store,
    )

    async def fake_authentication(**kwargs):
        return raw_body

    monkeypatch.setattr(
        market_data,
        "authenticate_market_data_request",
        fake_authentication,
    )

    with pytest.raises(HTTPException) as error:
        await market_data.ingest_candles_v2(request)

    assert error.value.status_code == 503
    assert (
        error.value.detail
        == "Broker-time resolution is unavailable"
    )
    assert data_store.saved_messages == []

def test_v2_payload_matches_mt5_connector_contract():
    payload = build_valid_candles_v2_payload()

    message = market_data.MarketDataBatchV2.model_validate(
        payload
    )

    assert message.schema_version == 2
    assert message.source_id == TEST_SOURCE_ID
    assert message.symbol == "XAUUSD"
    assert message.timeframe.value == "M1"
    assert message.volume_source.value == "TICK"

    for candle in message.candles:
        assert candle.broker_local_time.tzinfo is None
        assert candle.is_closed is True
        assert candle.open > 0
        assert candle.high >= max(candle.open, candle.close)
        assert candle.low <= min(candle.open, candle.close)
        assert candle.tick_volume >= 0
        assert candle.real_volume >= 0
        assert candle.spread_points >= 0


def test_v2_rejects_timezone_in_broker_local_time():
    payload = build_valid_candles_v2_payload()
    payload["candles"][0]["broker_local_time"] = (
        "2026-11-12T09:00:00Z"
    )

    with pytest.raises(ValueError):
        market_data.MarketDataBatchV2.model_validate(
            payload
        )


def test_v2_rejects_legacy_open_time_field():
    payload = build_valid_candles_v2_payload()
    payload["candles"][0]["open_time"] = (
        "2026-11-12T07:00:00Z"
    )

    with pytest.raises(ValueError):
        market_data.MarketDataBatchV2.model_validate(
            payload
        )


def test_v2_rejects_duplicate_broker_local_times():
    payload = build_valid_candles_v2_payload()
    payload["candles"][1]["broker_local_time"] = (
        payload["candles"][0]["broker_local_time"]
    )

    with pytest.raises(ValueError):
        market_data.MarketDataBatchV2.model_validate(
            payload
        )


def test_v2_rejects_non_chronological_candles():
    payload = build_valid_candles_v2_payload()
    payload["candles"].reverse()

    with pytest.raises(ValueError):
        market_data.MarketDataBatchV2.model_validate(
            payload
        )


def test_v2_rejects_unsupported_extra_candle_fields():
    payload = build_valid_candles_v2_payload()
    payload["candles"][0]["utc_offset_minutes"] = 180

    with pytest.raises(ValueError):
        market_data.MarketDataBatchV2.model_validate(
            payload
        )
@pytest.mark.asyncio
async def test_account_snapshot_is_received_and_stored(monkeypatch):
    patch_runtime_types(monkeypatch)

    sent_at = datetime.now(timezone.utc)

    payload = {
        "message_id": str(uuid4()),
        "source_id": TEST_SOURCE_ID,
        "account_mode": "DEMO",
        "account_currency": "USD",
        "balance": 10000.0,
        "equity": 9950.0,
        "daily_start_equity": 10000.0,
        "free_margin": 9000.0,
        "used_margin": 1000.0,
        "daily_realized_loss": 50.0,
        "positions_count": 1,
        "sent_at": sent_at.isoformat(),
    }

    raw_body = json.dumps(payload).encode("utf-8")
    store = FakeMarketDataStore(result=True)

    request = build_request(
        market_data_store=store,
    )

    async def fake_authentication(**kwargs):
        return raw_body

    monkeypatch.setattr(
        market_data,
        "authenticate_market_data_request",
        fake_authentication,
    )

    result = await market_data.ingest_account(request)

    assert result["status"] == "stored"
    assert result["message_type"] == "account"
    assert result["source_id"] == TEST_SOURCE_ID
    assert len(store.saved_messages) == 1

    saved = store.saved_messages[0]

    assert saved["message_type"] == "account"
    assert saved["raw_body"] == raw_body
    assert saved["payload"]["account_mode"] == "DEMO"
    assert saved["payload"]["equity"] == 9950.0


@pytest.mark.asyncio
async def test_positions_snapshot_is_received_and_stored(monkeypatch):
    patch_runtime_types(monkeypatch)

    sent_at = datetime.now(timezone.utc)
    opened_at = sent_at - timedelta(minutes=5)

    payload = {
        "message_id": str(uuid4()),
        "source_id": TEST_SOURCE_ID,
        "sent_at": sent_at.isoformat(),
        "positions": [
            {
                "position_id": "123456",
                "symbol": "XAUUSD",
                "side": "BUY",
                "volume": 0.01,
                "entry_price": 2650.0,
                "current_price": 2655.0,
                "stop_loss": 2640.0,
                "take_profit": 2670.0,
                "floating_profit": 5.0,
                "opened_at": opened_at.isoformat(),
            }
        ],
    }

    raw_body = json.dumps(payload).encode("utf-8")
    store = FakeMarketDataStore(result=True)

    request = build_request(
        market_data_store=store,
    )

    async def fake_authentication(**kwargs):
        return raw_body

    monkeypatch.setattr(
        market_data,
        "authenticate_market_data_request",
        fake_authentication,
    )

    result = await market_data.ingest_positions(request)

    assert result["status"] == "stored"
    assert result["message_type"] == "positions"
    assert result["source_id"] == TEST_SOURCE_ID
    assert len(store.saved_messages) == 1

    saved = store.saved_messages[0]

    assert saved["message_type"] == "positions"
    assert saved["raw_body"] == raw_body
    assert len(saved["payload"]["positions"]) == 1
    assert saved["payload"]["positions"][0]["position_id"] == "123456"
