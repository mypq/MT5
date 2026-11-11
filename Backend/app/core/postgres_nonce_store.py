import asyncio
import re
import time

import psycopg


class NonceStoreError(Exception):
    """Raised when persistent nonce storage cannot operate safely."""


class PostgresNonceStore:
    """
    PostgreSQL-backed atomic nonce storage.

    The database table must be initialized before this store
    is used to accept signed requests.

    Each operation uses a separate database connection.
    Blocking database operations run in worker threads.
    """

    _TABLE_NAME = "gold_bot_market_data_nonces"

    _SOURCE_PATTERN = re.compile(r"^[^\r\n]{1,128}$")
    _NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

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
        """
        Create the nonce table and expiry index.

        Call this during application startup before accepting
        authenticated market-data requests.
        """
        await asyncio.to_thread(self._initialize_sync)

    @classmethod
    def _validate_claim(
        cls,
        source_id: str,
        nonce: str,
        ttl_seconds: int,
    ) -> None:
        if (
            not isinstance(source_id, str)
            or not cls._SOURCE_PATTERN.fullmatch(source_id)
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

    def _claim_nonce_sync(
        self,
        source_id: str,
        nonce: str,
        ttl_seconds: int,
    ) -> bool:
        current_time = int(time.time())
        expires_at = current_time + ttl_seconds

        try:
            with self._connect() as connection:
                with connection.transaction():
                    # Remove expired records so old nonce values
                    # do not remain permanently reserved.
                    connection.execute(
                        """
                        DELETE FROM gold_bot_market_data_nonces
                        WHERE expires_at <= %s
                        """,
                        (current_time,),
                    )

                    result = connection.execute(
                        """
                        INSERT INTO gold_bot_market_data_nonces (
                            source_id,
                            nonce,
                            expires_at
                        )
                        VALUES (%s, %s, %s)
                        ON CONFLICT (source_id, nonce) DO NOTHING
                        RETURNING 1
                        """,
                        (
                            source_id,
                            nonce,
                            expires_at,
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

        Returns True for the first successful claim.
        Returns False if the same nonce is already reserved.

        Database failures raise NonceStoreError.
        """
        self._validate_claim(
            source_id,
            nonce,
            ttl_seconds,
        )

        return await asyncio.to_thread(
            self._claim_nonce_sync,
            source_id,
            nonce,
            ttl_seconds,
        )
