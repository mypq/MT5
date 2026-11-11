import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


class MarketDataStoreError(Exception):
    """Raised when market-data persistence fails."""


class MarketDataMessageConflictError(MarketDataStoreError):
    """Raised when a message ID is reused with different content."""


class MarketDataStore:
    """
    PostgreSQL storage for authenticated MT5 market-data messages.

    Stores the validated JSON payload, its exact-body SHA-256
    digest, source identity, message identity, and send time.

    A repeated identical message is treated as an idempotent
    duplicate. Reusing a message ID with different content
    is rejected.
    """

    _TABLE_NAME = "gold_bot_market_data_messages"

    _SOURCE_PATTERN = re.compile(
        r"^[A-Za-z0-9_.:-]{1,128}$"
    )

    _MESSAGE_TYPES = {
        "candles",
        "account",
        "positions",
    }

    def __init__(self, database_url: str) -> None:
        if not isinstance(database_url, str):
            raise MarketDataStoreError(
                "Database URL must be a string"
            )

        normalized_url = database_url.strip()

        if normalized_url.startswith(
            "postgresql+psycopg://"
        ):
            normalized_url = normalized_url.replace(
                "postgresql+psycopg://",
                "postgresql://",
                1,
            )

        if not normalized_url.startswith(
            ("postgresql://", "postgres://")
        ):
            raise MarketDataStoreError(
                "A PostgreSQL connection URL is required"
            )

        self._database_url = normalized_url

    def _connect(self):
        return psycopg.connect(
            self._database_url,
            connect_timeout=5,
            autocommit=False,
        )

    @classmethod
    def _validate_message(
        cls,
        *,
        message_type: str,
        source_id: str,
        message_id: UUID,
        sent_at: datetime,
        payload: dict[str, Any],
        raw_body: bytes,
    ) -> str:
        if message_type not in cls._MESSAGE_TYPES:
            raise MarketDataStoreError(
                "Unsupported market-data message type"
            )

        if (
            not isinstance(source_id, str)
            or not cls._SOURCE_PATTERN.fullmatch(source_id)
        ):
            raise MarketDataStoreError(
                "Invalid market-data source identifier"
            )

        if not isinstance(message_id, UUID):
            raise MarketDataStoreError(
                "Message ID must be a UUID"
            )

        if (
            not isinstance(sent_at, datetime)
            or sent_at.tzinfo is None
            or sent_at.utcoffset() is None
        ):
            raise MarketDataStoreError(
                "Message timestamp must include a timezone"
            )

        if not isinstance(payload, dict):
            raise MarketDataStoreError(
                "Market-data payload must be a JSON object"
            )

        if not isinstance(raw_body, bytes) or not raw_body:
            raise MarketDataStoreError(
                "Exact raw request body is required"
            )

        if payload.get("source_id") != source_id:
            raise MarketDataStoreError(
                "Payload source identifier mismatch"
            )

        if str(payload.get("message_id")) != str(message_id):
            raise MarketDataStoreError(
                "Payload message identifier mismatch"
            )

        return hashlib.sha256(raw_body).hexdigest()

    def _initialize_sync(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS
                    gold_bot_market_data_messages (
                        id BIGINT GENERATED ALWAYS AS IDENTITY
                            PRIMARY KEY,
                        source_id VARCHAR(128) NOT NULL,
                        message_type VARCHAR(16) NOT NULL,
                        message_id UUID NOT NULL,
                        sent_at TIMESTAMPTZ NOT NULL,
                        received_at TIMESTAMPTZ NOT NULL
                            DEFAULT CURRENT_TIMESTAMP,
                        body_sha256 CHAR(64) NOT NULL,
                        payload JSONB NOT NULL,
                        CONSTRAINT uq_market_data_message
                            UNIQUE (
                                source_id,
                                message_type,
                                message_id
                            ),
                        CONSTRAINT ck_market_data_message_type
                            CHECK (
                                message_type IN (
                                    'candles',
                                    'account',
                                    'positions'
                                )
                            )
                    )
                    """
                )

                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                    idx_market_data_source_received
                    ON gold_bot_market_data_messages (
                        source_id,
                        received_at DESC
                    )
                    """
                )

                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                    idx_market_data_type_sent
                    ON gold_bot_market_data_messages (
                        message_type,
                        sent_at DESC
                    )
                    """
                )

        except Exception as exc:
            raise MarketDataStoreError(
                "Unable to initialize market-data storage"
            ) from exc

    async def initialize(self) -> None:
        """Create the market-data table and indexes."""
        await asyncio.to_thread(self._initialize_sync)

    def _store_message_sync(
        self,
        *,
        source_id: str,
        message_type: str,
        message_id: UUID,
        sent_at: datetime,
        payload: dict[str, Any],
        body_sha256: str,
    ) -> bool:
        try:
            with self._connect() as connection:
                inserted = connection.execute(
                    """
                    INSERT INTO gold_bot_market_data_messages (
                        source_id,
                        message_type,
                        message_id,
                        sent_at,
                        body_sha256,
                        payload
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (
                        source_id,
                        message_type,
                        message_id
                    )
                    DO NOTHING
                    RETURNING id
                    """,
                    (
                        source_id,
                        message_type,
                        message_id,
                        sent_at.astimezone(timezone.utc),
                        body_sha256,
                        Jsonb(payload),
                    ),
                ).fetchone()

                if inserted is not None:
                    return True

                existing = connection.execute(
                    """
                    SELECT body_sha256
                    FROM gold_bot_market_data_messages
                    WHERE source_id = %s
                      AND message_type = %s
                      AND message_id = %s
                    """,
                    (
                        source_id,
                        message_type,
                        message_id,
                    ),
                ).fetchone()

                if existing is None:
                    raise MarketDataStoreError(
                        "Unable to verify existing message"
                    )

                if existing[0].strip() != body_sha256:
                    raise MarketDataMessageConflictError(
                        "Message ID was reused with different content"
                    )

                return False

        except MarketDataMessageConflictError:
            raise
        except MarketDataStoreError:
            raise
        except Exception as exc:
            raise MarketDataStoreError(
                "Unable to persist market-data message"
            ) from exc

    async def store_message(
        self,
        *,
        message_type: str,
        source_id: str,
        message_id: UUID,
        sent_at: datetime,
        payload: dict[str, Any],
        raw_body: bytes,
    ) -> bool:
        """
        Persist one validated market-data message.

        Returns:
            True: a new message was stored.
            False: the exact same message was already stored.

        Raises:
            MarketDataMessageConflictError:
                The same message ID was reused with different bytes.
            MarketDataStoreError:
                Validation or database operation failed.
        """
        body_sha256 = self._validate_message(
            message_type=message_type,
            source_id=source_id,
            message_id=message_id,
            sent_at=sent_at,
            payload=payload,
            raw_body=raw_body,
        )

        return await asyncio.to_thread(
            self._store_message_sync,
            source_id=source_id,
            message_type=message_type,
            message_id=message_id,
            sent_at=sent_at,
            payload=payload,
            body_sha256=body_sha256,
        )
