
import pytest

from app.core.device_registry import (
    DeviceRegistry,
    DeviceRegistryError,
)
from app.core.device_secret_crypto import (
    DeviceSecretCipher,
)


KEY = "ab" * 32
SOURCE_ID = "MT5-DEMO-01"
SECRET = "gold-bot-device-secret-" + ("x" * 40)


class FakeResult:
    def __init__(self, rows=None):
        self.rows = rows or []

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(
        self,
        responses=None,
        error=None,
    ):
        self.responses = list(responses or [])
        self.error = error
        self.executed = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    def execute(self, query, params=None):
        self.executed.append((query, params))

        if self.error is not None:
            raise self.error

        if self.responses:
            response = self.responses.pop(0)

            if isinstance(response, Exception):
                raise response

            return response

        return FakeResult()


class FakeCipher(DeviceSecretCipher):
    def __init__(self):
        self.real_cipher = DeviceSecretCipher(KEY)
        self.decrypt_calls = []

    def encrypt(self, *, source_id, secret):
        return self.real_cipher.encrypt(
            source_id=source_id,
            secret=secret,
        )

    def decrypt(self, *, source_id, encrypted_secret):
        self.decrypt_calls.append(
            (source_id, encrypted_secret)
        )
        return self.real_cipher.decrypt(
            source_id=source_id,
            encrypted_secret=encrypted_secret,
        )


def make_registry(cipher=None):
    return DeviceRegistry(
        database_url=(
            "postgresql+psycopg://user:pass@localhost/db"
        ),
        cipher=cipher or DeviceSecretCipher(KEY),
    )


def make_encrypted_secret(
    source_id=SOURCE_ID,
    secret=SECRET,
):
    return DeviceSecretCipher(KEY).encrypt(
        source_id=source_id,
        secret=secret,
    )


def valid_schema_responses():
    return [
        FakeResult([
            ("source_id",),
            ("encrypted_secret",),
            ("is_active",),
            ("created_at",),
            ("updated_at",),
            ("revoked_at",),
        ]),
        FakeResult([
            ("gold_bot_trusted_devices_pkey", "p"),
        ]),
        FakeResult([
            ("idx_trusted_devices_active", True, True),
        ]),
    ]


def test_valid_postgresql_url_is_accepted():
    registry = make_registry()

    assert registry._database_url == (
        "postgresql://user:pass@localhost/db"
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "sqlite:///database.db",
        "mysql://localhost/db",
        "https://example.com",
        None,
        123,
    ],
)
def test_non_postgresql_urls_are_rejected(url):
    with pytest.raises(DeviceRegistryError):
        DeviceRegistry(
            database_url=url,
            cipher=DeviceSecretCipher(KEY),
        )


def test_postgresql_url_is_trimmed():
    registry = DeviceRegistry(
        database_url="  postgresql://localhost/db  ",
        cipher=DeviceSecretCipher(KEY),
    )

    assert registry._database_url == (
        "postgresql://localhost/db"
    )


def test_invalid_cipher_type_is_rejected():
    with pytest.raises(
        DeviceRegistryError,
        match="valid device-secret cipher",
    ):
        DeviceRegistry(
            database_url="postgresql://localhost/db",
            cipher=object(),
        )


def test_valid_source_id_is_normalized():
    assert DeviceRegistry._validate_source_id(
        "  MT5-DEMO-01  "
    ) == SOURCE_ID


@pytest.mark.parametrize(
    "source_id",
    [
        "",
        " ",
        "\n",
        "\r",
        "DEVICE\nATTACK",
        "x" * 129,
        None,
        123,
        b"MT5-DEMO-01",
    ],
)
def test_invalid_source_ids_are_rejected(source_id):
    with pytest.raises(DeviceRegistryError):
        DeviceRegistry._validate_source_id(source_id)


def test_initialize_accepts_valid_schema(monkeypatch):
    registry = make_registry()
    connection = FakeConnection(
        responses=valid_schema_responses()
    )

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    registry._initialize_sync()

    assert len(connection.executed) == 3
    assert connection.closed is True


def test_initialize_rejects_missing_table(monkeypatch):
    registry = make_registry()

    connection = FakeConnection(
        responses=[
            FakeResult(),
            FakeResult(),
            FakeResult(),
        ]
    )

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    with pytest.raises(
        DeviceRegistryError,
        match="Missing table",
    ):
        registry._initialize_sync()


def test_initialize_rejects_missing_columns(monkeypatch):
    registry = make_registry()

    connection = FakeConnection(
        responses=[
            FakeResult([
                ("source_id",),
                ("encrypted_secret",),
            ]),
            FakeResult([
                ("gold_bot_trusted_devices_pkey", "p"),
            ]),
            FakeResult([
                ("idx_trusted_devices_active", True, True),
            ]),
        ]
    )

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    with pytest.raises(
        DeviceRegistryError,
        match="Missing trusted-device columns",
    ):
        registry._initialize_sync()


def test_initialize_rejects_missing_primary_key(monkeypatch):
    registry = make_registry()

    responses = valid_schema_responses()
    responses[1] = FakeResult()

    connection = FakeConnection(responses=responses)

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    with pytest.raises(
        DeviceRegistryError,
        match="primary key",
    ):
        registry._initialize_sync()


def test_initialize_rejects_wrong_primary_key_type(monkeypatch):
    registry = make_registry()

    responses = valid_schema_responses()
    responses[1] = FakeResult([
        ("gold_bot_trusted_devices_pkey", "u"),
    ])

    connection = FakeConnection(responses=responses)

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    with pytest.raises(
        DeviceRegistryError,
        match="primary key",
    ):
        registry._initialize_sync()


def test_initialize_rejects_missing_active_index(monkeypatch):
    registry = make_registry()

    responses = valid_schema_responses()
    responses[2] = FakeResult()

    connection = FakeConnection(responses=responses)

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    with pytest.raises(
        DeviceRegistryError,
        match="Missing trusted-device active index",
    ):
        registry._initialize_sync()


@pytest.mark.parametrize(
    "index_state",
    [
        (False, True),
        (True, False),
        (False, False),
    ],
)
def test_initialize_rejects_invalid_active_index(
    monkeypatch,
    index_state,
):
    registry = make_registry()

    responses = valid_schema_responses()
    responses[2] = FakeResult([
        (
            "idx_trusted_devices_active",
            index_state[0],
            index_state[1],
        ),
    ])

    connection = FakeConnection(responses=responses)

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    with pytest.raises(
        DeviceRegistryError,
        match="Invalid trusted-device active index",
    ):
        registry._initialize_sync()


def test_initialize_wraps_database_errors(monkeypatch):
    registry = make_registry()

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: FakeConnection(
            error=RuntimeError("private database details")
        ),
    )

    with pytest.raises(
        DeviceRegistryError,
        match="Unable to initialize trusted device registry",
    ) as exc:
        registry._initialize_sync()

    assert isinstance(exc.value.__cause__, RuntimeError)
    assert "private database details" not in str(exc.value)


@pytest.mark.asyncio
async def test_initialize_runs_in_async_method(monkeypatch):
    registry = make_registry()
    called = []

    def fake_initialize():
        called.append(True)

    monkeypatch.setattr(
        registry,
        "_initialize_sync",
        fake_initialize,
    )

    await registry.initialize()

    assert called == [True]


@pytest.mark.asyncio
async def test_register_device_accepts_valid_encrypted_secret(
    monkeypatch,
):
    cipher = FakeCipher()
    registry = make_registry(cipher)

    encrypted = make_encrypted_secret()
    connection = FakeConnection(
        responses=[FakeResult([(SOURCE_ID,)])]
    )

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    result = await registry.register_device(
        source_id=SOURCE_ID,
        encrypted_secret=encrypted,
    )

    assert result is True
    assert len(connection.executed) == 1

    query, params = connection.executed[0]

    assert "INSERT INTO gold_bot_trusted_devices" in query
    assert "ON CONFLICT (source_id) DO NOTHING" in query
    assert params == (SOURCE_ID, encrypted)


@pytest.mark.asyncio
async def test_register_device_returns_false_for_duplicate(
    monkeypatch,
):
    registry = make_registry()

    connection = FakeConnection(
        responses=[FakeResult()]
    )

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    result = await registry.register_device(
        source_id=SOURCE_ID,
        encrypted_secret=make_encrypted_secret(),
    )

    assert result is False


@pytest.mark.asyncio
async def test_register_device_rejects_invalid_encrypted_secret(
    monkeypatch,
):
    registry = make_registry()

    called = []

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: called.append(True),
    )

    with pytest.raises(
        DeviceRegistryError,
        match="encryption validation failed",
    ):
        await registry.register_device(
            source_id=SOURCE_ID,
            encrypted_secret="invalid-token",
        )

    assert called == []


@pytest.mark.asyncio
async def test_register_device_rejects_wrong_source_binding(
    monkeypatch,
):
    registry = make_registry()

    encrypted = make_encrypted_secret(
        source_id="ANOTHER-DEVICE"
    )

    with pytest.raises(DeviceRegistryError):
        await registry.register_device(
            source_id=SOURCE_ID,
            encrypted_secret=encrypted,
        )


@pytest.mark.asyncio
async def test_register_device_rejects_invalid_source_id():
    registry = make_registry()

    with pytest.raises(DeviceRegistryError):
        await registry.register_device(
            source_id="",
            encrypted_secret=make_encrypted_secret(),
        )


@pytest.mark.asyncio
async def test_register_device_wraps_database_errors(monkeypatch):
    registry = make_registry()

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: FakeConnection(
            error=RuntimeError("database failure")
        ),
    )

    with pytest.raises(
        DeviceRegistryError,
        match="Unable to register trusted device",
    ):
        await registry.register_device(
            source_id=SOURCE_ID,
            encrypted_secret=make_encrypted_secret(),
        )


@pytest.mark.asyncio
async def test_get_device_secret_returns_decrypted_secret(
    monkeypatch,
):
    registry = make_registry()

    encrypted = make_encrypted_secret()

    connection = FakeConnection(
        responses=[FakeResult([(encrypted,)])]
    )

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    result = await registry.get_device_secret(SOURCE_ID)

    assert result == SECRET.encode("utf-8")

    query, params = connection.executed[0]

    assert "is_active = TRUE" in query
    assert "revoked_at IS NULL" in query
    assert params == (SOURCE_ID,)


@pytest.mark.asyncio
async def test_get_device_secret_returns_none_for_unknown_device(
    monkeypatch,
):
    registry = make_registry()

    connection = FakeConnection(
        responses=[FakeResult()]
    )

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    result = await registry.get_device_secret(SOURCE_ID)

    assert result is None


@pytest.mark.asyncio
async def test_get_device_secret_rejects_invalid_source_id():
    registry = make_registry()

    with pytest.raises(DeviceRegistryError):
        await registry.get_device_secret("")


@pytest.mark.asyncio
async def test_get_device_secret_wraps_database_errors(monkeypatch):
    registry = make_registry()

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: FakeConnection(
            error=RuntimeError("database failure")
        ),
    )

    with pytest.raises(
        DeviceRegistryError,
        match="Unable to retrieve trusted device",
    ):
        await registry.get_device_secret(SOURCE_ID)


@pytest.mark.asyncio
async def test_get_device_secret_rejects_tampered_stored_secret(
    monkeypatch,
):
    registry = make_registry()

    connection = FakeConnection(
        responses=[FakeResult([("v1:invalid",)])]
    )

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    with pytest.raises(
        DeviceRegistryError,
        match="could not be authenticated",
    ):
        await registry.get_device_secret(SOURCE_ID)


@pytest.mark.asyncio
async def test_revoke_device_updates_active_record(monkeypatch):
    registry = make_registry()

    connection = FakeConnection(
        responses=[FakeResult([(SOURCE_ID,)])]
    )

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    result = await registry.revoke_device(SOURCE_ID)

    assert result is True

    query, params = connection.executed[0]

    assert "SET" in query
    assert "is_active = FALSE" in query
    assert "revoked_at = CURRENT_TIMESTAMP" in query
    assert "updated_at = CURRENT_TIMESTAMP" in query
    assert "WHERE source_id = %s" in query
    assert "AND is_active = TRUE" in query
    assert "RETURNING source_id" in query
    assert params == (SOURCE_ID,)


@pytest.mark.asyncio
async def test_revoke_device_returns_false_when_not_active(
    monkeypatch,
):
    registry = make_registry()

    connection = FakeConnection(
        responses=[FakeResult()]
    )

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    result = await registry.revoke_device(SOURCE_ID)

    assert result is False


@pytest.mark.asyncio
async def test_revoke_device_rejects_invalid_source_id():
    registry = make_registry()

    with pytest.raises(DeviceRegistryError):
        await registry.revoke_device("")


@pytest.mark.asyncio
async def test_revoke_device_wraps_database_errors(monkeypatch):
    registry = make_registry()

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: FakeConnection(
            error=RuntimeError("database failure")
        ),
    )

    with pytest.raises(
        DeviceRegistryError,
        match="Unable to revoke trusted device",
    ):
        await registry.revoke_device(SOURCE_ID)


@pytest.mark.asyncio
async def test_revoked_device_is_not_returned(monkeypatch):
    registry = make_registry()

    connection = FakeConnection(
        responses=[FakeResult()]
    )

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    result = await registry.get_device_secret(SOURCE_ID)

    assert result is None

    query, _ = connection.executed[0]

    assert "is_active = TRUE" in query
    assert "revoked_at IS NULL" in query


@pytest.mark.asyncio
async def test_register_device_does_not_store_plaintext_secret(
    monkeypatch,
):
    registry = make_registry()

    encrypted = make_encrypted_secret()

    connection = FakeConnection(
        responses=[FakeResult([(SOURCE_ID,)])]
    )

    monkeypatch.setattr(
        registry,
        "_connect",
        lambda: connection,
    )

    await registry.register_device(
        source_id=SOURCE_ID,
        encrypted_secret=encrypted,
    )

    _, params = connection.executed[0]

    assert SECRET not in params
    assert encrypted in params
