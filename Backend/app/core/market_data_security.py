import hashlib
import hmac
import re
import time
from typing import Protocol


class SecurityVerificationError(Exception):
    """Raised when a signed market-data request is rejected."""


class NonceStore(Protocol):
    """
    Implement this interface using an atomic persistent store.

    Redis implementation should use SET key value NX EX ttl.
    A process-local dictionary is not sufficient for production.
    """

    async def claim_nonce(
        self,
        source_id: str,
        nonce: str,
        ttl_seconds: int,
    ) -> bool:
        """
        Atomically claim a nonce.

        Return True only when the nonce did not exist and was stored.
        Return False when the nonce has already been used.
        """
        ...


_TIMESTAMP_PATTERN = re.compile(r"^[0-9]{1,12}$")
_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SIGNATURE_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")


def _validate_secret(secret: str | bytes) -> bytes:
    if isinstance(secret, str):
        secret_bytes = secret.encode("utf-8")
    elif isinstance(secret, bytes):
        secret_bytes = secret
    else:
        raise SecurityVerificationError(
            "Invalid device secret type"
        )

    if len(secret_bytes) < 32:
        raise SecurityVerificationError(
            "Device secret must contain at least 32 bytes"
        )

    return secret_bytes


def calculate_request_signature(
    *,
    secret: str | bytes,
    source_id: str,
    timestamp: str,
    nonce: str,
    raw_body: bytes,
) -> str:
    """
    Calculate HMAC-SHA256 for the exact HTTP request body.

    Canonical message:
        timestamp + newline
        source_id + newline
        nonce + newline
        raw HTTP body bytes
    """
    secret_bytes = _validate_secret(secret)

    if not isinstance(raw_body, bytes):
        raise SecurityVerificationError(
            "Request body must be raw bytes"
        )

    if not isinstance(source_id, str):
        raise SecurityVerificationError(
            "Invalid source identifier"
        )

    normalized_source_id = source_id.strip()

    if not normalized_source_id or len(normalized_source_id) > 128:
        raise SecurityVerificationError(
            "Invalid source identifier"
        )

    if not _TIMESTAMP_PATTERN.fullmatch(timestamp):
        raise SecurityVerificationError(
            "Invalid request timestamp"
        )

    if not _NONCE_PATTERN.fullmatch(nonce):
        raise SecurityVerificationError(
            "Invalid request nonce"
        )

    canonical_header = (
        f"{timestamp}\n"
        f"{normalized_source_id}\n"
        f"{nonce}\n"
    ).encode("utf-8")

    message = canonical_header + raw_body

    return hmac.new(
        secret_bytes,
        message,
        hashlib.sha256,
    ).hexdigest()


async def verify_signed_request(
    *,
    raw_body: bytes,
    source_id: str,
    timestamp: str,
    nonce: str,
    signature: str,
    device_secret: str | bytes,
    nonce_store: NonceStore,
    max_age_seconds: int = 30,
    future_skew_seconds: int = 5,
) -> None:
    """
    Verify request authenticity, freshness, and replay protection.

    The caller must resolve device_secret from a trusted device
    registry using source_id. Never accept a secret supplied
    inside the request body.

    The nonce store must provide an atomic claim operation.
    Failure to access the store rejects the request.
    """
    if not isinstance(source_id, str):
        raise SecurityVerificationError(
            "Invalid source identifier"
        )

    normalized_source_id = source_id.strip()

    if not normalized_source_id or len(normalized_source_id) > 128:
        raise SecurityVerificationError(
            "Invalid source identifier"
        )

    if not isinstance(timestamp, str):
        raise SecurityVerificationError(
            "Invalid request timestamp"
        )

    if not _TIMESTAMP_PATTERN.fullmatch(timestamp):
        raise SecurityVerificationError(
            "Invalid request timestamp"
        )

    if str(int(timestamp)) != timestamp:
        raise SecurityVerificationError(
            "Timestamp must use canonical decimal format"
        )

    if not isinstance(nonce, str) or not _NONCE_PATTERN.fullmatch(nonce):
        raise SecurityVerificationError(
            "Invalid request nonce"
        )

    if not isinstance(signature, str):
        raise SecurityVerificationError(
            "Invalid request signature"
        )

    if not _SIGNATURE_PATTERN.fullmatch(signature):
        raise SecurityVerificationError(
            "Invalid request signature format"
        )

    if not isinstance(raw_body, bytes):
        raise SecurityVerificationError(
            "Request body must be raw bytes"
        )

    if not 1 <= max_age_seconds <= 300:
        raise SecurityVerificationError(
            "Invalid maximum request age configuration"
        )

    if not 0 <= future_skew_seconds <= 30:
        raise SecurityVerificationError(
            "Invalid future clock-skew configuration"
        )

    try:
        secret_bytes = _validate_secret(device_secret)

        expected_signature = calculate_request_signature(
            secret=secret_bytes,
            source_id=normalized_source_id,
            timestamp=timestamp,
            nonce=nonce,
            raw_body=raw_body,
        )
    except SecurityVerificationError:
        raise
    except Exception as exc:
        raise SecurityVerificationError(
            "Unable to calculate request signature"
        ) from exc

    if not hmac.compare_digest(
        expected_signature,
        signature.lower(),
    ):
        raise SecurityVerificationError(
            "Request signature verification failed"
        )

    current_time = int(time.time())
    request_time = int(timestamp)

    if request_time > current_time + future_skew_seconds:
        raise SecurityVerificationError(
            "Request timestamp is in the future"
        )

    if current_time - request_time > max_age_seconds:
        raise SecurityVerificationError(
            "Request timestamp has expired"
        )

    nonce_ttl = max_age_seconds + future_skew_seconds

    try:
        claimed = await nonce_store.claim_nonce(
            source_id=normalized_source_id,
            nonce=nonce,
            ttl_seconds=nonce_ttl,
        )
    except Exception as exc:
        raise SecurityVerificationError(
            "Replay protection store is unavailable"
        ) from exc

    if claimed is not True:
        raise SecurityVerificationError(
            "Request nonce has already been used"
  )
