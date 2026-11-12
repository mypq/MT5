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
@pytest.mark.parametrize(
    "secret",
    [
        "short",
        b"x" * 31,
        None,
        123,
    ],
)
def test_invalid_device_secret_is_rejected(secret):
    with pytest.raises(SecurityVerificationError):
        calculate_request_signature(
            secret=secret,
            method=TEST_METHOD,
            path=TEST_PATH,
            source_id=TEST_SOURCE_ID,
            timestamp="1800000000",
            nonce=TEST_NONCE,
            raw_body=TEST_BODY,
        )


@pytest.mark.parametrize(
    "source_id",
    [
        "",
        "   ",
        "x" * 129,
        "invalid\nsource",
        "invalid\rsource",
        None,
        123,
    ],
)
def test_invalid_source_id_is_rejected(source_id):
    with pytest.raises(SecurityVerificationError):
        calculate_request_signature(
            secret=TEST_SECRET,
            method=TEST_METHOD,
            path=TEST_PATH,
            source_id=source_id,
            timestamp="1800000000",
            nonce=TEST_NONCE,
            raw_body=TEST_BODY,
        )


@pytest.mark.parametrize(
    "timestamp",
    [
        "",
        "abc",
        "-1",
        "+123",
        "01",
        "1.5",
        "1" * 13,
        None,
    ],
)
def test_invalid_timestamp_format_is_rejected(timestamp):
    with pytest.raises(SecurityVerificationError):
        calculate_request_signature(
            secret=TEST_SECRET,
            method=TEST_METHOD,
            path=TEST_PATH,
            source_id=TEST_SOURCE_ID,
            timestamp=timestamp,
            nonce=TEST_NONCE,
            raw_body=TEST_BODY,
        )


@pytest.mark.parametrize(
    "nonce",
    [
        "",
        "short",
        "x" * 129,
        "invalid nonce value",
        "invalid\nnonce",
        None,
    ],
)
def test_invalid_nonce_is_rejected(nonce):
    with pytest.raises(SecurityVerificationError):
        calculate_request_signature(
            secret=TEST_SECRET,
            method=TEST_METHOD,
            path=TEST_PATH,
            source_id=TEST_SOURCE_ID,
            timestamp="1800000000",
            nonce=nonce,
            raw_body=TEST_BODY,
        )


@pytest.mark.parametrize(
    "method",
    [
        "",
        "po",
        "POST ",
        "post",
        "POST\n",
        "POST/GET",
        None,
    ],
)
def test_invalid_http_method_is_rejected(method):
    with pytest.raises(SecurityVerificationError):
        calculate_request_signature(
            secret=TEST_SECRET,
            method=method,
            path=TEST_PATH,
            source_id=TEST_SOURCE_ID,
            timestamp="1800000000",
            nonce=TEST_NONCE,
            raw_body=TEST_BODY,
        )


@pytest.mark.parametrize(
    "path",
    [
        "",
        "api/v1/candles",
        "/api/v1/candles?x=1",
        "/api/v1/candles#section",
        "/api/v1/\ncandles",
        "/api/v1/\rcandles",
        "/" + ("x" * 512),
        None,
    ],
)
def test_invalid_request_path_is_rejected(path):
    with pytest.raises(SecurityVerificationError):
        calculate_request_signature(
            secret=TEST_SECRET,
            method=TEST_METHOD,
            path=path,
            source_id=TEST_SOURCE_ID,
            timestamp="1800000000",
            nonce=TEST_NONCE,
            raw_body=TEST_BODY,
        )


def test_non_bytes_request_body_is_rejected():
    with pytest.raises(SecurityVerificationError):
        calculate_request_signature(
            secret=TEST_SECRET,
            method=TEST_METHOD,
            path=TEST_PATH,
            source_id=TEST_SOURCE_ID,
            timestamp="1800000000",
            nonce=TEST_NONCE,
            raw_body="not-bytes",
        )


@pytest.mark.asyncio
async def test_uppercase_signature_is_accepted(monkeypatch):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    request = signed_request(timestamp=str(current_time))
    request["signature"] = request["signature"].upper()

    await verify_signed_request(
        **request,
        nonce_store=MemoryNonceStore(),
    )


@pytest.mark.asyncio
async def test_source_id_surrounding_whitespace_is_normalized(monkeypatch):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    request = signed_request(timestamp=str(current_time))
    request["source_id"] = f" {TEST_SOURCE_ID} "

    store = MemoryNonceStore()

    await verify_signed_request(
        **request,
        nonce_store=store,
    )

    assert (TEST_SOURCE_ID, TEST_NONCE) in store.nonces


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "max_age_seconds",
    [0, -1, 301, 1000],
)
async def test_invalid_max_age_configuration_is_rejected(
    monkeypatch,
    max_age_seconds,
):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    request = signed_request(timestamp=str(current_time))

    with pytest.raises(
        SecurityVerificationError,
        match="maximum request age",
    ):
        await verify_signed_request(
            **request,
            nonce_store=MemoryNonceStore(),
            max_age_seconds=max_age_seconds,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "future_skew_seconds",
    [-1, 31, 100],
)
async def test_invalid_future_skew_configuration_is_rejected(
    monkeypatch,
    future_skew_seconds,
):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    request = signed_request(timestamp=str(current_time))

    with pytest.raises(
        SecurityVerificationError,
        match="future clock-skew",
    ):
        await verify_signed_request(
            **request,
            nonce_store=MemoryNonceStore(),
            future_skew_seconds=future_skew_seconds,
        )


@pytest.mark.asyncio
async def test_nonce_store_receives_expected_ttl(monkeypatch):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    class RecordingNonceStore:
        def __init__(self):
            self.received = None

        async def claim_nonce(
            self,
            source_id,
            nonce,
            ttl_seconds,
        ):
            self.received = (
                source_id,
                nonce,
                ttl_seconds,
            )
            return True

    store = RecordingNonceStore()
    request = signed_request(timestamp=str(current_time))

    await verify_signed_request(
        **request,
        nonce_store=store,
        max_age_seconds=30,
        future_skew_seconds=5,
    )

    assert store.received == (
        TEST_SOURCE_ID,
        TEST_NONCE,
        36,
    )


@pytest.mark.asyncio
async def test_nonce_store_false_result_rejects_request(monkeypatch):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    class RejectingNonceStore:
        async def claim_nonce(
            self,
            source_id,
            nonce,
            ttl_seconds,
        ):
            return False

    request = signed_request(timestamp=str(current_time))

    with pytest.raises(
        SecurityVerificationError,
        match="already been used",
    ):
        await verify_signed_request(
            **request,
            nonce_store=RejectingNonceStore(),
        )


@pytest.mark.asyncio
async def test_nonce_store_non_boolean_result_is_rejected(monkeypatch):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    class InvalidNonceStore:
        async def claim_nonce(
            self,
            source_id,
            nonce,
            ttl_seconds,
        ):
            return 1

    request = signed_request(timestamp=str(current_time))

    with pytest.raises(
        SecurityVerificationError,
        match="already been used",
    ):
        await verify_signed_request(
            **request,
            nonce_store=InvalidNonceStore(),
        )


@pytest.mark.asyncio
async def test_invalid_signature_format_is_rejected(monkeypatch):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    request = signed_request(timestamp=str(current_time))
    request["signature"] = "not-a-valid-signature"

    with pytest.raises(
        SecurityVerificationError,
        match="signature format",
    ):
        await verify_signed_request(
            **request,
            nonce_store=MemoryNonceStore(),
        )


@pytest.mark.asyncio
async def test_nonce_is_not_claimed_when_signature_is_invalid(
    monkeypatch,
):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    store = MemoryNonceStore()
    request = signed_request(timestamp=str(current_time))
    request["signature"] = "0" * 64

    with pytest.raises(SecurityVerificationError):
        await verify_signed_request(
            **request,
            nonce_store=store,
        )

    assert store.nonces == set()


@pytest.mark.asyncio
async def test_nonce_is_not_claimed_when_timestamp_is_expired(
    monkeypatch,
):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    store = MemoryNonceStore()
    request = signed_request(
        timestamp=str(current_time - 31)
    )

    with pytest.raises(SecurityVerificationError):
        await verify_signed_request(
            **request,
            nonce_store=store,
        )

    assert store.nonces == set()


@pytest.mark.asyncio
async def test_nonce_is_not_claimed_when_timestamp_is_in_future(
    monkeypatch,
):
    current_time = 1_800_000_000
    monkeypatch.setattr(
        market_data_security.time,
        "time",
        lambda: current_time,
    )

    store = MemoryNonceStore()
    request = signed_request(
        timestamp=str(current_time + 6)
    )

    with pytest.raises(SecurityVerificationError):
        await verify_signed_request(
            **request,
            nonce_store=store,
        )

    assert store.nonces == set()
