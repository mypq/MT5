
import asyncio
import hashlib
import json
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

    V2 candle batches are stored atomically:
    - One parent market-data message.
    - One append-only raw row per candle.
    - No broker-local-time to UTC conversion during ingestion.
    """

    _TABLE_NAME = "gold_bot_market_data_messages"
    _RAW_TABLE_NAME = "gold_bot_candles_raw"

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

    _RAW_REQUIRED_COLUMNS = {
        "id",
        "batch_id",
        "source_id",
        "symbol",
        "timeframe",
        "broker_local_time",
        "seq_index",
        "server_name",
        "received_utc",
        "payload_hash",
        "raw_candle",
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

    @staticmethod
    def _is_v2_candle_batch(
        message_type: str,
        payload: dict[str, Any],
    ) -> bool:
        return (
            message_type == "candles"
            and payload.get("schema_version") == 2
        )

    @staticmethod
    def _canonical_candle_hash(
        candle: dict[str, Any],
    ) -> str:
        try:
            canonical = json.dumps(
                candle,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise MarketDataStoreError(
                "Unable to serialize raw candle"
            ) from exc

        return hashlib.sha256(canonical).hexdigest()

    @classmethod
    def _validate_v2_payload(
        cls,
        payload: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str | None]:
        candles = payload.get("candles")

        if not isinstance(candles, list) or not candles:
            raise MarketDataStoreError(
                "V2 candle batch must contain candles"
            )

        symbol = payload.get("symbol")
        timeframe = payload.get("timeframe")

        if symbol != "XAUUSD":
            raise MarketDataStoreError(
                "Unsupported raw candle symbol"
            )

        if isinstance(timeframe, dict):
            timeframe = timeframe.get("value")

        allowed_timeframes = {
            "M1",
            "M5",
            "M15",
            "M30",
            "H1",
            "H4",
            "D1",
            "W1",
            "MN1",
        }

        if timeframe not in allowed_timeframes:
            raise MarketDataStoreError(
                "Unsupported raw candle timeframe"
            )

        broker_time = payload.get("broker_time") or {}

        if not isinstance(broker_time, dict):
            raise MarketDataStoreError(
                "Invalid broker-time metadata"
            )

        server_name = broker_time.get("server_name")

        if server_name is not None:
            if (
                not isinstance(server_name, str)
                or not server_name.strip()
                or len(server_name) > 128
            ):
                raise MarketDataStoreError(
                    "Invalid broker server name"
                )

            server_name = server_name.strip()

        for candle in candles:
            if not isinstance(candle, dict):
                raise MarketDataStoreError(
                    "Invalid raw candle object"
                )

            local_time = candle.get("broker_local_time")

            if not isinstance(local_time, str):
                raise MarketDataStoreError(
                    "Raw candle broker-local time is missing"
                )

            try:
                parsed_time = datetime.strptime(
                    local_time,
                    "%Y-%m-%dT%H:%M:%S",
                )
            except ValueError as exc:
                raise MarketDataStoreError(
                    "Invalid raw candle broker-local time"
                ) from exc

            if parsed_time.strftime(
                "%Y-%m-%dT%H:%M:%S"
            ) != local_time:
                raise MarketDataStoreError(
                    "Raw candle broker-local time is not canonical"
                )

        return candles, server_name

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

                raw_rows = connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = %s
                    """,
                    (self._RAW_TABLE_NAME,),
                ).fetchall()

                actual_raw_columns = {
                    row[0] for row in raw_rows
                }

                if not actual_raw_columns:
                    problems.append(
                        f"Missing table: {self._RAW_TABLE_NAME}"
                    )
                else:
                    missing_raw = (
                        self._RAW_REQUIRED_COLUMNS
                        - actual_raw_columns
                    )

                    if missing_raw:
                        problems.append(
                            "Missing raw-candle columns: "
                            + ", ".join(sorted(missing_raw))
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

                raw_constraints = connection.execute(
                    """
                    SELECT conname, contype
                    FROM pg_constraint
                    WHERE conrelid = to_regclass(%s)
                    """,
                    (self._RAW_TABLE_NAME,),
                ).fetchall()

                actual_raw_constraints = {
                    name: constraint_type
                    for name, constraint_type in raw_constraints
                }

                if actual_raw_constraints.get(
                    "uq_raw_candle_batch_sequence"
                ) != "u":
                    problems.append(
                        "Missing raw-candle batch sequence constraint"
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

                raw_indexes = connection.execute(
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
                    (self._RAW_TABLE_NAME,),
                ).fetchall()

                actual_raw_indexes = {
                    name: (is_valid, is_ready)
                    for name, is_valid, is_ready in raw_indexes
                }

                required_raw_indexes = {
                    "idx_raw_candles_source_market_received",
                    "idx_raw_candles_batch_sequence",
                }

                for index_name in required_raw_indexes:
                    index_state = actual_raw_indexes.get(index_name)

                    if index_state is None:
                        problems.append(
                            f"Missing raw-candle index: {index_name}"
                        )
                    elif not all(index_state):
                        problems.append(
                            f"Invalid raw-candle index: {index_name}"
                        )

                trigger = connection.execute(
                    """
                    SELECT t.tgname, t.tgenabled
                    FROM pg_trigger t
                    JOIN pg_class tbl
                      ON tbl.oid = t.tgrelid
                    JOIN pg_namespace ns
                      ON ns.oid = tbl.relnamespace
                    WHERE ns.nspname = current_schema()
                      AND tbl.relname = %s
                      AND NOT t.tgisinternal
                    """,
                    (self._RAW_TABLE_NAME,),
                ).fetchall()

                trigger_state = {
                    name: enabled
                    for name, enabled in trigger
                }

                if trigger_state.get(
                    "trg_raw_candles_append_only"
                ) not in {"O", "A"}:
                    problems.append(
                        "Missing or disabled raw-candle append-only trigger"
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
            v2_candles = None
            server_name = None

            if self._is_v2_candle_batch(
                message_type,
                payload,
            ):
                v2_candles, server_name = (
                    self._validate_v2_payload(payload)
                )

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

                if inserted is None:
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

                batch_id = inserted[0]

                if v2_candles is not None:
                    timeframe = payload["timeframe"]
                    symbol = payload["symbol"]

                    for seq_index, candle in enumerate(v2_candles):
                        local_time = datetime.strptime(
                            candle["broker_local_time"],
                            "%Y-%m-%dT%H:%M:%S",
                        )

                        candle_hash = (
                            self._canonical_candle_hash(candle)
                        )

                        connection.execute(
                            """
                            INSERT INTO gold_bot_candles_raw (
                                batch_id,
                                source_id,
                                symbol,
                                timeframe,
                                broker_local_time,
                                seq_index,
                                server_name,
                                payload_hash,
                                raw_candle
                            )
                            VALUES (
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s
                            )
                            """,
                            (
                                batch_id,
                                source_id,
                                symbol,
                                timeframe,
                                local_time,
                                seq_index,
                                server_name,
                                candle_hash,
                                Jsonb(candle),
                            ),
                        )

                return True

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

        V2 candle batches and their raw candle rows are committed
        in one database transaction.

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
