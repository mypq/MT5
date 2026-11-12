
from datetime import datetime, timedelta, timezone
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


@pytest.fixture
def utc_start():
    return datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def make_connection(fetchone_results):
    connection = MagicMock()
    connection.execute.return_value.fetchone.side_effect = (
        fetchone_results
    )

    context = MagicMock()
    context.__enter__.return_value = connection
    context.__exit__.return_value = False

    return connection, context


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
    [-720, -300, 0, 120, 840],
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


def test_rejects_naive_utc_datetime(store):
    with pytest.raises(MarketTimeStoreError):
        store._validate_utc_datetime(
            datetime(2026, 1, 1, 0, 0),
            "effective_from_utc",
        )


def test_rejects_invalid_utc_datetime_type(store):
    with pytest.raises(MarketTimeStoreError):
        store._validate_utc_datetime(
            "2026-01-01T00:00:00Z",
            "effective_from_utc",
        )


def test_accepts_utc_datetime(store, utc_start):
    result = store._validate_utc_datetime(
        utc_start,
        "effective_from_utc",
    )

    assert result == utc_start
    assert result.tzinfo == timezone.utc


def test_normalizes_aware_datetime_to_utc(store):
    source = datetime(
        2026, 1, 1, 3, 0,
        tzinfo=timezone(timedelta(hours=3)),
    )

    result = store._validate_utc_datetime(
        source,
        "effective_from_utc",
    )

    assert result == datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )


@pytest.mark.parametrize(
    "offset",
    [-721, 841, True, 120.5, "120"],
)
@pytest.mark.asyncio
async def test_rejects_invalid_history_offset(
    store,
    utc_start,
    offset,
):
    with pytest.raises(MarketTimeStoreError):
        await store.add_utc_offset_period(
            source_id="MT5-DEMO-01",
            effective_from_utc=utc_start,
            effective_to_utc=None,
            utc_offset_minutes=offset,
            evidence_reference="Test evidence",
        )


@pytest.mark.asyncio
async def test_rejects_missing_evidence_reference(
    store,
    utc_start,
):
    with pytest.raises(MarketTimeStoreError):
        await store.add_utc_offset_period(
            source_id="MT5-DEMO-01",
            effective_from_utc=utc_start,
            effective_to_utc=None,
            utc_offset_minutes=120,
            evidence_reference="   ",
        )


@pytest.mark.asyncio
async def test_rejects_naive_start_datetime(store):
    with pytest.raises(MarketTimeStoreError):
        await store.add_utc_offset_period(
            source_id="MT5-DEMO-01",
            effective_from_utc=datetime(2026, 1, 1),
            effective_to_utc=None,
            utc_offset_minutes=120,
            evidence_reference="Test evidence",
        )


@pytest.mark.asyncio
async def test_rejects_naive_end_datetime(
    store,
    utc_start,
):
    with pytest.raises(MarketTimeStoreError):
        await store.add_utc_offset_period(
            source_id="MT5-DEMO-01",
            effective_from_utc=utc_start,
            effective_to_utc=datetime(2026, 6, 1),
            utc_offset_minutes=120,
            evidence_reference="Test evidence",
        )


@pytest.mark.asyncio
async def test_rejects_invalid_utc_period(
    store,
    utc_start,
):
    with pytest.raises(
        MarketTimeStoreError,
        match="must be after",
    ):
        await store.add_utc_offset_period(
            source_id="MT5-DEMO-01",
            effective_from_utc=utc_start,
            effective_to_utc=utc_start,
            utc_offset_minutes=120,
            evidence_reference="Test evidence",
        )


@pytest.mark.asyncio
async def test_rejects_invalid_dst_value(
    store,
    utc_start,
):
    with pytest.raises(MarketTimeStoreError):
        await store.add_utc_offset_period(
            source_id="MT5-DEMO-01",
            effective_from_utc=utc_start,
            effective_to_utc=None,
            utc_offset_minutes=120,
            evidence_reference="Test evidence",
            is_dst="yes",
        )


@pytest.mark.asyncio
async def test_add_utc_offset_period_returns_inserted_id(
    store,
    utc_start,
):
    connection, context = make_connection(
        [
            ("MT5-DEMO-01",),
            None,
            (42,),
        ]
    )

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        result = await store.add_utc_offset_period(
            source_id="MT5-DEMO-01",
            effective_from_utc=utc_start,
            effective_to_utc=datetime(
                2026, 6, 1, tzinfo=timezone.utc
            ),
            utc_offset_minutes=120,
            evidence_reference="Broker historical records",
            is_dst=True,
        )

    assert result == 42

    insert_call = connection.execute.call_args_list[2]
    assert "gold_bot_time_offset_history_v2" in insert_call.args[0]
    assert "'UNVERIFIED'" in insert_call.args[0]


@pytest.mark.asyncio
async def test_add_utc_offset_period_rejects_overlap(
    store,
    utc_start,
):
    _, context = make_connection(
        [
            ("MT5-DEMO-01",),
            (1,),
        ]
    )

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="overlaps",
        ):
            await store.add_utc_offset_period(
                source_id="MT5-DEMO-01",
                effective_from_utc=utc_start,
                effective_to_utc=datetime(
                    2026, 6, 1, tzinfo=timezone.utc
                ),
                utc_offset_minutes=120,
                evidence_reference="Broker records",
            )


@pytest.mark.asyncio
async def test_add_utc_offset_period_rejects_unknown_device(
    store,
    utc_start,
):
    _, context = make_connection([None])

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="does not exist",
        ):
            await store.add_utc_offset_period(
                source_id="MT5-DEMO-01",
                effective_from_utc=utc_start,
                effective_to_utc=None,
                utc_offset_minutes=120,
                evidence_reference="Broker records",
            )


@pytest.mark.asyncio
async def test_adjacent_utc_periods_are_allowed(
    store,
    utc_start,
):
    _, context = make_connection(
        [
            ("MT5-DEMO-01",),
            None,
            (43,),
        ]
    )

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        result = await store.add_utc_offset_period(
            source_id="MT5-DEMO-01",
            effective_from_utc=datetime(
                2026, 6, 1, tzinfo=timezone.utc
            ),
            effective_to_utc=None,
            utc_offset_minutes=180,
            evidence_reference="Second period evidence",
        )

    assert result == 43


def test_store_does_not_expose_automatic_conversion(store):
    assert not hasattr(store, "convert_to_utc")
    assert not hasattr(store, "convert_broker_time")


def test_store_does_not_expose_legacy_period_writer(store):
    assert not hasattr(store, "add_offset_period")


def test_utc_period_writer_is_available(store):
    assert callable(store.add_utc_offset_period)
