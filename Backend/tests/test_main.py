
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import main
from app.core.device_secret_crypto import DeviceSecretCipher


class FakeCipher(DeviceSecretCipher):
    def __init__(self, key):
        # Test double: preserves the required DeviceSecretCipher type.
        pass


def fake_secret(value):
    return type(
        "Secret",
        (),
        {
            "get_secret_value": lambda self: value,
        },
    )()


def test_lifespan_disabled_mode_sets_database_disabled():
    with TestClient(main.app) as client:
        assert main.app.state.database_status == "DISABLED"
        assert main.app.state.nonce_store is None
        assert main.app.state.device_registry is None
        assert main.app.state.market_data_store is None
        assert main.app.state.market_time_store is None
        assert main.app.state.nonce_cleanup_task is None

        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        assert response.json()["database_status"] == "DISABLED"


def test_root_endpoint_returns_application_information():
    with TestClient(main.app) as client:
        response = client.get("/")

    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "running"
    assert payload["trading_mode"] == "DEMO_ONLY"
    assert "application" in payload
    assert "version" in payload


def test_health_endpoint_reports_disabled_ingestion(monkeypatch):
    monkeypatch.setattr(
        main.settings,
        "MARKET_DATA_INGESTION_ENABLED",
        False,
    )

    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "healthy"
    assert payload["database_status"] == "DISABLED"
    assert payload["market_data_ingestion_enabled"] is False
    assert payload["shutting_down"] is False


def test_health_endpoint_reports_unhealthy_database_when_not_connected(
    monkeypatch,
):
    calls = []

    class FakeStore:
        def __init__(self, *args, **kwargs):
            calls.append(("construct", self.__class__.__name__))

        async def initialize(self):
            calls.append(("initialize", self.__class__.__name__))

    class FakeNonceStore(FakeStore):
        pass

    class FakeDeviceRegistry(FakeStore):
        pass

    class FakeMarketDataStore(FakeStore):
        pass

    class FakeMarketTimeStore(FakeStore):
        pass

    async def fake_cleanup(store):
        await asyncio.Future()

    monkeypatch.setattr(
        main.settings,
        "MARKET_DATA_INGESTION_ENABLED",
        True,
    )
    monkeypatch.setattr(
        main.settings,
        "DATABASE_URL",
        fake_secret("postgresql://test"),
    )
    monkeypatch.setattr(
        main.settings,
        "DEVICE_SECRET_ENCRYPTION_KEY",
        fake_secret("ab" * 32),
    )

    monkeypatch.setattr(main, "DeviceSecretCipher", FakeCipher)
    monkeypatch.setattr(main, "PostgresNonceStore", FakeNonceStore)
    monkeypatch.setattr(main, "DeviceRegistry", FakeDeviceRegistry)
    monkeypatch.setattr(main, "MarketDataStore", FakeMarketDataStore)
    monkeypatch.setattr(main, "MarketTimeStore", FakeMarketTimeStore)
    monkeypatch.setattr(main, "run_nonce_cleanup", fake_cleanup)

    with TestClient(main.app) as client:
        # Startup succeeds with isolated fake stores.
        # This test specifically verifies the unhealthy health response.
        main.app.state.database_status = "NOT_CONNECTED"

        response = client.get("/health")

        assert response.status_code == 200
        payload = response.json()

        assert payload["status"] == "unhealthy"
        assert payload["database_status"] == "NOT_CONNECTED"
        assert payload["market_data_ingestion_enabled"] is True


def test_system_status_reports_demo_only_safety():
    with TestClient(main.app) as client:
        response = client.get("/api/v1/system/status")

    assert response.status_code == 200
    payload = response.json()

    assert payload["trading_mode"] == "DEMO_ONLY"
    assert payload["mt5_connection_status"] == "NOT_CONNECTED"
    assert payload["analysis_mode_available"] is True
    assert payload["database_status"] == "DISABLED"
    assert payload["device_registry_status"] == "disabled"
    assert payload["nonce_store_status"] == "disabled"
    assert payload["market_data_store_status"] == "disabled"
    assert payload["market_time_store_status"] == "disabled"
    assert payload["nonce_cleanup_status"] == "stopped"


def test_system_status_reports_automated_trading_setting(monkeypatch):
    monkeypatch.setattr(
        main.settings,
        "AUTOMATED_TRADING_ENABLED",
        False,
    )

    with TestClient(main.app) as client:
        response = client.get("/api/v1/system/status")

    assert response.status_code == 200
    assert response.json()["automated_trading_enabled"] is False


def test_lifespan_initializes_all_stores_when_ingestion_enabled(
    monkeypatch,
):
    calls = []

    class FakeStore:
        def __init__(self, *args, **kwargs):
            calls.append(("construct", self.__class__.__name__))

        async def initialize(self):
            calls.append(("initialize", self.__class__.__name__))

    class FakeNonceStore(FakeStore):
        pass

    class FakeDeviceRegistry(FakeStore):
        pass

    class FakeMarketDataStore(FakeStore):
        pass

    class FakeMarketTimeStore(FakeStore):
        pass

    async def fake_cleanup(store):
        await asyncio.Future()

    monkeypatch.setattr(
        main.settings,
        "MARKET_DATA_INGESTION_ENABLED",
        True,
    )
    monkeypatch.setattr(
        main.settings,
        "DATABASE_URL",
        fake_secret("postgresql://test"),
    )
    monkeypatch.setattr(
        main.settings,
        "DEVICE_SECRET_ENCRYPTION_KEY",
        fake_secret("ab" * 32),
    )

    monkeypatch.setattr(main, "DeviceSecretCipher", FakeCipher)
    monkeypatch.setattr(main, "PostgresNonceStore", FakeNonceStore)
    monkeypatch.setattr(main, "DeviceRegistry", FakeDeviceRegistry)
    monkeypatch.setattr(main, "MarketDataStore", FakeMarketDataStore)
    monkeypatch.setattr(main, "MarketTimeStore", FakeMarketTimeStore)
    monkeypatch.setattr(main, "run_nonce_cleanup", fake_cleanup)

    with TestClient(main.app) as client:
        assert main.app.state.database_status == "CONNECTED"

        assert main.app.state.nonce_store is not None
        assert main.app.state.device_registry is not None
        assert main.app.state.market_data_store is not None
        assert main.app.state.market_time_store is not None

        assert main.app.state.nonce_cleanup_task is not None
        assert not main.app.state.nonce_cleanup_task.done()

        assert client.get("/health").json()["status"] == "healthy"

        status = client.get(
            "/api/v1/system/status"
        ).json()

        assert status["database_status"] == "CONNECTED"
        assert status["device_registry_status"] == "initialized"
        assert status["nonce_store_status"] == "initialized"
        assert status["market_data_store_status"] == "initialized"
        assert status["market_time_store_status"] == "initialized"
        assert status["nonce_cleanup_status"] == "running"

    initialize_calls = [
        name for action, name in calls if action == "initialize"
    ]

    assert initialize_calls == [
        "FakeNonceStore",
        "FakeDeviceRegistry",
        "FakeMarketDataStore",
        "FakeMarketTimeStore",
    ]

    assert main.app.state.shutting_down is True
    assert main.app.state.nonce_cleanup_task is None


def test_lifespan_stops_cleanup_task_on_shutdown(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_cleanup(store):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        async def initialize(self):
            pass

    monkeypatch.setattr(
        main.settings,
        "MARKET_DATA_INGESTION_ENABLED",
        True,
    )
    monkeypatch.setattr(
        main.settings,
        "DATABASE_URL",
        fake_secret("postgresql://test"),
    )
    monkeypatch.setattr(
        main.settings,
        "DEVICE_SECRET_ENCRYPTION_KEY",
        fake_secret("ab" * 32),
    )

    monkeypatch.setattr(main, "DeviceSecretCipher", FakeCipher)
    monkeypatch.setattr(main, "PostgresNonceStore", FakeStore)
    monkeypatch.setattr(main, "DeviceRegistry", FakeStore)
    monkeypatch.setattr(main, "MarketDataStore", FakeStore)
    monkeypatch.setattr(main, "MarketTimeStore", FakeStore)
    monkeypatch.setattr(main, "run_nonce_cleanup", fake_cleanup)

    with TestClient(main.app):
        assert (
            started.is_set()
            or main.app.state.nonce_cleanup_task is not None
        )

    assert cancelled.is_set()
    assert main.app.state.nonce_cleanup_task is None
    assert main.app.state.shutting_down is True


def test_lifespan_initialization_failure_sets_error_and_reraises(
    monkeypatch,
):
    class BrokenStore:
        def __init__(self, *args, **kwargs):
            pass

        async def initialize(self):
            raise RuntimeError("database initialization failed")

    monkeypatch.setattr(
        main.settings,
        "MARKET_DATA_INGESTION_ENABLED",
        True,
    )
    monkeypatch.setattr(
        main.settings,
        "DATABASE_URL",
        fake_secret("postgresql://test"),
    )
    monkeypatch.setattr(
        main.settings,
        "DEVICE_SECRET_ENCRYPTION_KEY",
        fake_secret("ab" * 32),
    )

    monkeypatch.setattr(main, "DeviceSecretCipher", FakeCipher)
    monkeypatch.setattr(main, "PostgresNonceStore", BrokenStore)

    with pytest.raises(RuntimeError, match="database initialization failed"):
        with TestClient(main.app):
            pass

    assert main.app.state.database_status == "ERROR"
    assert main.app.state.shutting_down is True


def test_unexpected_exception_handler_hides_internal_details():
    class FakeRequest:
        method = "GET"

        class URL:
            path = "/test"

        url = URL()

    response = asyncio.run(
        main.unexpected_exception_handler(
            FakeRequest(),
            RuntimeError("private database password"),
        )
    )

    assert response.status_code == 500
    assert response.body == (
        b'{"error":"INTERNAL_SERVER_ERROR",'
        b'"message":"An unexpected server error occurred."}'
    )
    assert b"private database password" not in response.body


def test_market_data_router_is_registered():
    paths = set(main.app.openapi()["paths"])

    assert "/" in paths
    assert "/health" in paths
    assert "/api/v1/system/status" in paths

    assert "/api/v1/market-data/candles" in paths
    assert "/api/v1/market-data/candles/v2" in paths
    assert "/api/v1/market-data/account" in paths
    assert "/api/v1/market-data/positions" in paths


def test_lifespan_marks_shutdown_even_when_ingestion_is_disabled():
    with TestClient(main.app):
        assert main.app.state.shutting_down is False

    assert main.app.state.shutting_down is True
