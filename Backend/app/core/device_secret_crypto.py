import base64
import binascii
import re
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class DeviceSecretEncryptionError(Exception):
    """Raised when device-secret encryption or decryption fails."""


class DeviceSecretCipher:
    """
    AES-256-GCM encryption for registered device secrets.

    Each encrypted secret is bound to its source_id using
    authenticated additional data (AAD).

    The encryption key must be supplied by trusted server
    configuration, never by an API request.
    """

    _SOURCE_PATTERN = re.compile(r"^[^\r\n]{1,128}$")
    _TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

    _VERSION_PREFIX = "v1:"
    _NONCE_LENGTH = 12
    _TAG_LENGTH = 16
    _MAX_SECRET_LENGTH = 4096
    _MAX_TOKEN_LENGTH = 6000

    def __init__(self, encryption_key_hex: str) -> None:
        if (
            not isinstance(encryption_key_hex, str)
            or not re.fullmatch(
                r"[0-9a-fA-F]{64}",
                encryption_key_hex,
            )
        ):
            raise DeviceSecretEncryptionError(
                "Encryption key must be exactly 64 hexadecimal characters"
            )

        self._key = bytes.fromhex(encryption_key_hex)
        self._cipher = AESGCM(self._key)

    @classmethod
    def _validate_source_id(cls, source_id: str) -> str:
        if not isinstance(source_id, str):
            raise DeviceSecretEncryptionError(
                "Invalid device source identifier"
            )

        normalized = source_id.strip()

        if (
            not normalized
            or not cls._SOURCE_PATTERN.fullmatch(normalized)
        ):
            raise DeviceSecretEncryptionError(
                "Invalid device source identifier"
            )

        return normalized

    @classmethod
    def _validate_secret(
        cls,
        secret: str | bytes,
    ) -> bytes:
        if isinstance(secret, str):
            secret_bytes = secret.encode("utf-8")
        elif isinstance(secret, bytes):
            secret_bytes = secret
        else:
            raise DeviceSecretEncryptionError(
                "Device secret must be text or bytes"
            )

        if not 32 <= len(secret_bytes) <= cls._MAX_SECRET_LENGTH:
            raise DeviceSecretEncryptionError(
                "Device secret length is outside the allowed range"
            )

        return secret_bytes

    @staticmethod
    def _associated_data(source_id: str) -> bytes:
        return (
            "gold-bot-device-secret:v1:"
            + source_id
        ).encode("utf-8")

    def encrypt(
        self,
        *,
        source_id: str,
        secret: str | bytes,
    ) -> str:
        """
        Encrypt a device secret and return a versioned token.

        A fresh cryptographically secure nonce is generated
        for every encryption operation.
        """
        normalized_source_id = self._validate_source_id(
            source_id
        )
        secret_bytes = self._validate_secret(secret)

        nonce = secrets.token_bytes(self._NONCE_LENGTH)

        try:
            ciphertext = self._cipher.encrypt(
                nonce,
                secret_bytes,
                self._associated_data(normalized_source_id),
            )

            encoded = base64.urlsafe_b64encode(
                nonce + ciphertext
            ).rstrip(b"=").decode("ascii")

            token = self._VERSION_PREFIX + encoded

            if len(token) > self._MAX_TOKEN_LENGTH:
                raise DeviceSecretEncryptionError(
                    "Encrypted device secret is too large"
                )

            return token

        except DeviceSecretEncryptionError:
            raise
        except Exception as exc:
            raise DeviceSecretEncryptionError(
                "Unable to encrypt device secret"
            ) from exc

    def decrypt(
        self,
        *,
        source_id: str,
        encrypted_secret: str,
    ) -> bytes:
        """
        Decrypt a stored device secret.

        Decryption fails if the token is malformed, the key is
        incorrect, or the source_id does not match the original.
        """
        normalized_source_id = self._validate_source_id(
            source_id
        )

        if (
            not isinstance(encrypted_secret, str)
            or len(encrypted_secret) > self._MAX_TOKEN_LENGTH
            or not encrypted_secret.startswith(
                self._VERSION_PREFIX
            )
        ):
            raise DeviceSecretEncryptionError(
                "Invalid encrypted device-secret format"
            )

        encoded = encrypted_secret[len(self._VERSION_PREFIX):]

        if (
            not encoded
            or not self._TOKEN_PATTERN.fullmatch(encoded)
        ):
            raise DeviceSecretEncryptionError(
                "Invalid encrypted device-secret encoding"
            )

        try:
            padded = encoded + "=" * (-len(encoded) % 4)

            payload = base64.b64decode(
                padded.encode("ascii"),
                altchars=b"-_",
                validate=True,
            )

            minimum_length = (
                self._NONCE_LENGTH + self._TAG_LENGTH
            )

            if len(payload) < minimum_length:
                raise DeviceSecretEncryptionError(
                    "Encrypted device secret is incomplete"
                )

            nonce = payload[:self._NONCE_LENGTH]
            ciphertext = payload[self._NONCE_LENGTH:]

            plaintext = self._cipher.decrypt(
                nonce,
                ciphertext,
                self._associated_data(normalized_source_id),
            )

            return self._validate_secret(plaintext)

        except InvalidTag as exc:
            raise DeviceSecretEncryptionError(
                "Device secret authentication failed"
            ) from exc
        except (
            ValueError,
            binascii.Error,
            UnicodeError,
        ) as exc:
            raise DeviceSecretEncryptionError(
                "Unable to decode encrypted device secret"
            ) from exc
        except DeviceSecretEncryptionError:
            raise
        except Exception as exc:
            raise DeviceSecretEncryptionError(
                "Unable to decrypt device secret"
            ) from exc
