import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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


def test_postgres_prevents_deleting_device_with_time_history(
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
                start + timedelta(days=30),
                180,
                "Device deletion protection test",
            ),
        )

        with pytest.raises(
            psycopg.errors.ForeignKeyViolation
        ):
            with connection.transaction():
                connection.execute(
                    """
                    DELETE FROM gold_bot_device_time_config
                    WHERE source_id = %s
                    """,
                    (source_id,),
                )


def test_postgres_rejects_time_history_for_unknown_device(
    postgres_store,
):
    _, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    unknown_source_id = "PG-UNKNOWN-" + uuid.uuid4().hex

    with psycopg.connect(DATABASE_URL) as connection:
        with pytest.raises(
            psycopg.errors.ForeignKeyViolation
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
                        unknown_source_id,
                        start,
                        start + timedelta(days=30),
                        180,
                        "Unknown device history test",
                    ),
                )


def test_postgres_rejects_changing_history_to_unknown_device(
    postgres_store,
):
    store, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    record_id = asyncio.run(
        store.add_utc_offset_period(
            source_id=source_id,
            effective_from_utc=start,
            effective_to_utc=start + timedelta(days=30),
            utc_offset_minutes=180,
            evidence_reference="Source change protection test",
        )
    )

    unknown_source_id = "PG-UNKNOWN-" + uuid.uuid4().hex

    with psycopg.connect(DATABASE_URL) as connection:
        with pytest.raises(
            psycopg.errors.ForeignKeyViolation
        ):
            with connection.transaction():
                connection.execute(
                    """
                    UPDATE gold_bot_time_offset_history_v2
                    SET source_id = %s
                    WHERE id = %s
                    """,
                    (unknown_source_id, record_id),
                )


def test_postgres_rejects_update_that_creates_overlap(
    postgres_store,
):
    store, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    first_id = asyncio.run(
        store.add_utc_offset_period(
            source_id=source_id,
            effective_from_utc=start,
            effective_to_utc=start + timedelta(days=30),
            utc_offset_minutes=120,
            evidence_reference="First protected period",
        )
    )

    second_id = asyncio.run(
        store.add_utc_offset_period(
            source_id=source_id,
            effective_from_utc=start + timedelta(days=30),
            effective_to_utc=start + timedelta(days=60),
            utc_offset_minutes=180,
            evidence_reference="Second protected period",
        )
    )

    with psycopg.connect(DATABASE_URL) as connection:
        with pytest.raises(
            psycopg.errors.ExclusionViolation
        ):
            with connection.transaction():
                connection.execute(
                    """
                    UPDATE gold_bot_time_offset_history_v2
                    SET effective_from_utc = %s
                    WHERE id = %s
                    """,
                    (
                        start + timedelta(days=15),
                        second_id,
                    ),
                )

    assert first_id != second_id


def test_postgres_rejects_open_ended_period_overlap(
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
            VALUES (%s, %s, NULL, %s, %s)
            """,
            (
                source_id,
                start,
                180,
                "Open ended period",
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
                        start + timedelta(days=60),
                        120,
                        "Overlapping closed period",
                    ),
                )


def test_postgres_rejects_reactivating_overlapping_period(
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
            VALUES (%s, %s, %s, %s, %s, 'UNVERIFIED')
            """,
            (
                source_id,
                start,
                start + timedelta(days=60),
                120,
                "Active period",
            ),
        )

        rejected = connection.execute(
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
            RETURNING id
            """,
            (
                source_id,
                start + timedelta(days=30),
                start + timedelta(days=90),
                180,
                "Rejected overlapping period",
            ),
        ).fetchone()

        rejected_id = rejected[0]

        with pytest.raises(
            psycopg.errors.ExclusionViolation
        ):
            with connection.transaction():
                connection.execute(
                    """
                    UPDATE gold_bot_time_offset_history_v2
                    SET validation_status = 'UNVERIFIED'
                    WHERE id = %s
                    """,
                    (rejected_id,),
                )


def test_postgres_rejects_invalid_validation_status(
    postgres_store,
):
    _, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    with psycopg.connect(DATABASE_URL) as connection:
        with pytest.raises(
            psycopg.errors.CheckViolation
        ):
            with connection.transaction():
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
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        source_id,
                        start,
                        start + timedelta(days=30),
                        120,
                        "Invalid status test",
                        "UNKNOWN",
                    ),
                )


def test_postgres_accepts_minimum_and_maximum_utc_offsets(
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
                start + timedelta(days=30),
                -720,
                "Minimum valid UTC offset",
            ),
        )

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
                start + timedelta(days=60),
                840,
                "Maximum valid UTC offset",
            ),
        )


def test_postgres_allows_same_period_for_different_devices(
    postgres_store,
):
    store, first_source_id = postgres_store

    second_source_id = "PG-TEST-" + uuid.uuid4().hex

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    try:
        asyncio.run(
            store.save_device_config(
                source_id=second_source_id,
                broker_name="Second Test Broker",
                server_name="Second Test Server",
                timezone_name="Asia/Baghdad",
                reported_utc_offset_minutes=180,
            )
        )

        with psycopg.connect(DATABASE_URL) as connection:
            for source_id in (
                first_source_id,
                second_source_id,
            ):
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
                        180,
                        "Independent device period",
                    ),
                )

    finally:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                DELETE FROM gold_bot_time_offset_history_v2
                WHERE source_id = %s
                """,
                (second_source_id,),
            )

            connection.execute(
                """
                DELETE FROM gold_bot_device_time_config
                WHERE source_id = %s
                """,
                (second_source_id,),
            )


def test_postgres_config_change_invalidates_verified_history(
    postgres_store,
):
    store, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    with psycopg.connect(DATABASE_URL) as connection:
        verified_id = connection.execute(
            """
            INSERT INTO gold_bot_time_offset_history_v2 (
                source_id,
                effective_from_utc,
                effective_to_utc,
                utc_offset_minutes,
                evidence_reference,
                validation_status
            )
            VALUES (%s, %s, %s, %s, %s, 'VERIFIED')
            RETURNING id
            """,
            (
                source_id,
                start,
                start + timedelta(days=30),
                180,
                "Verified history",
            ),
        ).fetchone()[0]

        rejected_id = connection.execute(
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
            RETURNING id
            """,
            (
                source_id,
                start + timedelta(days=30),
                start + timedelta(days=60),
                180,
                "Rejected history",
            ),
        ).fetchone()[0]

        connection.execute(
            """
            UPDATE gold_bot_device_time_config
            SET config_status = 'VERIFIED'
            WHERE source_id = %s
            """,
            (source_id,),
        )

    asyncio.run(
        store.save_device_config(
            source_id=source_id,
            broker_name="Integration Test Broker",
            server_name="PostgreSQL Test Server",
            timezone_name="Asia/Baghdad",
            reported_utc_offset_minutes=180,
        )
    )

    with psycopg.connect(DATABASE_URL) as connection:
        rows = connection.execute(
            """
            SELECT id, validation_status
            FROM gold_bot_time_offset_history_v2
            WHERE id IN (%s, %s)
            ORDER BY id
            """,
            (verified_id, rejected_id),
        ).fetchall()

        config_status = connection.execute(
            """
            SELECT config_status
            FROM gold_bot_device_time_config
            WHERE source_id = %s
            """,
            (source_id,),
        ).fetchone()[0]

    assert rows == [
        (verified_id, "UNVERIFIED"),
        (rejected_id, "REJECTED"),
    ]

    assert config_status == "UNVERIFIED"


def test_postgres_unchanged_config_preserves_verified_history(
    postgres_store,
):
    store, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    with psycopg.connect(DATABASE_URL) as connection:
        record_id = connection.execute(
            """
            INSERT INTO gold_bot_time_offset_history_v2 (
                source_id,
                effective_from_utc,
                effective_to_utc,
                utc_offset_minutes,
                evidence_reference,
                validation_status
            )
            VALUES (%s, %s, %s, %s, %s, 'VERIFIED')
            RETURNING id
            """,
            (
                source_id,
                start,
                start + timedelta(days=30),
                180,
                "Unchanged config test",
            ),
        ).fetchone()[0]

        connection.execute(
            """
            UPDATE gold_bot_device_time_config
            SET config_status = 'VERIFIED'
            WHERE source_id = %s
            """,
            (source_id,),
        )

    asyncio.run(
        store.save_device_config(
            source_id=source_id,
            broker_name="Integration Test Broker",
            server_name="PostgreSQL Test Server",
            timezone_name="Europe/Athens",
            reported_utc_offset_minutes=120,
        )
    )

    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            """
            SELECT validation_status
            FROM gold_bot_time_offset_history_v2
            WHERE id = %s
            """,
            (record_id,),
        ).fetchone()

        config_status = connection.execute(
            """
            SELECT config_status
            FROM gold_bot_device_time_config
            WHERE source_id = %s
            """,
            (source_id,),
        ).fetchone()[0]

    assert row[0] == "VERIFIED"
    assert config_status == "VERIFIED"


@pytest.mark.asyncio
async def test_postgres_save_config_wraps_connection_failure():
    store = MarketTimeStore(DATABASE_URL)

    with patch.object(
        store,
        "_connect",
        side_effect=psycopg.OperationalError(
            "Simulated database connection failure"
        ),
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="Unable to save broker-time configuration",
        ) as error:
            await store.save_device_config(
                source_id="PG-CONNECTION-FAILURE",
                broker_name="Test Broker",
            )

    assert isinstance(
        error.value.__cause__,
        psycopg.OperationalError,
    )


@pytest.mark.asyncio
async def test_postgres_initialize_wraps_connection_failure():
    store = MarketTimeStore(DATABASE_URL)

    with patch.object(
        store,
        "_connect",
        side_effect=psycopg.OperationalError(
            "Simulated initialization failure"
        ),
    ):
        with pytest.raises(
            MarketTimeStoreError,
            match="Unable to initialize broker-time storage",
        ) as error:
            await store.initialize()

    assert isinstance(
        error.value.__cause__,
        psycopg.OperationalError,
    )


@pytest.mark.asyncio
async def test_postgres_broker_name_change_invalidates_verified_history(
    postgres_store,
):
    store, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    with psycopg.connect(DATABASE_URL) as connection:
        record_id = connection.execute(
            """
            INSERT INTO gold_bot_time_offset_history_v2 (
                source_id,
                effective_from_utc,
                effective_to_utc,
                utc_offset_minutes,
                evidence_reference,
                validation_status
            )
            VALUES (%s, %s, %s, %s, %s, 'VERIFIED')
            RETURNING id
            """,
            (
                source_id,
                start,
                start + timedelta(days=30),
                180,
                "Broker name change test",
            ),
        ).fetchone()[0]

    await store.save_device_config(
        source_id=source_id,
        broker_name="Changed Broker",
        server_name="PostgreSQL Test Server",
        timezone_name="Europe/Athens",
        reported_utc_offset_minutes=120,
    )

    with psycopg.connect(DATABASE_URL) as connection:
        status = connection.execute(
            """
            SELECT validation_status
            FROM gold_bot_time_offset_history_v2
            WHERE id = %s
            """,
            (record_id,),
        ).fetchone()[0]

    assert status == "UNVERIFIED"


@pytest.mark.asyncio
async def test_postgres_server_name_change_invalidates_verified_history(
    postgres_store,
):
    store, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    with psycopg.connect(DATABASE_URL) as connection:
        record_id = connection.execute(
            """
            INSERT INTO gold_bot_time_offset_history_v2 (
                source_id,
                effective_from_utc,
                effective_to_utc,
                utc_offset_minutes,
                evidence_reference,
                validation_status
            )
            VALUES (%s, %s, %s, %s, %s, 'VERIFIED')
            RETURNING id
            """,
            (
                source_id,
                start,
                start + timedelta(days=30),
                180,
                "Server name change test",
            ),
        ).fetchone()[0]

    await store.save_device_config(
        source_id=source_id,
        broker_name="Integration Test Broker",
        server_name="Changed Server",
        timezone_name="Europe/Athens",
        reported_utc_offset_minutes=120,
    )

    with psycopg.connect(DATABASE_URL) as connection:
        status = connection.execute(
            """
            SELECT validation_status
            FROM gold_bot_time_offset_history_v2
            WHERE id = %s
            """,
            (record_id,),
        ).fetchone()[0]

    assert status == "UNVERIFIED"


@pytest.mark.asyncio
async def test_postgres_reported_offset_change_invalidates_verified_history(
    postgres_store,
):
    store, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    with psycopg.connect(DATABASE_URL) as connection:
        record_id = connection.execute(
            """
            INSERT INTO gold_bot_time_offset_history_v2 (
                source_id,
                effective_from_utc,
                effective_to_utc,
                utc_offset_minutes,
                evidence_reference,
                validation_status
            )
            VALUES (%s, %s, %s, %s, %s, 'VERIFIED')
            RETURNING id
            """,
            (
                source_id,
                start,
                start + timedelta(days=30),
                180,
                "Reported offset change test",
            ),
        ).fetchone()[0]

    await store.save_device_config(
        source_id=source_id,
        broker_name="Integration Test Broker",
        server_name="PostgreSQL Test Server",
        timezone_name="Europe/Athens",
        reported_utc_offset_minutes=180,
    )

    with psycopg.connect(DATABASE_URL) as connection:
        status = connection.execute(
            """
            SELECT validation_status
            FROM gold_bot_time_offset_history_v2
            WHERE id = %s
            """,
            (record_id,),
        ).fetchone()[0]

    assert status == "UNVERIFIED"


@pytest.mark.asyncio
async def test_postgres_concurrent_config_updates_isolate_devices(
    postgres_store,
):
    store, first_source_id = postgres_store
    second_source_id = "PG-TEST-" + uuid.uuid4().hex

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    try:
        await store.save_device_config(
            source_id=second_source_id,
            broker_name="Second Broker",
            server_name="Second Server",
            timezone_name="Asia/Baghdad",
            reported_utc_offset_minutes=180,
        )

        with psycopg.connect(DATABASE_URL) as connection:
            first_id = connection.execute(
                """
                INSERT INTO gold_bot_time_offset_history_v2 (
                    source_id,
                    effective_from_utc,
                    effective_to_utc,
                    utc_offset_minutes,
                    evidence_reference,
                    validation_status
                )
                VALUES (%s, %s, %s, %s, %s, 'VERIFIED')
                RETURNING id
                """,
                (
                    first_source_id,
                    start,
                    start + timedelta(days=30),
                    120,
                    "First concurrent device",
                ),
            ).fetchone()[0]

            second_id = connection.execute(
                """
                INSERT INTO gold_bot_time_offset_history_v2 (
                    source_id,
                    effective_from_utc,
                    effective_to_utc,
                    utc_offset_minutes,
                    evidence_reference,
                    validation_status
                )
                VALUES (%s, %s, %s, %s, %s, 'VERIFIED')
                RETURNING id
                """,
                (
                    second_source_id,
                    start,
                    start + timedelta(days=30),
                    180,
                    "Second concurrent device",
                ),
            ).fetchone()[0]

        await asyncio.gather(
            store.save_device_config(
                source_id=first_source_id,
                broker_name="Updated First Broker",
                server_name="PostgreSQL Test Server",
                timezone_name="Europe/Athens",
                reported_utc_offset_minutes=120,
            ),
            store.save_device_config(
                source_id=second_source_id,
                broker_name="Updated Second Broker",
                server_name="Second Server",
                timezone_name="Asia/Baghdad",
                reported_utc_offset_minutes=180,
            ),
        )

        with psycopg.connect(DATABASE_URL) as connection:
            statuses = connection.execute(
                """
                SELECT id, validation_status
                FROM gold_bot_time_offset_history_v2
                WHERE id IN (%s, %s)
                ORDER BY id
                """,
                (first_id, second_id),
            ).fetchall()

        assert statuses == [
            (first_id, "UNVERIFIED"),
            (second_id, "UNVERIFIED"),
        ]

    finally:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                DELETE FROM gold_bot_time_offset_history_v2
                WHERE source_id = %s
                """,
                (second_source_id,),
            )

            connection.execute(
                """
                DELETE FROM gold_bot_device_time_config
                WHERE source_id = %s
                """,
                (second_source_id,),
            )


@pytest.mark.asyncio
async def test_postgres_invalid_source_id_fails_before_connection():
    store = MarketTimeStore(DATABASE_URL)

    with patch.object(store, "_connect") as connect_mock:
        with pytest.raises(
            MarketTimeStoreError,
            match="Invalid device source identifier",
        ):
            await store.save_device_config(
                source_id="INVALID DEVICE ID",
                broker_name="Test Broker",
            )

    connect_mock.assert_not_called()


@pytest.mark.asyncio
async def test_postgres_config_update_rolls_back_if_history_update_fails(
    postgres_store,
):
    store, source_id = postgres_store

    start = datetime(
        2026, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    suffix = uuid.uuid4().hex
    function_name = f"fail_time_history_update_{suffix}"
    trigger_name = f"fail_time_history_trigger_{suffix}"

    with psycopg.connect(DATABASE_URL) as connection:
        record_id = connection.execute(
            """
            INSERT INTO gold_bot_time_offset_history_v2 (
                source_id,
                effective_from_utc,
                effective_to_utc,
                utc_offset_minutes,
                evidence_reference,
                validation_status
            )
            VALUES (%s, %s, %s, %s, %s, 'VERIFIED')
            RETURNING id
            """,
            (
                source_id,
                start,
                start + timedelta(days=30),
                180,
                "Atomic rollback test",
            ),
        ).fetchone()[0]

        connection.execute(
            """
            UPDATE gold_bot_device_time_config
            SET config_status = 'VERIFIED'
            WHERE source_id = %s
            """,
            (source_id,),
        )

        connection.execute(
            f"""
            CREATE FUNCTION {function_name}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'Forced history update failure';
            END;
            $$
            """
        )

        connection.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE UPDATE OF validation_status
            ON gold_bot_time_offset_history_v2
            FOR EACH ROW
            EXECUTE FUNCTION {function_name}()
            """
        )

    try:
        with pytest.raises(
            MarketTimeStoreError,
            match="Unable to save broker-time configuration",
        ):
            await store.save_device_config(
                source_id=source_id,
                broker_name="Changed Broker",
                server_name="Changed Server",
                timezone_name="Asia/Baghdad",
                reported_utc_offset_minutes=180,
            )

        with psycopg.connect(DATABASE_URL) as connection:
            config = connection.execute(
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

            history_status = connection.execute(
                """
                SELECT validation_status
                FROM gold_bot_time_offset_history_v2
                WHERE id = %s
                """,
                (record_id,),
            ).fetchone()[0]

        assert config == (
            "Integration Test Broker",
            "PostgreSQL Test Server",
            "Europe/Athens",
            120,
            "VERIFIED",
        )

        assert history_status == "VERIFIED"

    finally:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                f"""
                DROP TRIGGER IF EXISTS {trigger_name}
                ON gold_bot_time_offset_history_v2
                """
            )

            connection.execute(
                f"""
                DROP FUNCTION IF EXISTS {function_name}()
                """
            )
