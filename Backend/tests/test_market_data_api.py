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


def build_request(
    *,
    device_registry=None,
    nonce_store=None,
    market_data_store=None,
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
