
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from alembic import command
from alembic.config import Config


DATABASE_URL = os.getenv("MARKET_TIME_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="PostgreSQL integration database is not configured",
)


def test_baseline_migration_preserves_existing_data(monkeypatch):
    source_id = "MIGRATION-TEST-" + uuid.uuid4().hex

    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    config = Config("alembic.ini")

    start = datetime(
        2040, 1, 1, 0, 0,
        tzinfo=timezone.utc,
    )

    legacy_start = datetime(2020, 10, 25, 1, 0, 0)
    legacy_end = legacy_start + timedelta(hours=1)

    try:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                INSERT INTO gold_bot_device_time_config (
                    source_id,
                    broker_name,
                    server_name,
                    timezone_name,
                    reported_utc_offset_minutes,
                    config_status
                )
                VALUES (%s, %s, %s, %s, %s, 'VERIFIED')
                """,
                (
                    source_id,
                    "Migration Test Broker",
                    "Migration Test Server",
                    "Asia/Baghdad",
                    180,
                ),
            )

            connection.execute(
                """
                INSERT INTO gold_bot_time_offset_history (
                    source_id,
                    broker_local_from,
                    broker_local_to,
                    utc_offset_minutes,
                    proof_source,
                    is_dst,
                    validation_status
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'VERIFIED')
                """,
                (
                    source_id,
                    legacy_start,
                    legacy_end,
                    180,
                    "Migration preservation test",
                    False,
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
                    is_dst,
                    validation_status
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'VERIFIED')
                """,
                (
                    source_id,
                    start,
                    start + timedelta(days=30),
                    180,
                    "Verified migration preservation",
                    False,
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
                    is_dst,
                    validation_status
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'REJECTED')
                """,
                (
                    source_id,
                    start + timedelta(days=60),
                    start + timedelta(days=90),
                    180,
                    "Rejected migration preservation",
                    True,
                ),
            )

        # Remove only Alembic's revision marker.
        # The baseline downgrade intentionally preserves all data.
        command.downgrade(config, "base")

        # Re-adopt the existing database schema.
        command.upgrade(config, "head")

        with psycopg.connect(DATABASE_URL) as connection:
            device = connection.execute(
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

            legacy = connection.execute(
                """
                SELECT
                    broker_local_from,
                    broker_local_to,
                    utc_offset_minutes,
                    proof_source,
                    is_dst,
                    validation_status
                FROM gold_bot_time_offset_history
                WHERE source_id = %s
                """,
                (source_id,),
            ).fetchone()

            v2 = connection.execute(
                """
                SELECT
                    effective_from_utc,
                    effective_to_utc,
                    evidence_reference,
                    validation_status
                FROM gold_bot_time_offset_history_v2
                WHERE source_id = %s
                ORDER BY effective_from_utc
                """,
                (source_id,),
            ).fetchall()

            revision = connection.execute(
                """
                SELECT version_num
                FROM alembic_version
                """
            ).fetchone()

        assert device == (
            "Migration Test Broker",
            "Migration Test Server",
            "Asia/Baghdad",
            180,
            "VERIFIED",
        )

        assert legacy == (
            legacy_start,
            legacy_end,
            180,
            "Migration preservation test",
            False,
            "VERIFIED",
        )

        assert len(v2) == 2
        assert v2[0][2:] == (
            "Verified migration preservation",
            "VERIFIED",
        )
        assert v2[1][2:] == (
            "Rejected migration preservation",
            "REJECTED",
        )

        assert revision is not None
        assert revision[0] == "0001_baseline"

    finally:
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
