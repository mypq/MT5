
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.core.market_time_store import (
    MarketTimeStore,
    MarketTimeStoreError,
)


@pytest.fixture
def store():
    return MarketTimeStore(
        "postgresql://user:password@localhost/test_db"
    )


def test_accepts_valid_postgresql_url():
    store = MarketTimeStore(
        "postgresql://user:password@localhost/test_db"
    )

    assert store._database_url.startswith("postgresql://")


def test_normalizes_psycopg_url():
    store = MarketTimeStore(
        "postgresql+psycopg://user:password@localhost/test_db"
    )

    assert store._database_url.startswith("postgresql://")


@pytest.mark.parametrize(
    "database_url",
    [
        "",
        "sqlite:///test.db",
        "mysql://localhost/test",
    ],
)
def test_rejects_invalid_database_url(database_url):
    with pytest.raises(MarketTimeStoreError):
        MarketTimeStore(database_url)


@pytest.mark.parametrize(
    "source_id",
    [
        "",
        "device with spaces",
        "device/invalid",
        "x" * 129,
    ],
)
def test_rejects_invalid_source_id(store, source_id):
    with pytest.raises(MarketTimeStoreError):
        store._validate_source_id(source_id)


def test_accepts_valid_source_id(store):
    assert store._validate_source_id("MT5-DEMO-01") == "MT5-DEMO-01"


@pytest.mark.parametrize(
    "timezone_name",
    [
        "../invalid",
        "/Europe/Athens",
        "Europe//Athens",
        "x" * 65,
    ],
)
def test_rejects_invalid_timezone_name(store, timezone_name):
    with pytest.raises(MarketTimeStoreError):
        store._validate_timezone_name(timezone_name)


def test_accepts_timezone_name(store):
    assert (
        store._validate_timezone_name("Europe/Athens")
        == "Europe/Athens"
    )


def test_accepts_none_timezone_name(store):
    assert store._validate_timezone_name(None) is None


@pytest.mark.parametrize(
    "offset",
    [
        -721,
        841,
        True,
        120.5,
        "120",
    ],
)
@pytest.mark.asyncio
async def test_rejects_invalid_reported_offset(store, offset):
    with pytest.raises(MarketTimeStoreError):
        await store.save_device_config(
            source_id="MT5-DEMO-01",
            reported_utc_offset_minutes=offset,
        )


@pytest.mark.parametrize(
    "offset",
    [
        -720,
        -300,
        0,
        120,
        840,
    ],
)
@pytest.mark.asyncio
async def test_accepts_valid_reported_offset(store, offset):
    with patch.object(
        store,
        "_save_device_config_sync",
    ) as save_mock:
        await store.save_device_config(
            source_id="MT5-DEMO-01",
            reported_utc_offset_minutes=offset,
        )

    save_mock.assert_called_once()


@pytest.mark.parametrize(
    "local_time",
    [
        datetime(
            2026, 11, 12, 8, 0,
            tzinfo=timezone.utc,
        ),
        "2026-11-12T08:00:00",
        None,
    ],
)
def test_rejects_non_naive_broker_local_datetime(
    store,
    local_time,
):
    with pytest.raises(MarketTimeStoreError):
        store._validate_local_datetime(
            local_time,
            "broker_local_from",
        )


def test_accepts_naive_broker_local_datetime(store):
    value = datetime(2026, 11, 12, 8, 0)

    result = store._validate_local_datetime(
        value,
        "broker_local_from",
    )

    assert result == value
    assert result.tzinfo is None


@pytest.mark.parametrize(
    "offset",
    [
        -721,
        841,
        True,
        120.5,
        "120",
    ],
)
@pytest.mark.asyncio
async def test_rejects_invalid_history_offset(store, offset):
    with pytest.raises(MarketTimeStoreError):
        await store.add_offset_period(
            source_id="MT5-DEMO-01",
            broker_local_from=datetime(2026, 1, 1),
            broker_local_to=None,
            utc_offset_minutes=offset,
            proof_source="Test source",
        )


@pytest.mark.asyncio
async def test_rejects_missing_proof_source(store):
    with pytest.raises(MarketTimeStoreError):
        await store.add_offset_period(
            source_id="MT5-DEMO-01",
            broker_local_from=datetime(2026, 1, 1),
            broker_local_to=None,
            utc_offset_minutes=120,
            proof_source="   ",
        )


@pytest.mark.asyncio
async def test_rejects_invalid_history_period(store):
    with pytest.raises(MarketTimeStoreError):
        await store.add_offset_period(
            source_id="MT5-DEMO-01",
            broker_local_from=datetime(2026, 2, 1),
            broker_local_to=datetime(2026, 1, 1),
            utc_offset_minutes=120,
            proof_source="Broker documentation",
        )


@pytest.mark.asyncio
async def test_rejects_invalid_dst_value(store):
    with pytest.raises(MarketTimeStoreError):
        await store.add_offset_period(
            source_id="MT5-DEMO-01",
            broker_local_from=datetime(2026, 1, 1),
            broker_local_to=None,
            utc_offset_minutes=120,
            proof_source="Broker documentation",
            is_dst="yes",
        )


@pytest.mark.asyncio
async def test_add_offset_period_returns_inserted_id(store):
    mock_connection = MagicMock()
    mock_connection.execute.return_value.fetchone.side_effect = [
        ("MT5-DEMO-01",),
        None,
        (42,),
    ]

    mock_context = MagicMock()
    mock_context.__enter__.return_value = mock_connection
    mock_context.__exit__.return_value = False

    with patch.object(
        store,
        "_connect",
        return_value=mock_context,
    ):
        result = await store.add_offset_period(
            source_id="MT5-DEMO-01",
            broker_local_from=datetime(2026, 1, 1),
            broker_local_to=datetime(2026, 6, 1),
            utc_offset_minutes=120,
            proof_source="Verified broker records",
            is_dst=True,
        )

    assert result == 42


@pytest.mark.asyncio
async def test_add_offset_period_rejects_overlap(store):
    mock_connection = MagicMock()
    mock_connection.execute.return_value.fetchone.side_effect = [
        ("MT5-DEMO-01",),
        (1,),
    ]

    mock_context = MagicMock()
    mock_context.__enter__.return_value = mock_connection
    mock_context.__exit__.return_value = False

    with patch.object(
        store,
        "_connect",
        return_value=mock_context,
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="overlaps",
        ):
            await store.add_offset_period(
                source_id="MT5-DEMO-01",
                broker_local_from=datetime(2026, 1, 1),
                broker_local_to=datetime(2026, 6, 1),
                utc_offset_minutes=120,
                proof_source="Broker records",
            )


@pytest.mark.asyncio
async def test_add_offset_period_rejects_unknown_device(store):
    mock_connection = MagicMock()
    mock_connection.execute.return_value.fetchone.return_value = None

    mock_context = MagicMock()
    mock_context.__enter__.return_value = mock_connection
    mock_context.__exit__.return_value = False

    with patch.object(
        store,
        "_connect",
        return_value=mock_context,
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="does not exist",
        ):
            await store.add_offset_period(
                source_id="MT5-DEMO-01",
                broker_local_from=datetime(2026, 1, 1),
                broker_local_to=None,
                utc_offset_minutes=120,
                proof_source="Broker records",
            )


def test_store_does_not_expose_automatic_conversion(store):
    assert not hasattr(store, "convert_to_utc")
    assert not hasattr(store, "convert_broker_time")
