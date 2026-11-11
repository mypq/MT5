
import hashlib
import hmac
import re
import time
from typing import Protocol


class SecurityVerificationError(Exception):
    """Raised when a signed market-data request is rejected."""


class NonceStore(Protocol):
    """
    Atomic persistent nonce storage interface.

    Production implementations must use an atomic operation,
    such as Redis SET key value NX EX ttl.

    Process-local dictionaries are not production-safe.
    """

    async def claim_nonce(
        self,
        source_id: str,
        nonce: str,
        ttl_seconds: int,
    ) -> bool:
        """Return True only when the nonce is stored for the first time."""
        ...


_TIMESTAMP_PATTERN = re.compile(r"^[0-9]{1,12}$")
_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SIGNATURE_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")
_METHOD_PATTERN = re.compile(r"^[A-Z]{3,16}$")


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


def _validate_source_id(source_id: str) -> str:
    if not isinstance(source_id, str):
        raise SecurityVerificationError(
            "Invalid source identifier"
        )

    normalized = source_id.strip()

    if (
        not normalized
        or len(normalized) > 128
        or "\n" in normalized
        or "\r" in normalized
    ):
        raise SecurityVerificationError(
            "Invalid source identifier"
        )

    return normalized


def _validate_timestamp(timestamp: str) -> str:
    if (
        not isinstance(timestamp, str)
        or not _TIMESTAMP_PATTERN.fullmatch(timestamp)
        or str(int(timestamp)) != timestamp
    ):
        raise SecurityVerificationError(
            "Invalid request timestamp"
        )

    return timestamp


def _validate_nonce(nonce: str) -> str:
    if (
        not isinstance(nonce, str)
        or not _NONCE_PATTERN.fullmatch(nonce)
    ):
        raise SecurityVerificationError(
            "Invalid request nonce"
        )

    return nonce


def _validate_method(method: str) -> str:
    if (
        not isinstance(method, str)
        or not _METHOD_PATTERN.fullmatch(method)
    ):
        raise SecurityVerificationError(
            "Invalid HTTP method"
        )

    return method


def _validate_path(path: str) -> str:
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or len(path) > 512
        or "\n" in path
        or "\r" in path
        or "?" in path
        or "#" in path
    ):
        raise SecurityVerificationError(
            "Invalid request path"
        )

    return path


def calculate_request_signature(
    *,
    secret: str | bytes,
    method: str,
    path: str,
    source_id: str,
    timestamp: str,
    nonce: str,
    raw_body: bytes,
) -> str:
    """
    Calculate HMAC-SHA256 for one exact HTTP request.

    Signed fields:
        HTTP method
        URL path
        timestamp
        source identifier
        nonce
        exact raw HTTP body bytes

    The caller must use the same method, path, and body
    when generating and verifying the signature.
    """
    secret_bytes = _validate_secret(secret)
    normalized_method = _validate_method(method)
    normalized_path = _validate_path(path)
    normalized_source_id = _validate_source_id(source_id)
    normalized_timestamp = _validate_timestamp(timestamp)
    normalized_nonce = _validate_nonce(nonce)

    if not isinstance(raw_body, bytes):
        raise SecurityVerificationError(
            "Request body must be raw bytes"
        )

    canonical_header = (
        f"{normalized_method}\n"
        f"{normalized_path}\n"
        f"{normalized_timestamp}\n"
        f"{normalized_source_id}\n"
        f"{normalized_nonce}\n"
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
    method: str,
    path: str,
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
    Verify authenticity, request freshness, and replay protection.

    The caller must resolve device_secret from a trusted device
    registry using source_id. Never accept a secret supplied
    inside the request body.

    The nonce store must provide an atomic persistent claim.
    Any nonce-store failure rejects the request.
    """
    normalized_source_id = _validate_source_id(source_id)
    normalized_timestamp = _validate_timestamp(timestamp)
    normalized_nonce = _validate_nonce(nonce)

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

    expected_signature = calculate_request_signature(
        secret=device_secret,
        method=method,
        path=path,
        source_id=normalized_source_id,
        timestamp=normalized_timestamp,
        nonce=normalized_nonce,
        raw_body=raw_body,
    )

    if not hmac.compare_digest(
        expected_signature,
        signature.lower(),
    ):
        raise SecurityVerificationError(
            "Request signature verification failed"
        )

    current_time = int(time.time())
    request_time = int(normalized_timestamp)

    if request_time > current_time + future_skew_seconds:
        raise SecurityVerificationError(
            "Request timestamp is in the future"
        )

    if current_time - request_time > max_age_seconds:
        raise SecurityVerificationError(
            "Request timestamp has expired"
        )

    nonce_ttl = max_age_seconds + future_skew_seconds + 1

    try:
        claimed = await nonce_store.claim_nonce(
            source_id=normalized_source_id,
            nonce=normalized_nonce,
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
