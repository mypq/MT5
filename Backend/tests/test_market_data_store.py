import hashlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.core.market_data_store import (
    MarketDataMessageConflictError,
    MarketDataStore,
    MarketDataStoreError,
)


TEST_SOURCE_ID = "MT5-DEMO-01"


def build_message(
    *,
    source_id=TEST_SOURCE_ID,
    message_id=None,
    raw_body=b'{"test":"market-data"}',
    sent_at=None,
    payload=None,
):
    resolved_message_id = message_id or uuid4()

    resolved_payload = payload or {
        "source_id": source_id,
        "message_id": str(resolved_message_id),
        "candles": [],
    }

    return {
        "message_type": "candles",
        "source_id": source_id,
        "message_id": resolved_message_id,
        "sent_at": sent_at or datetime.now(timezone.utc),
        "payload": resolved_payload,
        "raw_body": raw_body,
    }


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(
        self,
        *,
        insert_result=(1,),
        existing_hash=None,
    ):
        self.insert_result = insert_result
        self.existing_hash = existing_hash
        self.executed_statements = []

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        return False

    def execute(self, query, parameters=None):
        self.executed_statements.append(
            (query, parameters)
        )

        normalized_query = query.strip().upper()

        if normalized_query.startswith("INSERT"):
            return FakeCursor(self.insert_result)

        if normalized_query.startswith("SELECT"):
            if self.existing_hash is None:
                return FakeCursor(None)

            return FakeCursor((self.existing_hash,))

        raise AssertionError(
            "Unexpected SQL statement in test"
        )


def create_store(monkeypatch, connection):
    store = MarketDataStore(
        "postgresql://test:test@localhost/test_db"
    )

    monkeypatch.setattr(
        store,
        "_connect",
        lambda: connection,
    )

    return store


@pytest.mark.asyncio
async def test_new_message_is_stored_successfully(monkeypatch):
    connection = FakeConnection(
        insert_result=(1,),
    )
    store = create_store(monkeypatch, connection)
    message = build_message()

    result = await store.store_message(**message)

    assert result is True
    assert len(connection.executed_statements) == 1

    query, parameters = connection.executed_statements[0]

    assert "INSERT INTO gold_bot_market_data_messages" in query
    assert parameters[0] == TEST_SOURCE_ID
    assert parameters[1] == "candles"
    assert parameters[2] == message["message_id"]


@pytest.mark.asyncio
async def test_identical_duplicate_message_is_idempotent(
    monkeypatch,
):
    message = build_message()

    expected_hash = hashlib.sha256(
        message["raw_body"]
    ).hexdigest()

    connection = FakeConnection(
        insert_result=None,
        existing_hash=expected_hash,
    )

    store = create_store(monkeypatch, connection)

    result = await store.store_message(**message)

    assert result is False
    assert len(connection.executed_statements) == 2

    select_query = connection.executed_statements[1][0]

    assert "SELECT body_sha256" in select_query


@pytest.mark.asyncio
async def test_reused_message_id_with_different_content_is_rejected(
    monkeypatch,
):
    message = build_message()

    connection = FakeConnection(
        insert_result=None,
        existing_hash="0" * 64,
    )

    store = create_store(monkeypatch, connection)

    with pytest.raises(
        MarketDataMessageConflictError,
        match="reused with different content",
    ):
        await store.store_message(**message)


@pytest.mark.asyncio
async def test_missing_existing_message_after_conflict_is_rejected(
    monkeypatch,
):
    message = build_message()

    connection = FakeConnection(
        insert_result=None,
        existing_hash=None,
    )

    store = create_store(monkeypatch, connection)

    with pytest.raises(
        MarketDataStoreError,
        match="Unable to verify existing message",
    ):
        await store.store_message(**message)


@pytest.mark.asyncio
async def test_unsupported_message_type_is_rejected(
    monkeypatch,
):
    connection = FakeConnection()
    store = create_store(monkeypatch, connection)

    message = build_message()
    message["message_type"] = "unknown"

    with pytest.raises(
        MarketDataStoreError,
        match="Unsupported market-data message type",
    ):
        await store.store_message(**message)

    assert connection.executed_statements == []


@pytest.mark.asyncio
async def test_payload_source_id_mismatch_is_rejected(
    monkeypatch,
):
    connection = FakeConnection()
    store = create_store(monkeypatch, connection)

    message = build_message(
        payload={
            "source_id": "MT5-OTHER-01",
            "message_id": str(uuid4()),
        }
    )

    with pytest.raises(
        MarketDataStoreError,
        match="Payload source identifier mismatch",
    ):
        await store.store_message(**message)

    assert connection.executed_statements == []


@pytest.mark.asyncio
async def test_payload_message_id_mismatch_is_rejected(
    monkeypatch,
):
    connection = FakeConnection()
    store = create_store(monkeypatch, connection)

    message = build_message(
        payload={
            "source_id": TEST_SOURCE_ID,
            "message_id": str(uuid4()),
        }
    )

    with pytest.raises(
        MarketDataStoreError,
        match="Payload message identifier mismatch",
    ):
        await store.store_message(**message)

    assert connection.executed_statements == []


@pytest.mark.asyncio
async def test_naive_timestamp_is_rejected(monkeypatch):
    connection = FakeConnection()
    store = create_store(monkeypatch, connection)

    message = build_message(
        sent_at=datetime.now(),
    )

    with pytest.raises(
        MarketDataStoreError,
        match="must include a timezone",
    ):
        await store.store_message(**message)

    assert connection.executed_statements == []


@pytest.mark.asyncio
async def test_empty_raw_body_is_rejected(monkeypatch):
    connection = FakeConnection()
    store = create_store(monkeypatch, connection)

    message = build_message(
        raw_body=b"",
    )

    with pytest.raises(
        MarketDataStoreError,
        match="Exact raw request body is required",
    ):
        await store.store_message(**message)

    assert connection.executed_statements == []


def test_non_postgresql_database_url_is_rejected():
    with pytest.raises(
        MarketDataStoreError,
        match="PostgreSQL connection URL is required",
    ):
        MarketDataStore(
            "sqlite:///test.db"
  )

@pytest.mark.asyncio
async def test_invalid_source_identifier_is_rejected(monkeypatch):
    connection = FakeConnection()
    store = create_store(monkeypatch, connection)

    message = build_message(source_id="invalid source!")

    with pytest.raises(
        MarketDataStoreError,
        match="Invalid market-data source identifier",
    ):
        await store.store_message(**message)

    assert connection.executed_statements == []


@pytest.mark.asyncio
async def test_non_uuid_message_id_is_rejected(monkeypatch):
    connection = FakeConnection()
    store = create_store(monkeypatch, connection)

    message = build_message()
    message["message_id"] = "not-a-uuid"

    with pytest.raises(
        MarketDataStoreError,
        match="Message ID must be a UUID",
    ):
        await store.store_message(**message)

    assert connection.executed_statements == []


@pytest.mark.asyncio
async def test_non_datetime_timestamp_is_rejected(monkeypatch):
    connection = FakeConnection()
    store = create_store(monkeypatch, connection)

    message = build_message(sent_at="2026-11-12T10:00:00Z")

    with pytest.raises(
        MarketDataStoreError,
        match="must be a datetime",
    ):
        await store.store_message(**message)

    assert connection.executed_statements == []


@pytest.mark.asyncio
async def test_non_dictionary_payload_is_rejected(monkeypatch):
    connection = FakeConnection()
    store = create_store(monkeypatch, connection)

    message = build_message(payload=["invalid"])

    with pytest.raises(
        MarketDataStoreError,
        match="payload must be a JSON object",
    ):
        await store.store_message(**message)

    assert connection.executed_statements == []


@pytest.mark.asyncio
async def test_non_bytes_raw_body_is_rejected(monkeypatch):
    connection = FakeConnection()
    store = create_store(monkeypatch, connection)

    message = build_message(raw_body='{"test": true}')

    with pytest.raises(
        MarketDataStoreError,
        match="Exact raw request body is required",
    ):
        await store.store_message(**message)

    assert connection.executed_statements == []


@pytest.mark.asyncio
async def test_raw_body_sha256_is_stored(monkeypatch):
    raw_body = b'{"source":"MT5","value":123}'
    message = build_message(raw_body=raw_body)

    connection = FakeConnection(insert_result=(1,))
    store = create_store(monkeypatch, connection)

    result = await store.store_message(**message)

    assert result is True

    query, parameters = connection.executed_statements[0]

    expected_hash = hashlib.sha256(raw_body).hexdigest()

    assert parameters[4] == expected_hash
    assert "body_sha256" in query


@pytest.mark.asyncio
async def test_sent_at_is_normalized_to_utc_before_storage(monkeypatch):
    from datetime import timedelta

    original_time = datetime(
        2026, 11, 12, 15, 30,
        tzinfo=timezone(timedelta(hours=3)),
    )

    message = build_message(sent_at=original_time)

    connection = FakeConnection(insert_result=(1,))
    store = create_store(monkeypatch, connection)

    await store.store_message(**message)

    parameters = connection.executed_statements[0][1]
    stored_time = parameters[3]

    assert stored_time.tzinfo == timezone.utc
    assert stored_time == datetime(
        2026, 11, 12, 12, 30,
        tzinfo=timezone.utc,
    )


class FailingDatabaseConnection:
    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        return False

    def execute(self, query, parameters=None):
        raise RuntimeError("simulated database failure")


@pytest.mark.asyncio
async def test_database_failure_is_wrapped_as_store_error(monkeypatch):
    connection = FailingDatabaseConnection()
    store = create_store(monkeypatch, connection)

    message = build_message()

    with pytest.raises(
        MarketDataStoreError,
        match="Unable to persist market-data message",
    ) as error:
        await store.store_message(**message)

    assert isinstance(error.value.__cause__, RuntimeError)


def test_postgresql_psycopg_url_is_accepted():
    store = MarketDataStore(
        "postgresql+psycopg://test:test@localhost/test_db"
    )

    assert store._database_url == (
        "postgresql://test:test@localhost/test_db"
    )


def test_non_string_database_url_is_rejected():
    with pytest.raises(
        MarketDataStoreError,
        match="Database URL must be a string",
    ):
        MarketDataStore(None)

class FakeSchemaConnection:
    def __init__(
        self,
        *,
        columns=None,
        constraints=None,
        indexes=None,
        fail_on=None,
    ):
        self.columns = columns
        self.constraints = constraints
        self.indexes = indexes
        self.fail_on = fail_on
        self.executed_statements = []

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        return False

    def execute(self, query, parameters=None):
        self.executed_statements.append(
            (query, parameters)
        )

        if self.fail_on and self.fail_on in query:
            raise RuntimeError("simulated schema failure")

        if "information_schema.columns" in query:
            return FakeCursor(
                [(column,) for column in (self.columns or [])]
            )

        if "FROM pg_constraint" in query:
            return FakeCursor(
                self.constraints or []
            )

        if "FROM pg_index" in query:
            return FakeCursor(
                self.indexes or []
            )

        raise AssertionError(
            "Unexpected schema SQL statement"
        )


def valid_schema_connection():
    return FakeSchemaConnection(
        columns=MarketDataStore._REQUIRED_COLUMNS,
        constraints=[
            ("uq_market_data_message", "u"),
            ("ck_market_data_message_type", "c"),
        ],
        indexes=[
            ("idx_market_data_source_received", True, True),
            ("idx_market_data_type_sent", True, True),
        ],
    )


def create_schema_store(monkeypatch, connection):
    store = MarketDataStore(
        "postgresql://test:test@localhost/test_db"
    )

    monkeypatch.setattr(
        store,
        "_connect",
        lambda: connection,
    )

    return store


@pytest.mark.asyncio
async def test_initialize_accepts_complete_schema(monkeypatch):
    connection = valid_schema_connection()
    store = create_schema_store(monkeypatch, connection)

    await store.initialize()

    assert len(connection.executed_statements) == 3


@pytest.mark.asyncio
async def test_initialize_rejects_missing_table(monkeypatch):
    connection = FakeSchemaConnection(
        columns=[],
        constraints=[],
        indexes=[],
    )

    store = create_schema_store(monkeypatch, connection)

    with pytest.raises(
        MarketDataStoreError,
        match="Missing table",
    ):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_missing_columns(monkeypatch):
    columns = (
        MarketDataStore._REQUIRED_COLUMNS
        - {"body_sha256", "payload"}
    )

    connection = valid_schema_connection()
    connection.columns = columns

    store = create_schema_store(monkeypatch, connection)

    with pytest.raises(
        MarketDataStoreError,
        match="Missing market-data columns",
    ):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_missing_unique_constraint(
    monkeypatch,
):
    connection = valid_schema_connection()
    connection.constraints = [
        ("ck_market_data_message_type", "c"),
    ]

    store = create_schema_store(monkeypatch, connection)

    with pytest.raises(
        MarketDataStoreError,
        match="Missing market-data unique constraint",
    ):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_missing_check_constraint(
    monkeypatch,
):
    connection = valid_schema_connection()
    connection.constraints = [
        ("uq_market_data_message", "u"),
    ]

    store = create_schema_store(monkeypatch, connection)

    with pytest.raises(
        MarketDataStoreError,
        match="Missing market-data message type constraint",
    ):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_missing_index(monkeypatch):
    connection = valid_schema_connection()
    connection.indexes = [
        ("idx_market_data_source_received", True, True),
    ]

    store = create_schema_store(monkeypatch, connection)

    with pytest.raises(
        MarketDataStoreError,
        match="Missing market-data index",
    ):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_invalid_index(monkeypatch):
    connection = valid_schema_connection()
    connection.indexes = [
        ("idx_market_data_source_received", False, True),
        ("idx_market_data_type_sent", True, True),
    ]

    store = create_schema_store(monkeypatch, connection)

    with pytest.raises(
        MarketDataStoreError,
        match="Invalid market-data index",
    ):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialize_never_executes_schema_creation(monkeypatch):
    connection = valid_schema_connection()
    store = create_schema_store(monkeypatch, connection)

    await store.initialize()

    executed_sql = "\n".join(
        query
        for query, _ in connection.executed_statements
    ).upper()

    assert "CREATE TABLE" not in executed_sql
    assert "ALTER TABLE" not in executed_sql
    assert "CREATE INDEX" not in executed_sql
    assert "DROP TABLE" not in executed_sql


@pytest.mark.asyncio
async def test_initialize_wraps_database_errors(monkeypatch):
    connection = FakeSchemaConnection(
        fail_on="information_schema.columns",
    )

    store = create_schema_store(monkeypatch, connection)

    with pytest.raises(
        MarketDataStoreError,
        match="Unable to initialize market-data storage",
    ) as error:
        await store.initialize()

    assert isinstance(error.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_initialize_rejects_database_connection_failure(
    monkeypatch,
):
    store = MarketDataStore(
        "postgresql://test:test@localhost/test_db"
    )

    def fail_connection():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        store,
        "_connect",
        fail_connection,
    )

    with pytest.raises(
        MarketDataStoreError,
        match="Unable to initialize market-data storage",
    ) as error:
        await store.initialize()

    assert isinstance(error.value.__cause__, RuntimeError)
