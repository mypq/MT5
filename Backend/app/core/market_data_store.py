
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

    Database schema is managed exclusively by Alembic.
    This class validates the schema and persists messages.
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

    _REQUIRED_COLUMNS = {
        "id",
        "source_id",
        "message_type",
        "message_id",
        "sent_at",
        "received_at",
        "body_sha256",
        "payload",
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
        if (
            not isinstance(message_type, str)
            or message_type not in cls._MESSAGE_TYPES
        ):
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

        if not isinstance(sent_at, datetime):
            raise MarketDataStoreError(
                "Message timestamp must be a datetime"
            )

        try:
            offset = sent_at.utcoffset()
        except Exception as exc:
            raise MarketDataStoreError(
                "Message timestamp has invalid timezone information"
            ) from exc

        if sent_at.tzinfo is None or offset is None:
            raise MarketDataStoreError(
                "Message timestamp must include a timezone"
            )

        if not isinstance(payload, dict):
            raise MarketDataStoreError(
                "Market-data payload must be a JSON object"
            )

        if (
            not isinstance(raw_body, bytes)
            or not raw_body
        ):
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
                rows = connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = %s
                    """,
                    (self._TABLE_NAME,),
                ).fetchall()

                actual_columns = {
                    row[0] for row in rows
                }

                problems = []

                if not actual_columns:
                    problems.append(
                        f"Missing table: {self._TABLE_NAME}"
                    )
                else:
                    missing = (
                        self._REQUIRED_COLUMNS
                        - actual_columns
                    )

                    if missing:
                        problems.append(
                            "Missing market-data columns: "
                            + ", ".join(sorted(missing))
                        )

                constraints = connection.execute(
                    """
                    SELECT conname, contype
                    FROM pg_constraint
                    WHERE conrelid = to_regclass(%s)
                    """,
                    (self._TABLE_NAME,),
                ).fetchall()

                actual_constraints = {
                    name: constraint_type
                    for name, constraint_type in constraints
                }

                if actual_constraints.get(
                    "uq_market_data_message"
                ) != "u":
                    problems.append(
                        "Missing market-data unique constraint"
                    )

                if actual_constraints.get(
                    "ck_market_data_message_type"
                ) != "c":
                    problems.append(
                        "Missing market-data message type constraint"
                    )

                indexes = connection.execute(
                    """
                    SELECT
                        idx.relname,
                        i.indisvalid,
                        i.indisready
                    FROM pg_index i
                    JOIN pg_class idx
                      ON idx.oid = i.indexrelid
                    JOIN pg_class tbl
                      ON tbl.oid = i.indrelid
                    JOIN pg_namespace ns
                      ON ns.oid = tbl.relnamespace
                    WHERE ns.nspname = current_schema()
                      AND tbl.relname = %s
                    """,
                    (self._TABLE_NAME,),
                ).fetchall()

                actual_indexes = {
                    name: (is_valid, is_ready)
                    for name, is_valid, is_ready in indexes
                }

                required_indexes = {
                    "idx_market_data_source_received",
                    "idx_market_data_type_sent",
                }

                for index_name in required_indexes:
                    index_state = actual_indexes.get(index_name)

                    if index_state is None:
                        problems.append(
                            f"Missing market-data index: {index_name}"
                        )
                    elif not all(index_state):
                        problems.append(
                            f"Invalid market-data index: {index_name}"
                        )

                if problems:
                    raise MarketDataStoreError(
                        "Database schema is not migrated correctly. "
                        "Run alembic upgrade head. Details: "
                        + "; ".join(problems)
                    )

        except MarketDataStoreError:
            raise
        except Exception as exc:
            raise MarketDataStoreError(
                "Unable to initialize market-data storage"
            ) from exc

    async def initialize(self) -> None:
        """
        Verify Alembic-managed market-data schema.

        This method never creates or alters database objects.
        """
        await asyncio.to_thread(
            self._initialize_sync
        )

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
