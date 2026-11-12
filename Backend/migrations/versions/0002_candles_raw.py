
"""Add append-only raw candle storage.

Revision ID: 0002_candles_raw
Revises: 0001_baseline
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0002_candles_raw"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE gold_bot_candles_raw (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

            batch_id BIGINT NOT NULL
                REFERENCES gold_bot_market_data_messages(id)
                ON DELETE RESTRICT,

            source_id VARCHAR(128) NOT NULL,
            symbol VARCHAR(16) NOT NULL,
            timeframe VARCHAR(8) NOT NULL,

            broker_local_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            seq_index INTEGER NOT NULL CHECK (seq_index >= 0),

            server_name VARCHAR(128),

            received_utc TIMESTAMPTZ NOT NULL
                DEFAULT clock_timestamp(),

            payload_hash CHAR(64) NOT NULL,
            raw_candle JSONB NOT NULL,

            CONSTRAINT uq_raw_candle_batch_sequence
                UNIQUE (batch_id, seq_index),

            CONSTRAINT ck_raw_candle_symbol
                CHECK (symbol = 'XAUUSD'),

            CONSTRAINT ck_raw_candle_timeframe
                CHECK (
                    timeframe IN (
                        'M1', 'M5', 'M15', 'M30',
                        'H1', 'H4', 'D1', 'W1', 'MN1'
                    )
                )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_raw_candles_source_market_received
        ON gold_bot_candles_raw (
            source_id,
            symbol,
            timeframe,
            received_utc DESC
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_raw_candles_batch_sequence
        ON gold_bot_candles_raw (batch_id, seq_index)
        """
    )

    op.execute(
        """
        CREATE FUNCTION gold_bot_reject_raw_candle_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'gold_bot_candles_raw is append-only; UPDATE and DELETE are forbidden';
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_raw_candles_append_only
        BEFORE UPDATE OR DELETE ON gold_bot_candles_raw
        FOR EACH ROW
        EXECUTE FUNCTION gold_bot_reject_raw_candle_mutation()
        """
    )


def downgrade() -> None:
    # Intentionally non-destructive.
    # Raw market history must never be dropped.
    pass
