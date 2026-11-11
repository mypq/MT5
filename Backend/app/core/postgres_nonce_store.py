import asyncio
import re

import psycopg


class NonceStoreError(Exception):
    """Raised when persistent nonce storage cannot operate safely."""


class PostgresNonceStore:
    """
    PostgreSQL-backed atomic nonce storage.

    The database table must be initialized before accepting
    authenticated market-data requests.

    Blocking database operations run in worker threads.
    """

    _SOURCE_PATTERN = re.compile(r"^[^\r\n]{1,128}$")
    _NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

    _TABLE_NAME = "gold_bot_market_data_nonces"

    def __init__(self, database_url: str) -> None:
        if not isinstance(database_url, str):
            raise NonceStoreError(
                "Database URL must be a string"
            )

        normalized_url = database_url.strip()

        if normalized_url.startswith("postgresql+psycopg://"):
            normalized_url = normalized_url.replace(
                "postgresql+psycopg://",
                "postgresql://",
                1,
            )

        if not normalized_url.startswith(
            ("postgresql://", "postgres://")
        ):
            raise NonceStoreError(
                "A PostgreSQL connection URL is required"
            )

        self._database_url = normalized_url

    def _connect(self):
        return psycopg.connect(
            self._database_url,
            connect_timeout=5,
            autocommit=False,
        )

    def _initialize_sync(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS
                    gold_bot_market_data_nonces (
                        source_id VARCHAR(128) NOT NULL,
                        nonce VARCHAR(128) NOT NULL,
                        expires_at BIGINT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL
                            DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (source_id, nonce)
                    )
                    """
                )

                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                    idx_market_data_nonces_expires_at
                    ON gold_bot_market_data_nonces (expires_at)
                    """
                )

        except Exception as exc:
            raise NonceStoreError(
                "Unable to initialize PostgreSQL nonce storage"
            ) from exc

    async def initialize(self) -> None:
        """Create the nonce table and expiry index."""
        await asyncio.to_thread(self._initialize_sync)

    @classmethod
    def _validate_claim(
        cls,
        source_id: str,
        nonce: str,
        ttl_seconds: int,
    ) -> str:
        if not isinstance(source_id, str):
            raise NonceStoreError(
                "Invalid nonce source identifier"
            )

        normalized_source_id = source_id.strip()

        if (
            not normalized_source_id
            or not cls._SOURCE_PATTERN.fullmatch(
                normalized_source_id
            )
        ):
            raise NonceStoreError(
                "Invalid nonce source identifier"
            )

        if (
            not isinstance(nonce, str)
            or not cls._NONCE_PATTERN.fullmatch(nonce)
        ):
            raise NonceStoreError(
                "Invalid nonce value"
            )

        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not 1 <= ttl_seconds <= 3600
        ):
            raise NonceStoreError(
                "Nonce TTL must be between 1 and 3600 seconds"
            )

        return normalized_source_id

    def _claim_nonce_sync(
        self,
        source_id: str,
        nonce: str,
        ttl_seconds: int,
    ) -> bool:
        try:
            with self._connect() as connection:
                result = connection.execute(
                    """
                    INSERT INTO gold_bot_market_data_nonces (
                        source_id,
                        nonce,
                        expires_at
                    )
                    VALUES (
                        %s,
                        %s,
                        FLOOR(
                            EXTRACT(EPOCH FROM clock_timestamp())
                        )::BIGINT + %s
                    )
                    ON CONFLICT (source_id, nonce)
                    DO UPDATE SET
                        expires_at = EXCLUDED.expires_at,
                        created_at = CURRENT_TIMESTAMP
                    WHERE
                        gold_bot_market_data_nonces.expires_at
                        <= FLOOR(
                            EXTRACT(EPOCH FROM clock_timestamp())
                        )::BIGINT
                    RETURNING 1
                    """,
                    (
                        source_id,
                        nonce,
                        ttl_seconds,
                    ),
                )

                return result.fetchone() is not None

        except Exception as exc:
            raise NonceStoreError(
                "PostgreSQL nonce claim failed"
            ) from exc

    async def claim_nonce(
        self,
        source_id: str,
        nonce: str,
        ttl_seconds: int,
    ) -> bool:
        """
        Atomically reserve a nonce.

        Returns True for a new or expired nonce.
        Returns False if the nonce is already active.

        Database failures raise NonceStoreError.
        """
        normalized_source_id = self._validate_claim(
            source_id,
            nonce,
            ttl_seconds,
        )

        return await asyncio.to_thread(
            self._claim_nonce_sync,
            normalized_source_id,
            nonce,
            ttl_seconds,
        )

    def _cleanup_expired_sync(self, batch_size: int) -> int:
        try:
            with self._connect() as connection:
                result = connection.execute(
                    """
                    WITH expired_rows AS (
                        SELECT ctid
                        FROM gold_bot_market_data_nonces
                        WHERE expires_at <= FLOOR(
                            EXTRACT(EPOCH FROM clock_timestamp())
                        )::BIGINT
                        LIMIT %s
                    )
                    DELETE FROM gold_bot_market_data_nonces
                    WHERE ctid IN (
                        SELECT ctid FROM expired_rows
                    )
                    RETURNING 1
                    """,
                    (batch_size,),
                )

                return len(result.fetchall())

        except Exception as exc:
            raise NonceStoreError(
                "PostgreSQL nonce cleanup failed"
            ) from exc

    async def cleanup_expired(
        self,
        batch_size: int = 1000,
    ) -> int:
        """
        Delete a limited batch of expired nonce records.

        Call periodically from a managed background task.
        This method is not executed for every incoming request.
        """
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 10000
        ):
            raise NonceStoreError(
                "Cleanup batch size must be between 1 and 10000"
            )

        return await asyncio.to_thread(
            self._cleanup_expired_sync,
            batch_size,
        )
