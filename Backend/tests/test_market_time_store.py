import asyncio
from datetime import datetime, timedelta, timezone
import os
from importlib.resources import files
from unittest.mock import MagicMock, patch
from zoneinfo import TZPATH, ZoneInfo

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


def test_accepts_real_iana_timezone(store):
    assert (
        store._validate_timezone_name("Asia/Baghdad")
        == "Asia/Baghdad"
    )


def test_rejects_unknown_iana_timezone(store):
    with pytest.raises(
        MarketTimeStoreError,
        match="Unknown IANA timezone",
    ):
        store._validate_timezone_name(
            "Mars/Olympus_Mons"
        )


def test_rejects_malformed_iana_timezone(store):
    with pytest.raises(MarketTimeStoreError):
        store._validate_timezone_name(
            "Asia//Baghdad"
        )


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


def test_uses_installed_tzdata_database():
    if os.environ.get("PYTHONTZPATH") != "":
        pytest.skip(
            "This test requires PYTHONTZPATH to be empty"
        )

    assert TZPATH == ()

    baghdad_zone_file = (
        files("tzdata.zoneinfo")
        .joinpath("Asia", "Baghdad")
    )

    assert baghdad_zone_file.is_file()

    baghdad = ZoneInfo("Asia/Baghdad")

    winter_time = datetime(
        2026, 1, 15, 12, 0,
        tzinfo=baghdad,
    )

    assert winter_time.utcoffset() == timedelta(hours=3)
def make_fetchall_connection(rows):
    connection = MagicMock()
    connection.execute.return_value.fetchall.return_value = rows

    context = MagicMock()
    context.__enter__.return_value = connection
    context.__exit__.return_value = False

    return connection, context


@pytest.mark.asyncio
async def test_resolves_broker_local_time_with_verified_period(
    store,
):
    local_time = datetime(2026, 1, 1, 3, 0)

    start = datetime(
        2025, 12, 31, 0, 0,
        tzinfo=timezone.utc,
    )
    end = datetime(
        2026, 1, 2, 0, 0,
        tzinfo=timezone.utc,
    )

    _, context = make_fetchall_connection([
        (start, end, 180),
    ])

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        result = await store.resolve_broker_local_time(
            source_id="MT5-DEMO-01",
            broker_local_time=local_time,
        )

    assert result == datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )


@pytest.mark.asyncio
async def test_returns_none_without_verified_periods(store):
    _, context = make_fetchall_connection([])

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        result = await store.resolve_broker_local_time(
            source_id="MT5-DEMO-01",
            broker_local_time=datetime(2026, 1, 1, 3, 0),
        )

    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_candidate_is_outside_period(
    store,
):
    start = datetime(
        2026, 1, 2, 0, 0,
        tzinfo=timezone.utc,
    )
    end = datetime(
        2026, 1, 3, 0, 0,
        tzinfo=timezone.utc,
    )

    _, context = make_fetchall_connection([
        (start, end, 180),
    ])

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        result = await store.resolve_broker_local_time(
            source_id="MT5-DEMO-01",
            broker_local_time=datetime(2026, 1, 1, 3, 0),
        )

    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_multiple_periods_match(store):
    start = datetime(
        2025, 12, 31, 0, 0,
        tzinfo=timezone.utc,
    )
    end = datetime(
        2026, 1, 2, 0, 0,
        tzinfo=timezone.utc,
    )

    _, context = make_fetchall_connection([
        (start, end, 180),
        (start, end, 120),
    ])

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        result = await store.resolve_broker_local_time(
            source_id="MT5-DEMO-01",
            broker_local_time=datetime(2026, 1, 1, 3, 0),
        )

    assert result is None


@pytest.mark.asyncio
async def test_rejects_timezone_aware_broker_local_time(store):
    aware_time = datetime(
        2026, 1, 1, 3, 0,
        tzinfo=timezone.utc,
    )

    with pytest.raises(
        MarketTimeStoreError,
        match="must not contain timezone data",
    ):
        await store.resolve_broker_local_time(
            source_id="MT5-DEMO-01",
            broker_local_time=aware_time,
        )


@pytest.mark.asyncio
async def test_rejects_invalid_source_id_for_resolution(store):
    with pytest.raises(MarketTimeStoreError):
        await store.resolve_broker_local_time(
            source_id="invalid source",
            broker_local_time=datetime(2026, 1, 1, 3, 0),
        )


@pytest.mark.asyncio
async def test_resolution_database_failure_raises_store_error(
    store,
):
    with patch.object(
        store,
        "_connect",
        side_effect=RuntimeError("Database unavailable"),
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="Unable to resolve broker-local timestamp",
        ):
            await store.resolve_broker_local_time(
                source_id="MT5-DEMO-01",
                broker_local_time=datetime(2026, 1, 1, 3, 0),
            )

@pytest.mark.asyncio
async def test_rejects_invalid_source_id_for_new_period(
    store,
    utc_start,
):
    with pytest.raises(MarketTimeStoreError):
        await store.add_utc_offset_period(
            source_id="invalid source",
            effective_from_utc=utc_start,
            effective_to_utc=None,
            utc_offset_minutes=180,
            evidence_reference="Broker records",
        )


@pytest.mark.asyncio
async def test_rejects_oversized_evidence_reference(
    store,
    utc_start,
):
    with pytest.raises(MarketTimeStoreError):
        await store.add_utc_offset_period(
            source_id="MT5-DEMO-01",
            effective_from_utc=utc_start,
            effective_to_utc=None,
            utc_offset_minutes=180,
            evidence_reference="E" * 513,
        )


@pytest.mark.asyncio
async def test_verified_period_includes_exact_start_boundary(
    store,
):
    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )
    end = datetime(
        2026, 1, 2, 0, 0,
        tzinfo=timezone.utc,
    )

    _, context = make_fetchall_connection([
        (start, end, 180),
    ])

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        result = await store.resolve_broker_local_time(
            source_id="MT5-DEMO-01",
            broker_local_time=datetime(2026, 1, 1, 3, 0),
        )

    assert result == start


@pytest.mark.asyncio
async def test_verified_period_excludes_exact_end_boundary(
    store,
):
    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )
    end = datetime(
        2026, 1, 2, 0, 0,
        tzinfo=timezone.utc,
    )

    _, context = make_fetchall_connection([
        (start, end, 180),
    ])

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        result = await store.resolve_broker_local_time(
            source_id="MT5-DEMO-01",
            broker_local_time=datetime(2026, 1, 2, 3, 0),
        )

    assert result is None


@pytest.mark.asyncio
async def test_resolution_query_only_uses_verified_records(
    store,
):
    _, context = make_fetchall_connection([])

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        await store.resolve_broker_local_time(
            source_id="MT5-DEMO-01",
            broker_local_time=datetime(2026, 1, 1, 3, 0),
        )

    query = context.__enter__.return_value.execute.call_args.args[0]

    assert "validation_status = 'VERIFIED'" in query


def test_changed_device_config_invalidates_verified_periods(
    store,
):
    connection, context = make_connection([
        ("MT5-DEMO-01",),
    ])

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        store._save_device_config_sync(
            source_id="MT5-DEMO-01",
            broker_name="Broker A",
            server_name="Demo Server",
            timezone_name="Asia/Baghdad",
            reported_utc_offset_minutes=180,
        )

    assert connection.execute.call_count == 2

    invalidation_query = (
        connection.execute.call_args_list[1].args[0]
    )

    assert "validation_status = 'UNVERIFIED'" in invalidation_query
    assert "validation_status = 'VERIFIED'" in invalidation_query


def test_unchanged_device_config_does_not_invalidate_periods(
    store,
):
    connection, context = make_connection([
        None,
    ])

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        store._save_device_config_sync(
            source_id="MT5-DEMO-01",
            broker_name="Broker A",
            server_name="Demo Server",
            timezone_name="Asia/Baghdad",
            reported_utc_offset_minutes=180,
        )

    assert connection.execute.call_count == 1


@pytest.mark.asyncio
async def test_resolution_rejects_non_datetime_input(store):
    with pytest.raises(MarketTimeStoreError):
        await store.resolve_broker_local_time(
            source_id="MT5-DEMO-01",
            broker_local_time="2026-01-01T03:00:00",
        )

def make_initialize_connection(
    *,
    missing_table=None,
    missing_column=None,
    constraint_exists=True,
):
    required_columns = {
        "gold_bot_device_time_config": [
            "source_id",
            "broker_name",
            "server_name",
            "timezone_name",
            "time_source",
            "reported_utc_offset_minutes",
            "config_status",
            "updated_at",
        ],
        "gold_bot_time_offset_history": [
            "id",
            "source_id",
            "broker_local_from",
            "broker_local_to",
            "utc_offset_minutes",
            "proof_source",
            "is_dst",
            "validation_status",
            "recorded_at",
        ],
        "gold_bot_time_offset_history_v2": [
            "id",
            "source_id",
            "effective_from_utc",
            "effective_to_utc",
            "utc_offset_minutes",
            "evidence_reference",
            "is_dst",
            "validation_status",
            "recorded_at",
        ],
    }

    rows = []

    for table_name, columns in required_columns.items():
        if table_name == missing_table:
            continue

        for column_name in columns:
            if (
                table_name == missing_column[0]
                and column_name == missing_column[1]
            ) if missing_column else False:
                continue

            rows.append((table_name, column_name))

    connection = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = rows
    result.fetchone.return_value = (constraint_exists,)
    connection.execute.return_value = result

    context = MagicMock()
    context.__enter__.return_value = connection
    context.__exit__.return_value = False

    return connection, context


@pytest.mark.asyncio
async def test_initialize_accepts_complete_database_schema(store):
    _, context = make_initialize_connection()

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_missing_database_table(store):
    _, context = make_initialize_connection(
        missing_table="gold_bot_time_offset_history_v2",
    )

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="Missing table",
        ):
            await store.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_missing_database_column(store):
    _, context = make_initialize_connection(
        missing_column=(
            "gold_bot_device_time_config",
            "timezone_name",
        ),
    )

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="missing columns",
        ):
            await store.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_missing_overlap_constraint(store):
    _, context = make_initialize_connection(
        constraint_exists=False,
    )

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="Missing V2 time overlap exclusion constraint",
        ):
            await store.initialize()


@pytest.mark.asyncio
async def test_initialize_wraps_database_connection_failure(store):
    with patch.object(
        store,
        "_connect",
        side_effect=RuntimeError("Database unavailable"),
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="Unable to initialize broker-time storage",
        ):
            await store.initialize()

def test_new_period_locks_device_row_before_checking_overlap(
    store,
    utc_start,
):
    connection, context = make_connection([
        ("MT5-DEMO-01",),
        None,
        (51,),
    ])

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        store._add_utc_offset_period_sync(
            source_id="MT5-DEMO-01",
            effective_from_utc=utc_start,
            effective_to_utc=None,
            utc_offset_minutes=180,
            evidence_reference="Broker evidence",
            is_dst=False,
        )

    first_query = connection.execute.call_args_list[0].args[0]

    assert "FOR UPDATE" in first_query
    assert "gold_bot_device_time_config" in first_query


def test_overlap_check_ignores_rejected_periods(
    store,
    utc_start,
):
    connection, context = make_connection([
        ("MT5-DEMO-01",),
        None,
        (52,),
    ])

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        store._add_utc_offset_period_sync(
            source_id="MT5-DEMO-01",
            effective_from_utc=utc_start,
            effective_to_utc=None,
            utc_offset_minutes=180,
            evidence_reference="Broker evidence",
            is_dst=False,
        )

    overlap_query = connection.execute.call_args_list[1].args[0]

    assert "validation_status <> 'REJECTED'" in overlap_query


def test_overlap_prevents_new_period_insertion(
    store,
    utc_start,
):
    connection, context = make_connection([
        ("MT5-DEMO-01",),
        (1,),
    ])

    with patch.object(
        store,
        "_connect",
        return_value=context,
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="overlaps",
        ):
            store._add_utc_offset_period_sync(
                source_id="MT5-DEMO-01",
                effective_from_utc=utc_start,
                effective_to_utc=None,
                utc_offset_minutes=180,
                evidence_reference="Broker evidence",
                is_dst=False,
            )

    assert connection.execute.call_count == 2


def test_database_failure_while_adding_period_is_wrapped(
    store,
    utc_start,
):
    with patch.object(
        store,
        "_connect",
        side_effect=RuntimeError("Database unavailable"),
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="Unable to record UTC broker offset history",
        ):
            store._add_utc_offset_period_sync(
                source_id="MT5-DEMO-01",
                effective_from_utc=utc_start,
                effective_to_utc=None,
                utc_offset_minutes=180,
                evidence_reference="Broker evidence",
                is_dst=False,
            )

def test_validate_text_trims_surrounding_whitespace(store):
    assert store._validate_text(
        "  Broker A  ",
        "broker name",
        128,
    ) == "Broker A"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "x" * 129,
        123,
        True,
    ],
)
def test_validate_text_rejects_invalid_values(store, value):
    with pytest.raises(MarketTimeStoreError):
        store._validate_text(
            value,
            "broker name",
            128,
        )


def test_validate_text_accepts_none(store):
    assert store._validate_text(
        None,
        "broker name",
        128,
    ) is None


def test_validate_utc_datetime_normalizes_non_utc_offset(store):
    source = datetime(
        2026, 1, 1, 5, 30,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
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
    [-720, -300, 0, 180, 840],
)
@pytest.mark.asyncio
async def test_accepts_boundary_history_offsets(
    store,
    utc_start,
    offset,
):
    with patch.object(
        store,
        "_add_utc_offset_period_sync",
        return_value=60,
    ) as add_mock:
        result = await store.add_utc_offset_period(
            source_id="MT5-DEMO-01",
            effective_from_utc=utc_start,
            effective_to_utc=None,
            utc_offset_minutes=offset,
            evidence_reference="Broker evidence",
        )

    assert result == 60
    assert add_mock.call_args.kwargs["utc_offset_minutes"] == offset


@pytest.mark.asyncio
async def test_add_period_normalizes_boundaries_to_utc(
    store,
):
    start = datetime(
        2026, 1, 1, 3, 0,
        tzinfo=timezone(timedelta(hours=3)),
    )
    end = datetime(
        2026, 6, 1, 3, 0,
        tzinfo=timezone(timedelta(hours=3)),
    )

    with patch.object(
        store,
        "_add_utc_offset_period_sync",
        return_value=61,
    ) as add_mock:
        await store.add_utc_offset_period(
            source_id="MT5-DEMO-01",
            effective_from_utc=start,
            effective_to_utc=end,
            utc_offset_minutes=180,
            evidence_reference="Broker evidence",
        )

    kwargs = add_mock.call_args.kwargs

    assert kwargs["effective_from_utc"] == datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )
    assert kwargs["effective_to_utc"] == datetime(
        2026, 6, 1, 0, 0,
        tzinfo=timezone.utc,
    )


@pytest.mark.asyncio
async def test_save_device_config_trims_and_validates_metadata(
    store,
):
    with patch.object(
        store,
        "_save_device_config_sync",
    ) as save_mock:
        await store.save_device_config(
            source_id="MT5-DEMO-01",
            broker_name="  Broker A  ",
            server_name="  Demo Server  ",
            timezone_name="Asia/Baghdad",
            reported_utc_offset_minutes=180,
        )

    kwargs = save_mock.call_args.kwargs

    assert kwargs["broker_name"] == "Broker A"
    assert kwargs["server_name"] == "Demo Server"
    assert kwargs["timezone_name"] == "Asia/Baghdad"
    assert kwargs["reported_utc_offset_minutes"] == 180


@pytest.mark.asyncio
async def test_save_device_config_rejects_invalid_timezone(
    store,
):
    with pytest.raises(MarketTimeStoreError):
        await store.save_device_config(
            source_id="MT5-DEMO-01",
            timezone_name="Invalid/Unknown_Zone",
        )


def test_save_device_config_wraps_database_failure(store):
    with patch.object(
        store,
        "_connect",
        side_effect=RuntimeError("Database unavailable"),
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="Unable to save broker-time configuration",
        ):
            store._save_device_config_sync(
                source_id="MT5-DEMO-01",
                broker_name="Broker A",
                server_name="Demo Server",
                timezone_name="Asia/Baghdad",
                reported_utc_offset_minutes=180,
            )


@pytest.mark.asyncio
async def test_add_period_preserves_dst_value(store, utc_start):
    with patch.object(
        store,
        "_add_utc_offset_period_sync",
        return_value=62,
    ) as add_mock:
        await store.add_utc_offset_period(
            source_id="MT5-DEMO-01",
            effective_from_utc=utc_start,
            effective_to_utc=None,
            utc_offset_minutes=180,
            evidence_reference="Broker evidence",
            is_dst=True,
        )

    assert add_mock.call_args.kwargs["is_dst"] is True
