import asyncio
import re

import psycopg

from app.core.device_secret_crypto import (
    DeviceSecretCipher,
    DeviceSecretEncryptionError,
)


class DeviceRegistryError(Exception):
    """Raised when trusted device registry operations fail."""


class DeviceRegistry:
    """
    PostgreSQL-backed registry for trusted MT5 devices.

    Device secrets are stored encrypted, never as plaintext.

    Enrollment must be performed by a trusted administrative
    process. Public API requests must never register devices.
    """

    _SOURCE_PATTERN = re.compile(r"^[^\r\n]{1,128}$")
    _TABLE_NAME = "gold_bot_trusted_devices"

    def __init__(
        self,
        database_url: str,
        cipher: DeviceSecretCipher,
    ) -> None:
        if not isinstance(database_url, str):
            raise DeviceRegistryError(
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
            raise DeviceRegistryError(
                "A PostgreSQL connection URL is required"
            )

        if not isinstance(cipher, DeviceSecretCipher):
            raise DeviceRegistryError(
                "A valid device-secret cipher is required"
            )

        self._database_url = normalized_url
        self._cipher = cipher

    def _connect(self):
        return psycopg.connect(
            self._database_url,
            connect_timeout=5,
            autocommit=False,
        )

    @classmethod
    def _validate_source_id(cls, source_id: str) -> str:
        if not isinstance(source_id, str):
            raise DeviceRegistryError(
                "Invalid device source identifier"
            )

        normalized = source_id.strip()

        if (
            not normalized
            or not cls._SOURCE_PATTERN.fullmatch(normalized)
        ):
            raise DeviceRegistryError(
                "Invalid device source identifier"
            )

        return normalized

    def _initialize_sync(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS
                    gold_bot_trusted_devices (
                        source_id VARCHAR(128) PRIMARY KEY,
                        encrypted_secret TEXT NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMPTZ NOT NULL
                            DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ NOT NULL
                            DEFAULT CURRENT_TIMESTAMP,
                        revoked_at TIMESTAMPTZ NULL
                    )
                    """
                )

                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                    idx_trusted_devices_active
                    ON gold_bot_trusted_devices (is_active)
                    """
                )

        except Exception as exc:
            raise DeviceRegistryError(
                "Unable to initialize trusted device registry"
            ) from exc

    async def initialize(self) -> None:
        """Create the trusted-device table and indexes."""
        await asyncio.to_thread(self._initialize_sync)

    def _register_device_sync(
        self,
        source_id: str,
        encrypted_secret: str,
    ) -> bool:
        try:
            with self._connect() as connection:
                result = connection.execute(
                    """
                    INSERT INTO gold_bot_trusted_devices (
                        source_id,
                        encrypted_secret
                    )
                    VALUES (%s, %s)
                    ON CONFLICT (source_id) DO NOTHING
                    RETURNING source_id
                    """,
                    (
                        source_id,
                        encrypted_secret,
                    ),
                )

                return result.fetchone() is not None

        except Exception as exc:
            raise DeviceRegistryError(
                "Unable to register trusted device"
            ) from exc

    async def register_device(
        self,
        *,
        source_id: str,
        encrypted_secret: str,
    ) -> bool:
        """
        Register a device without replacing an existing record.

        The encrypted secret is authenticated before storage.
        Returns False if the source_id already exists.

        This method must only be called by trusted enrollment
        code, never directly from a public endpoint.
        """
        normalized_source_id = self._validate_source_id(
            source_id
        )

        try:
            self._cipher.decrypt(
                source_id=normalized_source_id,
                encrypted_secret=encrypted_secret,
            )
        except DeviceSecretEncryptionError as exc:
            raise DeviceRegistryError(
                "Device secret encryption validation failed"
            ) from exc

        return await asyncio.to_thread(
            self._register_device_sync,
            normalized_source_id,
            encrypted_secret,
        )

    def _get_encrypted_secret_sync(
        self,
        source_id: str,
    ) -> str | None:
        try:
            with self._connect() as connection:
                result = connection.execute(
                    """
                    SELECT encrypted_secret
                    FROM gold_bot_trusted_devices
                    WHERE source_id = %s
                      AND is_active = TRUE
                      AND revoked_at IS NULL
                    """,
                    (source_id,),
                )

                row = result.fetchone()

                if row is None:
                    return None

                return row[0]

        except Exception as exc:
            raise DeviceRegistryError(
                "Unable to retrieve trusted device"
            ) from exc

    async def get_device_secret(
        self,
        source_id: str,
    ) -> bytes | None:
        """
        Return the decrypted secret for an active device.

        Returns None when the device is unknown or revoked.
        Database and decryption failures raise an error.
        """
        normalized_source_id = self._validate_source_id(
            source_id
        )

        encrypted_secret = await asyncio.to_thread(
            self._get_encrypted_secret_sync,
            normalized_source_id,
        )

        if encrypted_secret is None:
            return None

        try:
            return self._cipher.decrypt(
                source_id=normalized_source_id,
                encrypted_secret=encrypted_secret,
            )
        except DeviceSecretEncryptionError as exc:
            raise DeviceRegistryError(
                "Stored device secret could not be authenticated"
            ) from exc

    def _revoke_device_sync(self, source_id: str) -> bool:
        try:
            with self._connect() as connection:
                result = connection.execute(
                    """
                    UPDATE gold_bot_trusted_devices
                    SET
                        is_active = FALSE,
                        revoked_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE source_id = %s
                      AND is_active = TRUE
                    RETURNING source_id
                    """,
                    (source_id,),
                )

                return result.fetchone() is not None

        except Exception as exc:
            raise DeviceRegistryError(
                "Unable to revoke trusted device"
            ) from exc

    async def revoke_device(self, source_id: str) -> bool:
        """
        Revoke an active device immediately.

        Returns True when an active device was revoked.
        Returns False when it was unknown or already inactive.
        """
        normalized_source_id = self._validate_source_id(
            source_id
        )

        return await asyncio.to_thread(
            self._revoke_device_sync,
            normalized_source_id,
        )
