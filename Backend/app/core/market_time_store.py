import asyncio
import re
from datetime import datetime, timezone

import psycopg


class MarketTimeStoreError(Exception):
    """Broker-time storage error."""


class MarketTimeStore:
    """
    Stores per-device broker-time metadata and offset history.

    Reported offsets remain unverified. This module does not convert
    broker-local candle timestamps into UTC.
    """

    _SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
    _TIMEZONE_PATTERN = re.compile(
        r"^[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+)*$"
    )

    def __init__(self, database_url: str) -> None:
        if not isinstance(database_url, str):
            raise MarketTimeStoreError("Database URL must be a string")

        url = database_url.strip()

        if url.startswith("postgresql+psycopg://"):
            url = url.replace(
                "postgresql+psycopg://",
                "postgresql://",
                1,
            )

        if not url.startswith(("postgresql://", "postgres://")):
            raise MarketTimeStoreError(
                "A PostgreSQL connection URL is required"
            )

        self._database_url = url

    def _connect(self):
        return psycopg.connect(
            self._database_url,
            connect_timeout=5,
            autocommit=False,
        )

    @classmethod
    def _validate_source_id(cls, source_id: str) -> str:
        if (
            not isinstance(source_id, str)
            or not cls._SOURCE_PATTERN.fullmatch(source_id)
        ):
            raise MarketTimeStoreError(
                "Invalid device source identifier"
            )

        return source_id

    @staticmethod
    def _validate_text(
        value: str | None,
        field_name: str,
        maximum: int,
    ) -> str | None:
        if value is None:
            return None

        if not isinstance(value, str):
            raise MarketTimeStoreError(
                f"{field_name} must be text"
            )

        value = value.strip()

        if not value or len(value) > maximum:
            raise MarketTimeStoreError(
                f"Invalid {field_name}"
            )

        return value

    @classmethod
    def _validate_timezone_name(
        cls,
        timezone_name: str | None,
    ) -> str | None:
        value = cls._validate_text(
            timezone_name,
            "timezone name",
            64,
        )

        if value is not None and (
            value.startswith("/")
            or ".." in value
            or not cls._TIMEZONE_PATTERN.fullmatch(value)
        ):
            raise MarketTimeStoreError(
                "Invalid timezone identifier"
            )

        return value

    @staticmethod
    def _validate_local_datetime(
        value: datetime,
        field_name: str,
    ) -> datetime:
        if not isinstance(value, datetime):
            raise MarketTimeStoreError(
                f"{field_name} must be a datetime"
            )

        if value.tzinfo is not None and value.utcoffset() is not None:
            raise MarketTimeStoreError(
                f"{field_name} must be timezone-naive broker time"
            )

        return value.replace(tzinfo=None)

    def _initialize_sync(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS
                    gold_bot_device_time_config (
                        source_id VARCHAR(128) PRIMARY KEY,
                        broker_name VARCHAR(128),
                        server_name VARCHAR(128),
                        timezone_name VARCHAR(64),
                        time_source VARCHAR(32) NOT NULL
                            DEFAULT 'MT5_SERVER_TIME',
                        reported_utc_offset_minutes SMALLINT,
                        config_status VARCHAR(16) NOT NULL
                            DEFAULT 'UNVERIFIED',
                        updated_at TIMESTAMPTZ NOT NULL
                            DEFAULT CURRENT_TIMESTAMP,

                        CONSTRAINT ck_device_time_config_offset
                            CHECK (
                                reported_utc_offset_minutes IS NULL
                                OR reported_utc_offset_minutes
                                    BETWEEN -720 AND 840
                            ),

                        CONSTRAINT ck_device_time_config_status
                            CHECK (
                                config_status IN (
                                    'UNVERIFIED',
                                    'VERIFIED',
                                    'REJECTED'
                                )
                            )
                    )
                    """
                )

                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS
                    gold_bot_time_offset_history (
                        id BIGINT GENERATED ALWAYS AS IDENTITY
                            PRIMARY KEY,

                        source_id VARCHAR(128) NOT NULL
                            REFERENCES gold_bot_device_time_config(
                                source_id
                            ) ON DELETE RESTRICT,

                        broker_local_from TIMESTAMP WITHOUT TIME ZONE
                            NOT NULL,

                        broker_local_to TIMESTAMP WITHOUT TIME ZONE,

                        utc_offset_minutes SMALLINT NOT NULL,
                        proof_source VARCHAR(256) NOT NULL,
                        is_dst BOOLEAN,

                        validation_status VARCHAR(16) NOT NULL
                            DEFAULT 'UNVERIFIED',

                        recorded_at TIMESTAMPTZ NOT NULL
                            DEFAULT CURRENT_TIMESTAMP,

                        CONSTRAINT ck_time_offset_range
                            CHECK (
                                utc_offset_minutes
                                BETWEEN -720 AND 840
                            ),

                        CONSTRAINT ck_time_offset_period
                            CHECK (
                                broker_local_to IS NULL
                                OR broker_local_to > broker_local_from
                            ),

                        CONSTRAINT ck_time_offset_status
                            CHECK (
                                validation_status IN (
                                    'UNVERIFIED',
                                    'VERIFIED',
                                    'REJECTED'
                                )
                            )
                    )
                    """
                )

                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                    idx_time_offset_history_source_period
                    ON gold_bot_time_offset_history (
                        source_id,
                        broker_local_from
                    )
                    """
                )

        except Exception as exc:
            raise MarketTimeStoreError(
                "Unable to initialize broker-time storage"
            ) from exc

    async def initialize(self) -> None:
        """Create tables without modifying existing market-data records."""
        await asyncio.to_thread(self._initialize_sync)

    def _save_device_config_sync(
        self,
        *,
        source_id: str,
        broker_name: str | None,
        server_name: str | None,
        timezone_name: str | None,
        reported_utc_offset_minutes: int | None,
    ) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO gold_bot_device_time_config (
                        source_id,
                        broker_name,
                        server_name,
                        timezone_name,
                        reported_utc_offset_minutes,
                        config_status,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, 'UNVERIFIED',
                            CURRENT_TIMESTAMP)

                    ON CONFLICT (source_id)
                    DO UPDATE SET
                        broker_name = EXCLUDED.broker_name,
                        server_name = EXCLUDED.server_name,
                        timezone_name = EXCLUDED.timezone_name,
                        reported_utc_offset_minutes =
                            EXCLUDED.reported_utc_offset_minutes,
                        config_status = 'UNVERIFIED',
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        source_id,
                        broker_name,
                        server_name,
                        timezone_name,
                        reported_utc_offset_minutes,
                    ),
                )

        except Exception as exc:
            raise MarketTimeStoreError(
                "Unable to save broker-time configuration"
            ) from exc

    async def save_device_config(
        self,
        *,
        source_id: str,
        broker_name: str | None = None,
        server_name: str | None = None,
        timezone_name: str | None = None,
        reported_utc_offset_minutes: int | None = None,
    ) -> None:
        """Save reported metadata; changes always reset verification."""
        source_id = self._validate_source_id(source_id)

        broker_name = self._validate_text(
            broker_name,
            "broker name",
            128,
        )

        server_name = self._validate_text(
            server_name,
            "server name",
            128,
        )

        timezone_name = self._validate_timezone_name(
            timezone_name
        )

        if reported_utc_offset_minutes is not None:
            if (
                isinstance(reported_utc_offset_minutes, bool)
                or not isinstance(reported_utc_offset_minutes, int)
                or not -720 <= reported_utc_offset_minutes <= 840
            ):
                raise MarketTimeStoreError(
                    "Reported UTC offset is outside the allowed range"
                )

        await asyncio.to_thread(
            self._save_device_config_sync,
            source_id=source_id,
            broker_name=broker_name,
            server_name=server_name,
            timezone_name=timezone_name,
            reported_utc_offset_minutes=reported_utc_offset_minutes,
        )

    def _add_offset_period_sync(
        self,
        *,
        source_id: str,
        broker_local_from: datetime,
        broker_local_to: datetime | None,
        utc_offset_minutes: int,
        proof_source: str,
        is_dst: bool | None,
    ) -> int:
        try:
            with self._connect() as connection:
                device = connection.execute(
                    """
                    SELECT source_id
                    FROM gold_bot_device_time_config
                    WHERE source_id = %s
                    FOR UPDATE
                    """,
                    (source_id,),
                ).fetchone()

                if device is None:
                    raise MarketTimeStoreError(
                        "Device time configuration does not exist"
                    )

                overlap = connection.execute(
                    """
                    SELECT 1
                    FROM gold_bot_time_offset_history
                    WHERE source_id = %s
                      AND validation_status <> 'REJECTED'
                      AND (
                          broker_local_to IS NULL
                          OR broker_local_to > %s
                      )
                      AND (
                          %s IS NULL
                          OR broker_local_from < %s
                      )
                    LIMIT 1
                    """,
                    (
                        source_id,
                        broker_local_from,
                        broker_local_to,
                        broker_local_to,
                    ),
                ).fetchone()

                if overlap is not None:
                    raise MarketTimeStoreError(
                        "Offset period overlaps an existing period"
                    )

                row = connection.execute(
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
                    VALUES (%s, %s, %s, %s, %s, %s, 'UNVERIFIED')
                    RETURNING id
                    """,
                    (
                        source_id,
                        broker_local_from,
                        broker_local_to,
                        utc_offset_minutes,
                        proof_source,
                        is_dst,
                    ),
                ).fetchone()

                return int(row[0])

        except MarketTimeStoreError:
            raise
        except Exception as exc:
            raise MarketTimeStoreError(
                "Unable to record broker offset history"
            ) from exc

    async def add_offset_period(
        self,
        *,
        source_id: str,
        broker_local_from: datetime,
        broker_local_to: datetime | None,
        utc_offset_minutes: int,
        proof_source: str,
        is_dst: bool | None = None,
    ) -> int:
        """Record an unverified offset period without converting timestamps."""
        source_id = self._validate_source_id(source_id)

        start = self._validate_local_datetime(
            broker_local_from,
            "broker_local_from",
        )

        end = (
            None
            if broker_local_to is None
            else self._validate_local_datetime(
                broker_local_to,
                "broker_local_to",
            )
        )

        if end is not None and end <= start:
            raise MarketTimeStoreError(
                "Offset period end must be after its start"
            )

        if (
            isinstance(utc_offset_minutes, bool)
            or not isinstance(utc_offset_minutes, int)
            or not -720 <= utc_offset_minutes <= 840
        ):
            raise MarketTimeStoreError(
                "UTC offset is outside the allowed range"
            )

        proof_source = self._validate_text(
            proof_source,
            "proof source",
            256,
        )

        if proof_source is None:
            raise MarketTimeStoreError(
                "Proof source is required"
            )

        if not isinstance(is_dst, (bool, type(None))):
            raise MarketTimeStoreError(
                "is_dst must be true, false, or unknown"
            )

        return await asyncio.to_thread(
            self._add_offset_period_sync,
            source_id=source_id,
            broker_local_from=start,
            broker_local_to=end,
            utc_offset_minutes=utc_offset_minutes,
            proof_source=proof_source,
            is_dst=is_dst,
        )
