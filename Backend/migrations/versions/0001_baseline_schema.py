
"""Baseline database schema.

Revision ID: 0001_baseline
Revises:
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _assert_columns(table_name: str, expected: dict[str, str]) -> None:
    connection = op.get_bind()

    rows = connection.execute(
        sa.text(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table_name
            """
        ),
        {"table_name": table_name},
    ).fetchall()

    actual = {row[0]: row[1] for row in rows}

    if not actual:
        raise RuntimeError(
            f"Migration validation failed: table {table_name} is missing."
        )

    for column_name, expected_type in expected.items():
        actual_type = actual.get(column_name)

        if actual_type != expected_type:
            raise RuntimeError(
                f"Schema mismatch in {table_name}.{column_name}: "
                f"expected {expected_type}, got {actual_type}."
            )


def _create_schema() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gold_bot_trusted_devices (
            source_id VARCHAR(128) PRIMARY KEY,
            encrypted_secret TEXT NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            revoked_at TIMESTAMPTZ NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gold_bot_market_data_nonces (
            source_id VARCHAR(128) NOT NULL,
            nonce VARCHAR(128) NOT NULL,
            expires_at BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_id, nonce)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gold_bot_market_data_messages (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source_id VARCHAR(128) NOT NULL,
            message_type VARCHAR(16) NOT NULL,
            message_id UUID NOT NULL,
            sent_at TIMESTAMPTZ NOT NULL,
            received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            body_sha256 CHAR(64) NOT NULL,
            payload JSONB NOT NULL,

            CONSTRAINT uq_market_data_message
                UNIQUE (source_id, message_type, message_id),

            CONSTRAINT ck_market_data_message_type
                CHECK (
                    message_type IN ('candles', 'account', 'positions')
                )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gold_bot_device_time_config (
            source_id VARCHAR(128) PRIMARY KEY,
            broker_name VARCHAR(128),
            server_name VARCHAR(128),
            timezone_name VARCHAR(64),
            time_source VARCHAR(32) NOT NULL DEFAULT 'MT5_SERVER_TIME',
            reported_utc_offset_minutes SMALLINT,
            config_status VARCHAR(16) NOT NULL DEFAULT 'UNVERIFIED',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

            CONSTRAINT ck_device_time_config_offset
                CHECK (
                    reported_utc_offset_minutes IS NULL
                    OR reported_utc_offset_minutes BETWEEN -720 AND 840
                ),

            CONSTRAINT ck_device_time_config_status
                CHECK (
                    config_status IN ('UNVERIFIED', 'VERIFIED', 'REJECTED')
                )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gold_bot_time_offset_history (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

            source_id VARCHAR(128) NOT NULL
                REFERENCES gold_bot_device_time_config(source_id)
                ON DELETE RESTRICT,

            broker_local_from TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            broker_local_to TIMESTAMP WITHOUT TIME ZONE,
            utc_offset_minutes SMALLINT NOT NULL,
            proof_source VARCHAR(256) NOT NULL,
            is_dst BOOLEAN,

            validation_status VARCHAR(16) NOT NULL DEFAULT 'UNVERIFIED',
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

            CONSTRAINT ck_time_offset_range
                CHECK (utc_offset_minutes BETWEEN -720 AND 840),

            CONSTRAINT ck_time_offset_period
                CHECK (
                    broker_local_to IS NULL
                    OR broker_local_to > broker_local_from
                ),

            CONSTRAINT ck_time_offset_status
                CHECK (
                    validation_status IN ('UNVERIFIED', 'VERIFIED', 'REJECTED')
                )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gold_bot_time_offset_history_v2 (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

            source_id VARCHAR(128) NOT NULL
                REFERENCES gold_bot_device_time_config(source_id)
                ON DELETE RESTRICT,

            effective_from_utc TIMESTAMPTZ NOT NULL,
            effective_to_utc TIMESTAMPTZ,
            utc_offset_minutes SMALLINT NOT NULL,
            evidence_reference VARCHAR(512) NOT NULL,
            is_dst BOOLEAN,

            validation_status VARCHAR(16) NOT NULL DEFAULT 'UNVERIFIED',
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

            CONSTRAINT ck_time_offset_v2_range
                CHECK (utc_offset_minutes BETWEEN -720 AND 840),

            CONSTRAINT ck_time_offset_v2_period
                CHECK (
                    effective_to_utc IS NULL
                    OR effective_to_utc > effective_from_utc
                ),

            CONSTRAINT ck_time_offset_v2_status
                CHECK (
                    validation_status IN ('UNVERIFIED', 'VERIFIED', 'REJECTED')
                )
        )
        """
    )


def _create_indexes() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trusted_devices_active
        ON gold_bot_trusted_devices (is_active)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_data_nonces_expires_at
        ON gold_bot_market_data_nonces (expires_at)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_data_source_received
        ON gold_bot_market_data_messages (source_id, received_at DESC)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_data_type_sent
        ON gold_bot_market_data_messages (message_type, sent_at DESC)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_time_offset_history_source_period
        ON gold_bot_time_offset_history (source_id, broker_local_from)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_time_offset_history_v2_source_period
        ON gold_bot_time_offset_history_v2 (source_id, effective_from_utc)
        """
    )


def _validate_existing_schema() -> None:
    expected_columns = {
        "gold_bot_trusted_devices": {
            "source_id": "character varying",
            "encrypted_secret": "text",
            "is_active": "boolean",
            "created_at": "timestamp with time zone",
            "updated_at": "timestamp with time zone",
            "revoked_at": "timestamp with time zone",
        },
        "gold_bot_market_data_nonces": {
            "source_id": "character varying",
            "nonce": "character varying",
            "expires_at": "bigint",
            "created_at": "timestamp with time zone",
        },
        "gold_bot_market_data_messages": {
            "id": "bigint",
            "source_id": "character varying",
            "message_type": "character varying",
            "message_id": "uuid",
            "sent_at": "timestamp with time zone",
            "received_at": "timestamp with time zone",
            "body_sha256": "character",
            "payload": "jsonb",
        },
        "gold_bot_device_time_config": {
            "source_id": "character varying",
            "broker_name": "character varying",
            "server_name": "character varying",
            "timezone_name": "character varying",
            "time_source": "character varying",
            "reported_utc_offset_minutes": "smallint",
            "config_status": "character varying",
            "updated_at": "timestamp with time zone",
        },
        "gold_bot_time_offset_history": {
            "id": "bigint",
            "source_id": "character varying",
            "broker_local_from": "timestamp without time zone",
            "broker_local_to": "timestamp without time zone",
            "utc_offset_minutes": "smallint",
            "proof_source": "character varying",
            "is_dst": "boolean",
            "validation_status": "character varying",
            "recorded_at": "timestamp with time zone",
        },
        "gold_bot_time_offset_history_v2": {
            "id": "bigint",
            "source_id": "character varying",
            "effective_from_utc": "timestamp with time zone",
            "effective_to_utc": "timestamp with time zone",
            "utc_offset_minutes": "smallint",
            "evidence_reference": "character varying",
            "is_dst": "boolean",
            "validation_status": "character varying",
            "recorded_at": "timestamp with time zone",
        },
    }

    for table_name, columns in expected_columns.items():
        _assert_columns(table_name, columns)


def _create_time_exclusion_constraint() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ex_time_offset_v2_no_overlap'
                  AND conrelid =
                      'gold_bot_time_offset_history_v2'::regclass
            ) THEN
                ALTER TABLE gold_bot_time_offset_history_v2
                ADD CONSTRAINT ex_time_offset_v2_no_overlap
                EXCLUDE USING gist (
                    source_id WITH =,
                    tstzrange(
                        effective_from_utc,
                        effective_to_utc,
                        '[)'
                    ) WITH &&
                )
                WHERE (validation_status <> 'REJECTED');
            END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    _create_schema()
    _validate_existing_schema()
    _create_indexes()
    _create_time_exclusion_constraint()


def downgrade() -> None:
    # Intentionally non-destructive.
    # This baseline may adopt tables that existed before Alembic.
    # Dropping them could destroy production data.
    pass
