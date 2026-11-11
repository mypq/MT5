import pytest

from app.core.device_secret_crypto import (
    DeviceSecretCipher,
    DeviceSecretEncryptionError,
)
from app.core.market_data_security import (
    SecurityVerificationError,
    calculate_request_signature,
    verify_signed_request,
)
from app.core import market_data_security


TEST_SECRET = "gold-bot-test-secret-" + ("x" * 40)
TEST_SOURCE_ID = "MT5-DEMO-01"
TEST_METHOD = "POST"
TEST_PATH = "/api/v1/market-data/candles"
TEST_BODY = b'{"source_id":"MT5-DEMO-01","candles":[]}'
TEST_NONCE = "0123456789abcdef"


class MemoryNonceStore:
    """Atomic-in-process nonce store for isolated unit tests."""

    def __init__(self):
        self.nonces = set()

    async def claim_nonce(
        self,
        source_id: str,
        nonce: str,
        ttl_seconds: int,
    ) -> bool:
        key = (source_id, nonce)

        if key in self.nonces:
            return False

        self.nonces.add(key)
        return True


class FailingNonceStore:
    async def claim_nonce(
        self,
        source_id: str,
        nonce: str,
        ttl_seconds: int,
    ) -> bool:
        raise RuntimeError("Simulated database outage")


def signed_request(
    *,
    timestamp: str,
    nonce: str = TEST_NONCE,
    body: bytes = TEST_BODY,
) -> dict:
    signature = calculate_request_signature(
        secret=TEST_SECRET,
        method=TEST_METHOD,
        path=TEST_PATH,
        source_id=TEST_SOURCE_ID,
        timestamp=timestamp,
        nonce=nonce,
        raw_body=body,
    )

    return {
        "raw_body": body,
        "method": TEST_METHOD,
        "path": TEST_PATH,
        "source_id": TEST_SOURCE_ID,
        "timestamp": timestamp,
        "nonce": nonce,
        "signature": signature,
        "device_secret": TEST_SECRET,
    }


@pytest.mark.asyncio
async def test_valid_signed_request_is_accepted(monkeypatch):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    store = MemoryNonceStore()
    request = signed_request(timestamp=str(current_time))

    await verify_signed_request(
        **request,
        nonce_store=store,
    )

    assert (TEST_SOURCE_ID, TEST_NONCE) in store.nonces


@pytest.mark.asyncio
async def test_replayed_nonce_is_rejected(monkeypatch):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    store = MemoryNonceStore()
    request = signed_request(timestamp=str(current_time))

    await verify_signed_request(
        **request,
        nonce_store=store,
    )

    with pytest.raises(
        SecurityVerificationError,
        match="already been used",
    ):
        await verify_signed_request(
            **request,
            nonce_store=store,
        )


@pytest.mark.asyncio
async def test_modified_body_is_rejected(monkeypatch):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    request = signed_request(timestamp=str(current_time))
    request["raw_body"] = b'{"source_id":"ATTACKER"}'

    with pytest.raises(
        SecurityVerificationError,
        match="signature verification failed",
    ):
        await verify_signed_request(
            **request,
            nonce_store=MemoryNonceStore(),
        )


@pytest.mark.asyncio
async def test_expired_request_is_rejected(monkeypatch):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    request = signed_request(
        timestamp=str(current_time - 31)
    )

    with pytest.raises(
        SecurityVerificationError,
        match="has expired",
    ):
        await verify_signed_request(
            **request,
            nonce_store=MemoryNonceStore(),
            max_age_seconds=30,
        )


@pytest.mark.asyncio
async def test_future_request_is_rejected(monkeypatch):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    request = signed_request(
        timestamp=str(current_time + 6)
    )

    with pytest.raises(
        SecurityVerificationError,
        match="in the future",
    ):
        await verify_signed_request(
            **request,
            nonce_store=MemoryNonceStore(),
            future_skew_seconds=5,
        )


@pytest.mark.asyncio
async def test_nonce_store_failure_rejects_request(monkeypatch):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    request = signed_request(timestamp=str(current_time))

    with pytest.raises(
        SecurityVerificationError,
        match="Replay protection store is unavailable",
    ):
        await verify_signed_request(
            **request,
            nonce_store=FailingNonceStore(),
        )


def test_device_secret_encryption_round_trip():
    cipher = DeviceSecretCipher("ab" * 32)
    secret = "mt5-device-secret-" + ("z" * 40)

    encrypted = cipher.encrypt(
        source_id=TEST_SOURCE_ID,
        secret=secret,
    )

    decrypted = cipher.decrypt(
        source_id=TEST_SOURCE_ID,
        encrypted_secret=encrypted,
    )

    assert decrypted == secret.encode("utf-8")


def test_device_secret_cannot_be_decrypted_for_another_source():
    cipher = DeviceSecretCipher("ab" * 32)

    encrypted = cipher.encrypt(
        source_id=TEST_SOURCE_ID,
        secret=TEST_SECRET,
    )

    with pytest.raises(DeviceSecretEncryptionError):
        cipher.decrypt(
            source_id="MT5-OTHER-01",
            encrypted_secret=encrypted,
        )


def test_invalid_encryption_key_is_rejected():
    with pytest.raises(DeviceSecretEncryptionError):
        DeviceSecretCipher("not-a-valid-key")


def test_short_device_secret_is_rejected():
    cipher = DeviceSecretCipher("ab" * 32)

    with pytest.raises(DeviceSecretEncryptionError):
        cipher.encrypt(
            source_id=TEST_SOURCE_ID,
            secret="too-short",
        )
