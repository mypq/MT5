
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from app.core.market_time_store import (
    MarketTimeStore,
    MarketTimeStoreError,
)


DATABASE_URL = os.getenv("MARKET_TIME_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="PostgreSQL integration database is not configured",
)


@pytest.fixture
def postgres_store():
    source_id = "PG-TEST-" + uuid.uuid4().hex

    store = MarketTimeStore(DATABASE_URL)

    asyncio.run(store.initialize())

    asyncio.run(
        store.save_device_config(
            source_id=source_id,
            broker_name="Integration Test Broker",
            server_name="PostgreSQL Test Server",
            timezone_name="Europe/Athens",
            reported_utc_offset_minutes=120,
        )
    )

    yield store, source_id

    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """
            DELETE FROM gold_bot_time_offset_history_v2
            WHERE source_id = %s
            """,
            (source_id,),
        )

        connection.execute(
            """
            DELETE FROM gold_bot_time_offset_history
            WHERE source_id = %s
            """,
            (source_id,),
        )

        connection.execute(
            """
            DELETE FROM gold_bot_device_time_config
            WHERE source_id = %s
            """,
            (source_id,),
        )


def test_postgres_tables_are_created(postgres_store):
    _, source_id = postgres_store

    with psycopg.connect(DATABASE_URL) as connection:
        rows = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN (
                  'gold_bot_device_time_config',
                  'gold_bot_time_offset_history',
                  'gold_bot_time_offset_history_v2'
              )
            """
        ).fetchall()

    table_names = {row[0] for row in rows}

    assert "gold_bot_device_time_config" in table_names
    assert "gold_bot_time_offset_history" in table_names
    assert "gold_bot_time_offset_history_v2" in table_names

    assert source_id.startswith("PG-TEST-")


def test_postgres_saves_device_as_unverified(postgres_store):
    _, source_id = postgres_store

    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            """
            SELECT
                broker_name,
                server_name,
                timezone_name,
                reported_utc_offset_minutes,
                config_status
            FROM gold_bot_device_time_config
            WHERE source_id = %s
            """,
            (source_id,),
        ).fetchone()

    assert row is not None
    assert row[0] == "Integration Test Broker"
    assert row[1] == "PostgreSQL Test Server"
    assert row[2] == "Europe/Athens"
    assert row[3] == 120
    assert row[4] == "UNVERIFIED"


@pytest.mark.asyncio
async def test_postgres_inserts_utc_period(postgres_store):
    store, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    end = start + timedelta(days=90)

    record_id = await store.add_utc_offset_period(
        source_id=source_id,
        effective_from_utc=start,
        effective_to_utc=end,
        utc_offset_minutes=120,
        evidence_reference="PostgreSQL integration test",
        is_dst=False,
    )

    assert record_id > 0

    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            """
            SELECT
                effective_from_utc,
                effective_to_utc,
                utc_offset_minutes,
                validation_status
            FROM gold_bot_time_offset_history_v2
            WHERE id = %s
            """,
            (record_id,),
        ).fetchone()

    assert row is not None
    assert row[0] == start
    assert row[1] == end
    assert row[2] == 120
    assert row[3] == "UNVERIFIED"


@pytest.mark.asyncio
async def test_postgres_allows_adjacent_periods(postgres_store):
    store, source_id = postgres_store

    boundary = datetime(
        2026, 6, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    first_id = await store.add_utc_offset_period(
        source_id=source_id,
        effective_from_utc=boundary - timedelta(days=30),
        effective_to_utc=boundary,
        utc_offset_minutes=120,
        evidence_reference="First adjacent period",
    )

    second_id = await store.add_utc_offset_period(
        source_id=source_id,
        effective_from_utc=boundary,
        effective_to_utc=None,
        utc_offset_minutes=180,
        evidence_reference="Second adjacent period",
    )

    assert first_id != second_id


@pytest.mark.asyncio
async def test_postgres_rejects_overlapping_periods(postgres_store):
    store, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    await store.add_utc_offset_period(
        source_id=source_id,
        effective_from_utc=start,
        effective_to_utc=start + timedelta(days=60),
        utc_offset_minutes=120,
        evidence_reference="Original period",
    )

    with pytest.raises(
        MarketTimeStoreError,
        match="overlaps",
    ):
        await store.add_utc_offset_period(
            source_id=source_id,
            effective_from_utc=start + timedelta(days=30),
            effective_to_utc=start + timedelta(days=90),
            utc_offset_minutes=180,
            evidence_reference="Overlapping period",
        )


@pytest.mark.asyncio
async def test_postgres_serializes_concurrent_overlapping_inserts(
    postgres_store,
):
    store, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    async def insert_period(evidence):
        return await store.add_utc_offset_period(
            source_id=source_id,
            effective_from_utc=start,
            effective_to_utc=start + timedelta(days=60),
            utc_offset_minutes=120,
            evidence_reference=evidence,
        )

    results = await asyncio.gather(
        insert_period("Concurrent attempt A"),
        insert_period("Concurrent attempt B"),
        return_exceptions=True,
    )

    successes = [
        result
        for result in results
        if isinstance(result, int)
    ]

    failures = [
        result
        for result in results
        if isinstance(result, MarketTimeStoreError)
    ]

    assert len(successes) == 1
    assert len(failures) == 1

    with psycopg.connect(DATABASE_URL) as connection:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM gold_bot_time_offset_history_v2
            WHERE source_id = %s
              AND validation_status <> 'REJECTED'
            """,
            (source_id,),
        ).fetchone()[0]

    assert count == 1


@pytest.mark.asyncio
async def test_postgres_rejects_unknown_device(store=None):
    store = MarketTimeStore(DATABASE_URL)

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    with pytest.raises(
        MarketTimeStoreError,
        match="does not exist",
    ):
        await store.add_utc_offset_period(
            source_id="PG-UNKNOWN-" + uuid.uuid4().hex,
            effective_from_utc=start,
            effective_to_utc=None,
            utc_offset_minutes=120,
            evidence_reference="Unknown device test",
        )

@pytest.mark.asyncio
async def test_postgres_database_constraint_rejects_direct_overlap(
    postgres_store,
):
    _, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """
            INSERT INTO gold_bot_time_offset_history_v2 (
                source_id,
                effective_from_utc,
                effective_to_utc,
                utc_offset_minutes,
                evidence_reference
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                source_id,
                start,
                start + timedelta(days=60),
                120,
                "Direct database insert A",
            ),
        )

        with pytest.raises(
            psycopg.errors.ExclusionViolation
        ):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO gold_bot_time_offset_history_v2 (
                        source_id,
                        effective_from_utc,
                        effective_to_utc,
                        utc_offset_minutes,
                        evidence_reference
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        source_id,
                        start + timedelta(days=30),
                        start + timedelta(days=90),
                        180,
                        "Direct database insert B",
                    ),
                )

def test_postgres_allows_overlap_with_rejected_period(
    postgres_store,
):
    _, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """
            INSERT INTO gold_bot_time_offset_history_v2 (
                source_id,
                effective_from_utc,
                effective_to_utc,
                utc_offset_minutes,
                evidence_reference,
                validation_status
            )
            VALUES (%s, %s, %s, %s, %s, 'REJECTED')
            """,
            (
                source_id,
                start,
                start + timedelta(days=60),
                120,
                "Rejected historical period",
            ),
        )

        connection.execute(
            """
            INSERT INTO gold_bot_time_offset_history_v2 (
                source_id,
                effective_from_utc,
                effective_to_utc,
                utc_offset_minutes,
                evidence_reference,
                validation_status
            )
            VALUES (%s, %s, %s, %s, %s, 'UNVERIFIED')
            """,
            (
                source_id,
                start + timedelta(days=30),
                start + timedelta(days=90),
                180,
                "Replacement period",
            ),
        )
def test_postgres_database_rejects_invalid_period_and_offset(
    postgres_store,
):
    _, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    with psycopg.connect(DATABASE_URL) as connection:

        # Reject a period whose end equals its start.
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO gold_bot_time_offset_history_v2 (
                        source_id,
                        effective_from_utc,
                        effective_to_utc,
                        utc_offset_minutes,
                        evidence_reference
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        source_id,
                        start,
                        start,
                        120,
                        "Invalid zero-length period",
                    ),
                )

        # Reject an offset outside the allowed range.
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO gold_bot_time_offset_history_v2 (
                        source_id,
                        effective_from_utc,
                        effective_to_utc,
                        utc_offset_minutes,
                        evidence_reference
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        source_id,
                        start,
                        start + timedelta(days=30),
                        900,
                        "Invalid UTC offset",
                    ),
                )
