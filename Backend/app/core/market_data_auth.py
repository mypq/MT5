import json

from fastapi import Request

from app.core.device_registry import (
    DeviceRegistry,
    DeviceRegistryError,
)
from app.core.market_data_security import (
    SecurityVerificationError,
    verify_signed_request,
)
from app.core.postgres_nonce_store import PostgresNonceStore


class MarketDataAuthenticationError(Exception):
    """Raised when market-data request authentication fails."""


MAX_REQUEST_BODY_BYTES = 2_000_000


def _reject_duplicate_json_keys(pairs):
    result = {}

    for key, value in pairs:
        if key in result:
            raise ValueError(
                "Duplicate JSON keys are not allowed"
            )

        result[key] = value

    return result


async def authenticate_market_data_request(
    *,
    request: Request,
    device_registry: DeviceRegistry,
    nonce_store: PostgresNonceStore,
) -> bytes:
    """
    Authenticate an exact market-data HTTP request.

    Required headers:
        X-Device-ID
        X-Timestamp
        X-Nonce
        X-Signature

    The HMAC covers the HTTP method, URL path, timestamp,
    device identifier, nonce, and exact raw request body.

    Query parameters are rejected.
    """

    if request.method != "POST":
        raise MarketDataAuthenticationError(
            "Only POST requests are accepted"
        )

    if request.url.query:
        raise MarketDataAuthenticationError(
            "Query parameters are not accepted"
        )

    content_type = request.headers.get(
        "content-type",
        "",
    ).split(";", 1)[0].strip().lower()

    if content_type != "application/json":
        raise MarketDataAuthenticationError(
            "Content-Type must be application/json"
        )

    source_id = request.headers.get("X-Device-ID")
    timestamp = request.headers.get("X-Timestamp")
    nonce = request.headers.get("X-Nonce")
    signature = request.headers.get("X-Signature")

    required_headers = {
        "X-Device-ID": source_id,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }

    missing_headers = [
        name
        for name, value in required_headers.items()
        if value is None or not value.strip()
    ]

    if missing_headers:
        raise MarketDataAuthenticationError(
            "Required authentication headers are missing"
        )

    if len(source_id) > 128:
        raise MarketDataAuthenticationError(
            "Invalid device identifier"
        )

    if len(timestamp) > 12:
        raise MarketDataAuthenticationError(
            "Invalid request timestamp"
        )

    if len(nonce) > 128:
        raise MarketDataAuthenticationError(
            "Invalid request nonce"
        )

    if len(signature) != 64:
        raise MarketDataAuthenticationError(
            "Invalid request signature"
        )

    content_length = request.headers.get("content-length")

    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise MarketDataAuthenticationError(
                "Invalid Content-Length header"
            ) from exc

        if (
            declared_length < 0
            or declared_length > MAX_REQUEST_BODY_BYTES
        ):
            raise MarketDataAuthenticationError(
                "Request body exceeds the allowed size"
            )

    raw_body = await request.body()

    if not raw_body:
        raise MarketDataAuthenticationError(
            "Request body cannot be empty"
        )

    if len(raw_body) > MAX_REQUEST_BODY_BYTES:
        raise MarketDataAuthenticationError(
            "Request body exceeds the allowed size"
        )

    try:
        payload = json.loads(
            raw_body,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (
        ValueError,
        UnicodeDecodeError,
    ) as exc:
        raise MarketDataAuthenticationError(
            "Request body must contain valid JSON without duplicate keys"
        ) from exc

    if not isinstance(payload, dict):
        raise MarketDataAuthenticationError(
            "Request JSON must be an object"
        )

    body_source_id = payload.get("source_id")

    if (
        not isinstance(body_source_id, str)
        or body_source_id != source_id.strip()
    ):
        raise MarketDataAuthenticationError(
            "Device identifier does not match the request body"
        )

    try:
        device_secret = (
            await device_registry.get_device_secret(source_id)
        )
    except DeviceRegistryError as exc:
        raise MarketDataAuthenticationError(
            "Trusted device registry is unavailable"
        ) from exc

    if device_secret is None:
        raise MarketDataAuthenticationError(
            "Device is unknown or inactive"
        )

    try:
        await verify_signed_request(
            raw_body=raw_body,
            method=request.method,
            path=request.url.path,
            source_id=source_id,
            timestamp=timestamp,
            nonce=nonce,
            signature=signature,
            device_secret=device_secret,
            nonce_store=nonce_store,
        )
    except SecurityVerificationError as exc:
        raise MarketDataAuthenticationError(
            "Market-data request authentication failed"
        ) from exc

    return raw_body
