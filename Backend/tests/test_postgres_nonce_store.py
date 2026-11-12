
import pytest

from app.core.postgres_nonce_store import (
    NonceStoreError,
    PostgresNonceStore,
)


TEST_URL = "postgresql://test:test@localhost/test_db"
SOURCE = "MT5-DEMO-01"
NONCE = "0123456789abcdef"


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(
        self,
        *,
        columns=None,
        constraints=None,
        indexes=None,
        claim_result=True,
        cleanup_rows=None,
        fail_on=None,
    ):
        self.columns = columns
        self.constraints = constraints
        self.indexes = indexes
        self.claim_result = claim_result
        self.cleanup_rows = cleanup_rows or []
        self.fail_on = fail_on
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, parameters=None):
        self.statements.append((query, parameters))

        if self.fail_on and self.fail_on in query:
            raise RuntimeError("database failure")

        if "information_schema.columns" in query:
            return FakeCursor(self.columns or [])

        if "FROM pg_constraint" in query:
            return FakeCursor(self.constraints or [])

        if "FROM pg_index" in query:
            return FakeCursor(self.indexes or [])

        if "INSERT INTO gold_bot_market_data_nonces" in query:
            return FakeCursor(
                [(1,)] if self.claim_result else []
            )

        if "DELETE FROM gold_bot_market_data_nonces" in query:
            return FakeCursor(self.cleanup_rows)

        raise AssertionError("Unexpected SQL statement")


def valid_connection():
    return FakeConnection(
        columns=[
            ("source_id",),
            ("nonce",),
            ("expires_at",),
            ("created_at",),
        ],
        constraints=[
            ("gold_bot_market_data_nonces_pkey", "p"),
        ],
        indexes=[
            (
                "idx_market_data_nonces_expires_at",
                True,
                True,
                "CREATE INDEX ON gold_bot_market_data_nonces (expires_at)",
            ),
        ],
    )


def make_store(monkeypatch, connection):
    store = PostgresNonceStore(TEST_URL)
    monkeypatch.setattr(
        store,
        "_connect",
        lambda: connection,
    )
    return store


def test_valid_postgresql_url_is_accepted():
    store = PostgresNonceStore(TEST_URL)
    assert store._database_url == TEST_URL


def test_psycopg_url_is_normalized():
    store = PostgresNonceStore(
        "postgresql+psycopg://test:test@localhost/test_db"
    )
    assert store._database_url == TEST_URL


@pytest.mark.parametrize(
    "url",
    [None, 123, "", "sqlite:///test.db", "mysql://localhost/db"],
)
def test_invalid_database_url_is_rejected(url):
    with pytest.raises(NonceStoreError):
        PostgresNonceStore(url)


@pytest.mark.asyncio
async def test_initialize_accepts_valid_schema(monkeypatch):
    connection = valid_connection()
    store = make_store(monkeypatch, connection)

    await store.initialize()

    assert len(connection.statements) == 3


@pytest.mark.asyncio
async def test_initialize_rejects_missing_table(monkeypatch):
    connection = FakeConnection()
    store = make_store(monkeypatch, connection)

    with pytest.raises(NonceStoreError, match="Missing table"):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_missing_column(monkeypatch):
    connection = valid_connection()
    connection.columns = [
        ("source_id",),
        ("nonce",),
        ("created_at",),
    ]
    store = make_store(monkeypatch, connection)

    with pytest.raises(
        NonceStoreError,
        match="Missing nonce columns",
    ):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_missing_primary_key(monkeypatch):
    connection = valid_connection()
    connection.constraints = []
    store = make_store(monkeypatch, connection)

    with pytest.raises(
        NonceStoreError,
        match="primary key",
    ):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_missing_expiry_index(monkeypatch):
    connection = valid_connection()
    connection.indexes = []
    store = make_store(monkeypatch, connection)

    with pytest.raises(
        NonceStoreError,
        match="Missing nonce expiry index",
    ):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_invalid_expiry_index(monkeypatch):
    connection = valid_connection()
    connection.indexes = [
        (
            "idx_market_data_nonces_expires_at",
            False,
            True,
            "CREATE INDEX ON table (expires_at)",
        ),
    ]
    store = make_store(monkeypatch, connection)

    with pytest.raises(
        NonceStoreError,
        match="Invalid nonce expiry index",
    ):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialize_never_creates_or_drops_schema(monkeypatch):
    connection = valid_connection()
    store = make_store(monkeypatch, connection)

    await store.initialize()

    sql = "\n".join(
        query for query, _ in connection.statements
    ).upper()

    assert "CREATE TABLE" not in sql
    assert "CREATE INDEX" not in sql
    assert "ALTER TABLE" not in sql
    assert "DROP TABLE" not in sql


@pytest.mark.asyncio
async def test_initialize_wraps_database_failure(monkeypatch):
    connection = FakeConnection(
        fail_on="information_schema.columns"
    )
    store = make_store(monkeypatch, connection)

    with pytest.raises(
        NonceStoreError,
        match="Unable to initialize",
    ) as error:
        await store.initialize()

    assert isinstance(error.value.__cause__, RuntimeError)


@pytest.mark.parametrize(
    "source_id,nonce,ttl",
    [
        ("", NONCE, 30),
        ("   ", NONCE, 30),
        ("x" * 129, NONCE, 30),
        ("bad\nsource", NONCE, 30),
        (SOURCE, "short", 30),
        (SOURCE, "invalid nonce!", 30),
        (SOURCE, "x" * 129, 30),
        (SOURCE, NONCE, 0),
        (SOURCE, NONCE, -1),
        (SOURCE, NONCE, 3601),
        (SOURCE, NONCE, True),
        (SOURCE, NONCE, 30.5),
    ],
)
def test_invalid_nonce_claim_is_rejected(
    source_id,
    nonce,
    ttl,
):
    with pytest.raises(NonceStoreError):
        PostgresNonceStore._validate_claim(
            source_id,
            nonce,
            ttl,
        )


def test_source_id_is_normalized():
    result = PostgresNonceStore._validate_claim(
        f" {SOURCE} ",
        NONCE,
        30,
    )
    assert result == SOURCE


@pytest.mark.asyncio
async def test_new_nonce_is_claimed(monkeypatch):
    connection = valid_connection()
    connection.claim_result = True
    store = make_store(monkeypatch, connection)

    result = await store.claim_nonce(
        SOURCE,
        NONCE,
        30,
    )

    assert result is True
    assert len(connection.statements) == 1
    assert "ON CONFLICT" in connection.statements[0][0]
    assert "RETURNING 1" in connection.statements[0][0]


@pytest.mark.asyncio
async def test_active_duplicate_nonce_is_rejected(monkeypatch):
    connection = valid_connection()
    connection.claim_result = False
    store = make_store(monkeypatch, connection)

    result = await store.claim_nonce(
        SOURCE,
        NONCE,
        30,
    )

    assert result is False


@pytest.mark.asyncio
async def test_nonce_claim_database_failure_is_wrapped(monkeypatch):
    connection = FakeConnection(
        fail_on="INSERT INTO gold_bot_market_data_nonces"
    )
    store = make_store(monkeypatch, connection)

    with pytest.raises(
        NonceStoreError,
        match="nonce claim failed",
    ) as error:
        await store.claim_nonce(SOURCE, NONCE, 30)

    assert isinstance(error.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_cleanup_returns_deleted_count(monkeypatch):
    connection = valid_connection()
    connection.cleanup_rows = [(1,), (1,), (1,)]
    store = make_store(monkeypatch, connection)

    result = await store.cleanup_expired(100)

    assert result == 3
    assert "DELETE FROM gold_bot_market_data_nonces" in (
        connection.statements[0][0]
    )


@pytest.mark.parametrize(
    "batch_size",
    [0, -1, 10001, True, 1.5, "100"],
)
def test_invalid_cleanup_batch_size_is_rejected(batch_size):
    with pytest.raises(NonceStoreError):
        PostgresNonceStore._validate_claim(
            SOURCE,
            NONCE,
            30,
        )
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 10000
        ):
            raise NonceStoreError(
                "Cleanup batch size must be between 1 and 10000"
            )


@pytest.mark.asyncio
async def test_cleanup_database_failure_is_wrapped(monkeypatch):
    connection = FakeConnection(
        fail_on="DELETE FROM gold_bot_market_data_nonces"
    )
    store = make_store(monkeypatch, connection)

    with pytest.raises(
        NonceStoreError,
        match="nonce cleanup failed",
    ):
        await store.cleanup_expired()
