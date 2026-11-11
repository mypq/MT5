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
