import json
import time

import pytest
from starlette.requests import Request

from app.core.market_data_auth import (
    MAX_REQUEST_BODY_BYTES,
    MarketDataAuthenticationError,
    authenticate_market_data_request,
)
from app.core.market_data_security import (
    calculate_request_signature,
)


TEST_SECRET = "market-data-auth-test-secret-" + ("x" * 40)
TEST_SOURCE_ID = "MT5-DEMO-01"
TEST_PATH = "/api/v1/market-data/candles"


class FakeDeviceRegistry:
    def __init__(self, secret=TEST_SECRET):
        self.secret = secret
        self.requested_source_id = None

    async def get_device_secret(self, source_id):
        self.requested_source_id = source_id
        return self.secret


class FakeNonceStore:
    def __init__(self):
        self.nonces = set()

    async def claim_nonce(
        self,
        source_id,
        nonce,
        ttl_seconds,
    ):
        key = (source_id, nonce)

        if key in self.nonces:
            return False

        self.nonces.add(key)
        return True


def build_request(
    *,
    method="POST",
    path=TEST_PATH,
    body=None,
    headers=None,
    query_string=b"",
):
    request_body = b"{}" if body is None else body
    delivered = False

    async def receive():
        nonlocal delivered

        if delivered:
            return {
                "type": "http.request",
                "body": b"",
                "more_body": False,
            }

        delivered = True

        return {
            "type": "http.request",
            "body": request_body,
            "more_body": False,
        }

    header_items = []

    for key, value in (headers or {}).items():
        header_items.append(
            (
                key.lower().encode("latin-1"),
                value.encode("latin-1"),
            )
        )

    scope = {
        "type": "http",
        "asgi": {
            "version": "3.0",
            "spec_version": "2.3",
        },
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_string,
        "root_path": "",
        "headers": header_items,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }

    return Request(scope, receive)


def signed_headers(body, *, source_id=TEST_SOURCE_ID):
    timestamp = str(int(time.time()))
    nonce = "0123456789abcdef"

    signature = calculate_request_signature(
        secret=TEST_SECRET,
        method="POST",
        path=TEST_PATH,
        source_id=source_id,
        timestamp=timestamp,
        nonce=nonce,
        raw_body=body,
    )

    return {
        "Content-Type": "application/json",
        "X-Device-ID": source_id,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }


@pytest.mark.asyncio
async def test_valid_authenticated_request_is_accepted():
    body = json.dumps(
        {
            "source_id": TEST_SOURCE_ID,
            "candles": [],
        }
    ).encode("utf-8")

    request = build_request(
        body=body,
        headers=signed_headers(body),
    )

    result = await authenticate_market_data_request(
        request=request,
        device_registry=FakeDeviceRegistry(),
        nonce_store=FakeNonceStore(),
    )

    assert result == body


@pytest.mark.asyncio
async def test_non_post_method_is_rejected():
    request = build_request(method="GET")

    with pytest.raises(
        MarketDataAuthenticationError,
        match="Only POST",
    ):
        await authenticate_market_data_request(
            request=request,
            device_registry=FakeDeviceRegistry(),
            nonce_store=FakeNonceStore(),
        )


@pytest.mark.asyncio
async def test_query_parameters_are_rejected():
    request = build_request(
        query_string=b"debug=true",
    )

    with pytest.raises(
        MarketDataAuthenticationError,
        match="Query parameters",
    ):
        await authenticate_market_data_request(
            request=request,
            device_registry=FakeDeviceRegistry(),
            nonce_store=FakeNonceStore(),
        )


@pytest.mark.asyncio
async def test_non_json_content_type_is_rejected():
    request = build_request(
        headers={
            "Content-Type": "text/plain",
        },
    )

    with pytest.raises(
        MarketDataAuthenticationError,
        match="application/json",
    ):
        await authenticate_market_data_request(
            request=request,
            device_registry=FakeDeviceRegistry(),
            nonce_store=FakeNonceStore(),
        )


@pytest.mark.asyncio
async def test_missing_authentication_headers_are_rejected():
    request = build_request(
        headers={
            "Content-Type": "application/json",
        },
    )

    with pytest.raises(
        MarketDataAuthenticationError,
        match="headers are missing",
    ):
        await authenticate_market_data_request(
            request=request,
            device_registry=FakeDeviceRegistry(),
            nonce_store=FakeNonceStore(),
        )


@pytest.mark.asyncio
async def test_body_source_id_mismatch_is_rejected():
    body = json.dumps(
        {
            "source_id": "MT5-OTHER-01",
            "candles": [],
        }
    ).encode("utf-8")

    request = build_request(
        body=body,
        headers=signed_headers(body),
    )

    with pytest.raises(
        MarketDataAuthenticationError,
        match="does not match",
    ):
        await authenticate_market_data_request(
            request=request,
            device_registry=FakeDeviceRegistry(),
            nonce_store=FakeNonceStore(),
        )


@pytest.mark.asyncio
async def test_invalid_json_is_rejected():
    body = b'{"source_id":'

    request = build_request(
        body=body,
        headers=signed_headers(body),
    )

    with pytest.raises(
        MarketDataAuthenticationError,
        match="valid JSON",
    ):
        await authenticate_market_data_request(
            request=request,
            device_registry=FakeDeviceRegistry(),
            nonce_store=FakeNonceStore(),
        )


@pytest.mark.asyncio
async def test_duplicate_json_keys_are_rejected():
    body = (
        b'{"source_id":"MT5-DEMO-01",'
        b'"source_id":"MT5-OTHER-01"}'
    )

    request = build_request(
        body=body,
        headers=signed_headers(body),
    )

    with pytest.raises(
        MarketDataAuthenticationError,
        match="duplicate keys",
    ):
        await authenticate_market_data_request(
            request=request,
            device_registry=FakeDeviceRegistry(),
            nonce_store=FakeNonceStore(),
        )


@pytest.mark.asyncio
async def test_unknown_device_is_rejected():
    body = json.dumps(
        {
            "source_id": TEST_SOURCE_ID,
            "candles": [],
        }
    ).encode("utf-8")

    request = build_request(
        body=body,
        headers=signed_headers(body),
    )

    with pytest.raises(
        MarketDataAuthenticationError,
        match="unknown or inactive",
    ):
        await authenticate_market_data_request(
            request=request,
            device_registry=FakeDeviceRegistry(
                secret=None
            ),
            nonce_store=FakeNonceStore(),
        )


@pytest.mark.asyncio
async def test_oversized_declared_body_is_rejected():
    request = build_request(
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(
                MAX_REQUEST_BODY_BYTES + 1
            ),
        },
    )

    with pytest.raises(
        MarketDataAuthenticationError,
        match="exceeds the allowed size",
    ):
        await authenticate_market_data_request(
            request=request,
            device_registry=FakeDeviceRegistry(),
            nonce_store=FakeNonceStore(),
        )


@pytest.mark.asyncio
async def test_empty_body_is_rejected():
    request = build_request(
        body=b"",
        headers={
            "Content-Type": "application/json",
        },
    )

    with pytest.raises(
        MarketDataAuthenticationError,
        match="cannot be empty",
    ):
        await authenticate_market_data_request(
            request=request,
            device_registry=FakeDeviceRegistry(),
            nonce_store=FakeNonceStore(),
        )
