
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


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())


def _assert_columns(
    table_name: str,
    expected: dict[str, dict],
) -> None:
    connection = op.get_bind()

    rows = connection.execute(
        sa.text(
            """
            SELECT
                column_name,
                data_type,
                is_nullable,
                character_maximum_length,
                column_default,
                is_identity,
                identity_generation
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table_name
            """
        ),
        {"table_name": table_name},
    ).fetchall()

    actual = {row.column_name: row for row in rows}

    if not actual:
        raise RuntimeError(
            f"Migration validation failed: "
            f"table {table_name} is missing."
        )

    for column_name, specification in expected.items():
        column = actual.get(column_name)

        if column is None:
            raise RuntimeError(
                f"Missing column: {table_name}.{column_name}"
            )

        if column.data_type != specification["type"]:
            raise RuntimeError(
                f"Wrong type for {table_name}.{column_name}: "
                f"expected {specification['type']}, "
                f"got {column.data_type}."
            )

        expected_nullable = specification.get("nullable", False)
        actual_nullable = column.is_nullable == "YES"

        if actual_nullable != expected_nullable:
            raise RuntimeError(
                f"Nullability mismatch: "
                f"{table_name}.{column_name}"
            )

        expected_length = specification.get("length")

        if (
            expected_length is not None
            and column.character_maximum_length != expected_length
        ):
            raise RuntimeError(
                f"Length mismatch: {table_name}.{column_name}"
            )

        if specification.get("identity"):
            if column.is_identity != "YES":
                raise RuntimeError(
                    f"Identity property missing: "
                    f"{table_name}.{column_name}"
                )

            if column.identity_generation != "ALWAYS":
                raise RuntimeError(
                    f"Wrong identity generation: "
                    f"{table_name}.{column_name}"
                )

        default_fragment = specification.get("default")

        if default_fragment:
            actual_default = _normalize(column.column_default or "")

            if default_fragment not in actual_default:
                raise RuntimeError(
                    f"Default value mismatch: "
                    f"{table_name}.{column_name}"
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
    statements = [
        """
        CREATE INDEX IF NOT EXISTS idx_trusted_devices_active
        ON gold_bot_trusted_devices (is_active)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_market_data_nonces_expires_at
        ON gold_bot_market_data_nonces (expires_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_market_data_source_received
        ON gold_bot_market_data_messages (source_id, received_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_market_data_type_sent
        ON gold_bot_market_data_messages (message_type, sent_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_time_offset_history_source_period
        ON gold_bot_time_offset_history (source_id, broker_local_from)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_time_offset_history_v2_source_period
        ON gold_bot_time_offset_history_v2 (source_id, effective_from_utc)
        """,
    ]

    for statement in statements:
        op.execute(statement)


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


def _validate_constraints() -> None:
    connection = op.get_bind()

    expected = {
        "gold_bot_trusted_devices": {
            "gold_bot_trusted_devices_pkey": "p",
        },
        "gold_bot_market_data_nonces": {
            "gold_bot_market_data_nonces_pkey": "p",
        },
        "gold_bot_market_data_messages": {
            "gold_bot_market_data_messages_pkey": "p",
            "uq_market_data_message": "u",
            "ck_market_data_message_type": "c",
        },
        "gold_bot_device_time_config": {
            "gold_bot_device_time_config_pkey": "p",
            "ck_device_time_config_offset": "c",
            "ck_device_time_config_status": "c",
        },
        "gold_bot_time_offset_history": {
            "gold_bot_time_offset_history_pkey": "p",
            "gold_bot_time_offset_history_source_id_fkey": "f",
            "ck_time_offset_range": "c",
            "ck_time_offset_period": "c",
            "ck_time_offset_status": "c",
        },
        "gold_bot_time_offset_history_v2": {
            "gold_bot_time_offset_history_v2_pkey": "p",
            "gold_bot_time_offset_history_v2_source_id_fkey": "f",
            "ck_time_offset_v2_range": "c",
            "ck_time_offset_v2_period": "c",
            "ck_time_offset_v2_status": "c",
            "ex_time_offset_v2_no_overlap": "x",
        },
    }

    for table_name, required in expected.items():
        rows = connection.execute(
            sa.text(
                """
                SELECT
                    conname,
                    contype,
                    pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conrelid = to_regclass(:table_name)
                """
            ),
            {"table_name": table_name},
        ).fetchall()

        actual = {
            row.conname: (row.contype, row[2])
            for row in rows
        }

        for constraint_name, expected_type in required.items():
            constraint = actual.get(constraint_name)

            if constraint is None:
                raise RuntimeError(
                    f"Missing constraint: "
                    f"{table_name}.{constraint_name}"
                )

            if constraint[0] != expected_type:
                raise RuntimeError(
                    f"Wrong constraint type: "
                    f"{table_name}.{constraint_name}"
                )

            definition = _normalize(constraint[1])

            if constraint_name.endswith("_source_id_fkey"):
                if (
                    "foreign key (source_id)" not in definition
                    or "references gold_bot_device_time_config" not in definition
                    or "on delete restrict" not in definition
                ):
                    raise RuntimeError(
                        f"Foreign key definition mismatch: "
                        f"{constraint_name}"
                    )

            if constraint_name == "uq_market_data_message":
                for fragment in (
                    "unique",
                    "source_id",
                    "message_type",
                    "message_id",
                ):
                    if fragment not in definition:
                        raise RuntimeError(
                            "Market-data uniqueness constraint mismatch."
                        )

            if constraint_name == "ex_time_offset_v2_no_overlap":
                for fragment in (
                    "exclude using gist",
                    "source_id with =",
                    "tstzrange",
                    "with &&",
                    "validation_status",
                    "rejected",
                ):
                    if fragment not in definition:
                        raise RuntimeError(
                            "UTC overlap exclusion constraint mismatch."
                        )


def _validate_indexes() -> None:
    connection = op.get_bind()

    expected = {
        "idx_trusted_devices_active": (
            "gold_bot_trusted_devices",
            ("is_active",),
        ),
        "idx_market_data_nonces_expires_at": (
            "gold_bot_market_data_nonces",
            ("expires_at",),
        ),
        "idx_market_data_source_received": (
            "gold_bot_market_data_messages",
            ("source_id", "received_at desc"),
        ),
        "idx_market_data_type_sent": (
            "gold_bot_market_data_messages",
            ("message_type", "sent_at desc"),
        ),
        "idx_time_offset_history_source_period": (
            "gold_bot_time_offset_history",
            ("source_id", "broker_local_from"),
        ),
        "idx_time_offset_history_v2_source_period": (
            "gold_bot_time_offset_history_v2",
            ("source_id", "effective_from_utc"),
        ),
    }

    for index_name, (table_name, fragments) in expected.items():
        row = connection.execute(
            sa.text(
                """
                SELECT
                    i.indisvalid,
                    i.indisready,
                    pg_get_indexdef(i.indexrelid)
                FROM pg_index i
                JOIN pg_class idx ON idx.oid = i.indexrelid
                JOIN pg_class tbl ON tbl.oid = i.indrelid
                JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
                WHERE ns.nspname = current_schema()
                  AND idx.relname = :index_name
                  AND tbl.relname = :table_name
                """
            ),
            {
                "index_name": index_name,
                "table_name": table_name,
            },
        ).fetchone()

        if row is None:
            raise RuntimeError(f"Missing index: {index_name}")

        if not row.indisvalid or not row.indisready:
            raise RuntimeError(f"Invalid or incomplete index: {index_name}")

        definition = _normalize(row[2])

        for fragment in fragments:
            if fragment not in definition:
                raise RuntimeError(
                    f"Index definition mismatch: {index_name}"
                )


def _validate_existing_schema() -> None:
    expected_columns = {
        "gold_bot_trusted_devices": {
            "source_id": {"type": "character varying", "length": 128},
            "encrypted_secret": {"type": "text"},
            "is_active": {"type": "boolean", "default": "true"},
            "created_at": {"type": "timestamp with time zone", "default": "current_timestamp"},
            "updated_at": {"type": "timestamp with time zone", "default": "current_timestamp"},
            "revoked_at": {"type": "timestamp with time zone", "nullable": True},
        },
        "gold_bot_market_data_nonces": {
            "source_id": {"type": "character varying", "length": 128},
            "nonce": {"type": "character varying", "length": 128},
            "expires_at": {"type": "bigint"},
            "created_at": {"type": "timestamp with time zone", "default": "current_timestamp"},
        },
        "gold_bot_market_data_messages": {
            "id": {"type": "bigint", "identity": True},
            "source_id": {"type": "character varying", "length": 128},
            "message_type": {"type": "character varying", "length": 16},
            "message_id": {"type": "uuid"},
            "sent_at": {"type": "timestamp with time zone"},
            "received_at": {"type": "timestamp with time zone", "default": "current_timestamp"},
            "body_sha256": {"type": "character", "length": 64},
            "payload": {"type": "jsonb"},
        },
        "gold_bot_device_time_config": {
            "source_id": {"type": "character varying", "length": 128},
            "broker_name": {"type": "character varying", "length": 128, "nullable": True},
            "server_name": {"type": "character varying", "length": 128, "nullable": True},
            "timezone_name": {"type": "character varying", "length": 64, "nullable": True},
            "time_source": {"type": "character varying", "length": 32, "default": "mt5_server_time"},
            "reported_utc_offset_minutes": {"type": "smallint", "nullable": True},
            "config_status": {"type": "character varying", "length": 16, "default": "unverified"},
            "updated_at": {"type": "timestamp with time zone", "default": "current_timestamp"},
        },
        "gold_bot_time_offset_history": {
            "id": {"type": "bigint", "identity": True},
            "source_id": {"type": "character varying", "length": 128},
            "broker_local_from": {"type": "timestamp without time zone"},
            "broker_local_to": {"type": "timestamp without time zone", "nullable": True},
            "utc_offset_minutes": {"type": "smallint"},
            "proof_source": {"type": "character varying", "length": 256},
            "is_dst": {"type": "boolean", "nullable": True},
            "validation_status": {"type": "character varying", "length": 16, "default": "unverified"},
            "recorded_at": {"type": "timestamp with time zone", "default": "current_timestamp"},
        },
        "gold_bot_time_offset_history_v2": {
            "id": {"type": "bigint", "identity": True},
            "source_id": {"type": "character varying", "length": 128},
            "effective_from_utc": {"type": "timestamp with time zone"},
            "effective_to_utc": {"type": "timestamp with time zone", "nullable": True},
            "utc_offset_minutes": {"type": "smallint"},
            "evidence_reference": {"type": "character varying", "length": 512},
            "is_dst": {"type": "boolean", "nullable": True},
            "validation_status": {"type": "character varying", "length": 16, "default": "unverified"},
            "recorded_at": {"type": "timestamp with time zone", "default": "current_timestamp"},
        },
    }

    for table_name, columns in expected_columns.items():
        _assert_columns(table_name, columns)

    _validate_constraints()
    _validate_indexes()


def upgrade() -> None:
    _create_schema()
    _create_time_exclusion_constraint()
    _create_indexes()
    _validate_existing_schema()


def downgrade() -> None:
    # Intentionally non-destructive.
    # Existing production data must never be dropped by this baseline.
    pass
