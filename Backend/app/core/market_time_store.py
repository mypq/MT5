import asyncio
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg


class MarketTimeStoreError(Exception):
    """Broker-time storage error."""


class MarketTimeStore:
    """
    Stores per-device broker-time metadata and UTC offset history.

    Legacy local-time history is preserved without reinterpretation.
    New offset periods use UTC boundaries.

    All newly recorded periods remain UNVERIFIED.
    This module does not convert candle timestamps into UTC.
    """

    _SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
    _TIMEZONE_PATTERN = re.compile(
        r"^[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+)*$"
    )

    def __init__(self, database_url: str) -> None:
        if not isinstance(database_url, str):
            raise MarketTimeStoreError(
                "Database URL must be a string"
            )

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

        if value is None:
            return None

        if (
            value.startswith("/")
            or ".." in value
            or not cls._TIMEZONE_PATTERN.fullmatch(value)
        ):
            raise MarketTimeStoreError(
                "Invalid timezone identifier"
            )

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise MarketTimeStoreError(
                "Unknown IANA timezone identifier"
            ) from exc

        return value

    @staticmethod
    def _validate_utc_datetime(
        value: datetime,
        field_name: str,
    ) -> datetime:
        if not isinstance(value, datetime):
            raise MarketTimeStoreError(
                f"{field_name} must be a datetime"
            )

        try:
            offset = value.utcoffset()
        except Exception as exc:
            raise MarketTimeStoreError(
                f"{field_name} has invalid timezone information"
            ) from exc

        if value.tzinfo is None or offset is None:
            raise MarketTimeStoreError(
                f"{field_name} must be timezone-aware"
            )

        return value.astimezone(timezone.utc)

    def _initialize_sync(self) -> None:
        required_columns = {
            "gold_bot_device_time_config": {
                "source_id",
                "broker_name",
                "server_name",
                "timezone_name",
                "time_source",
                "reported_utc_offset_minutes",
                "config_status",
                "updated_at",
            },
            "gold_bot_time_offset_history": {
                "id",
                "source_id",
                "broker_local_from",
                "broker_local_to",
                "utc_offset_minutes",
                "proof_source",
                "is_dst",
                "validation_status",
                "recorded_at",
            },
            "gold_bot_time_offset_history_v2": {
                "id",
                "source_id",
                "effective_from_utc",
                "effective_to_utc",
                "utc_offset_minutes",
                "evidence_reference",
                "is_dst",
                "validation_status",
                "recorded_at",
            },
        }

        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = ANY(%s)
                    """,
                    (list(required_columns),),
                ).fetchall()

                actual = {}

                for table_name, column_name in rows:
                    actual.setdefault(table_name, set()).add(
                        column_name
                    )

                problems = []

                for table_name, expected in required_columns.items():
                    actual_columns = actual.get(table_name, set())

                    if not actual_columns:
                        problems.append(
                            f"Missing table: {table_name}"
                        )
                        continue

                    missing = expected - actual_columns

                    if missing:
                        problems.append(
                            f"{table_name} missing columns: "
                            f"{', '.join(sorted(missing))}"
                        )

                exclusion_exists = connection.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname =
                            'ex_time_offset_v2_no_overlap'
                          AND conrelid = to_regclass(
                            'gold_bot_time_offset_history_v2'
                          )
                          AND contype = 'x'
                    )
                    """
                ).fetchone()[0]

                if not exclusion_exists:
                    problems.append(
                        "Missing V2 time overlap exclusion constraint"
                    )

                if problems:
                    raise MarketTimeStoreError(
                        "Database schema is not migrated correctly. "
                        "Run alembic upgrade head. Details: "
                        + "; ".join(problems)
                    )

        except MarketTimeStoreError:
            raise
        except Exception as exc:
            raise MarketTimeStoreError(
                "Unable to initialize broker-time storage"
            ) from exc

    async def initialize(self) -> None:
        """
        Verify that Alembic-managed broker-time tables exist.
        This method never creates or alters database objects.
        """
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
                updated = connection.execute(
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
                    VALUES (
                        %s, %s, %s, %s, %s,
                        'UNVERIFIED',
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (source_id)
                    DO UPDATE SET
                        broker_name = EXCLUDED.broker_name,
                        server_name = EXCLUDED.server_name,
                        timezone_name = EXCLUDED.timezone_name,
                        reported_utc_offset_minutes =
                            EXCLUDED.reported_utc_offset_minutes,
                        config_status = 'UNVERIFIED',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE (
                        gold_bot_device_time_config.broker_name,
                        gold_bot_device_time_config.server_name,
                        gold_bot_device_time_config.timezone_name,
                        gold_bot_device_time_config.reported_utc_offset_minutes
                    ) IS DISTINCT FROM (
                        EXCLUDED.broker_name,
                        EXCLUDED.server_name,
                        EXCLUDED.timezone_name,
                        EXCLUDED.reported_utc_offset_minutes
                    )
                    RETURNING source_id
                    """,
                    (
                        source_id,
                        broker_name,
                        server_name,
                        timezone_name,
                        reported_utc_offset_minutes,
                    ),
                ).fetchone()

                if updated is not None:
                    connection.execute(
                        """
                        UPDATE gold_bot_time_offset_history_v2
                        SET validation_status = 'UNVERIFIED'
                        WHERE source_id = %s
                          AND validation_status = 'VERIFIED'
                        """,
                        (source_id,),
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

    def _add_utc_offset_period_sync(
        self,
        *,
        source_id: str,
        effective_from_utc: datetime,
        effective_to_utc: datetime | None,
        utc_offset_minutes: int,
        evidence_reference: str,
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
                    FROM gold_bot_time_offset_history_v2
                    WHERE source_id = %s
                      AND validation_status <> 'REJECTED'
                      AND (
                          effective_to_utc IS NULL
                          OR effective_to_utc > %s
                      )
                      AND (
                          %s::TIMESTAMPTZ IS NULL
                          OR effective_from_utc < %s
                      )
                    LIMIT 1
                    """,
                    (
                        source_id,
                        effective_from_utc,
                        effective_to_utc,
                        effective_to_utc,
                    ),
                ).fetchone()

                if overlap is not None:
                    raise MarketTimeStoreError(
                        "UTC offset period overlaps an existing period"
                    )

                row = connection.execute(
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
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        'UNVERIFIED'
                    )
                    RETURNING id
                    """,
                    (
                        source_id,
                        effective_from_utc,
                        effective_to_utc,
                        utc_offset_minutes,
                        evidence_reference,
                        is_dst,
                    ),
                ).fetchone()

                return int(row[0])

        except MarketTimeStoreError:
            raise
        except Exception as exc:
            raise MarketTimeStoreError(
                "Unable to record UTC broker offset history"
            ) from exc

    async def add_utc_offset_period(
        self,
        *,
        source_id: str,
        effective_from_utc: datetime,
        effective_to_utc: datetime | None,
        utc_offset_minutes: int,
        evidence_reference: str,
        is_dst: bool | None = None,
    ) -> int:
        """
        Record a new UTC-bounded offset period.

        Period semantics are [effective_from_utc, effective_to_utc).
        A missing end means the period is open-ended.

        All records are UNVERIFIED. This method does not convert
        broker-local timestamps or establish trust.
        """
        source_id = self._validate_source_id(source_id)

        start = self._validate_utc_datetime(
            effective_from_utc,
            "effective_from_utc",
        )

        end = (
            None
            if effective_to_utc is None
            else self._validate_utc_datetime(
                effective_to_utc,
                "effective_to_utc",
            )
        )

        if end is not None and end <= start:
            raise MarketTimeStoreError(
                "UTC offset period end must be after its start"
            )

        if (
            isinstance(utc_offset_minutes, bool)
            or not isinstance(utc_offset_minutes, int)
            or not -720 <= utc_offset_minutes <= 840
        ):
            raise MarketTimeStoreError(
                "UTC offset is outside the allowed range"
            )

        evidence_reference = self._validate_text(
            evidence_reference,
            "evidence reference",
            512,
        )

        if evidence_reference is None:
            raise MarketTimeStoreError(
                "Evidence reference is required"
            )

        if not isinstance(is_dst, (bool, type(None))):
            raise MarketTimeStoreError(
                "is_dst must be true, false, or unknown"
            )

        return await asyncio.to_thread(
            self._add_utc_offset_period_sync,
            source_id=source_id,
            effective_from_utc=start,
            effective_to_utc=end,
            utc_offset_minutes=utc_offset_minutes,
            evidence_reference=evidence_reference,
            is_dst=is_dst,
        )
