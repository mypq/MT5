
import asyncio
import logging

import pytest

from app.core.nonce_cleanup import run_nonce_cleanup
from app.core.postgres_nonce_store import (
    NonceStoreError,
    PostgresNonceStore,
)


def make_store():
    return PostgresNonceStore(
        "postgresql://user:password@localhost:5432/test_db"
    )


@pytest.mark.asyncio
async def test_rejects_non_postgres_nonce_store():
    with pytest.raises(TypeError):
        await run_nonce_cleanup(object())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interval",
    [
        0,
        1,
        9,
        -1,
        True,
        False,
        10.5,
        "300",
        None,
    ],
)
async def test_rejects_invalid_interval(interval):
    with pytest.raises(ValueError):
        await run_nonce_cleanup(
            make_store(),
            interval_seconds=interval,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "batch_size",
    [
        0,
        -1,
        10001,
        True,
        False,
        1.5,
        "100",
        None,
    ],
)
async def test_rejects_invalid_batch_size(batch_size):
    with pytest.raises(ValueError):
        await run_nonce_cleanup(
            make_store(),
            batch_size=batch_size,
        )


@pytest.mark.asyncio
async def test_runs_cleanup_with_configured_batch_size(
    monkeypatch,
):
    store = make_store()
    cleanup_calls = []
    sleep_calls = []

    async def fake_cleanup(*, batch_size):
        cleanup_calls.append(batch_size)
        return 0

    async def stop_after_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(
        store,
        "cleanup_expired",
        fake_cleanup,
    )
    monkeypatch.setattr(
        "app.core.nonce_cleanup.asyncio.sleep",
        stop_after_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await run_nonce_cleanup(
            store,
            interval_seconds=45,
            batch_size=250,
        )

    assert cleanup_calls == [250]
    assert sleep_calls == [45]


@pytest.mark.asyncio
async def test_cleanup_repeats_after_each_interval(
    monkeypatch,
):
    store = make_store()
    cleanup_count = 0
    sleep_count = 0

    async def fake_cleanup(*, batch_size):
        nonlocal cleanup_count
        cleanup_count += 1
        return 0

    async def fake_sleep(seconds):
        nonlocal sleep_count
        sleep_count += 1

        if sleep_count == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        store,
        "cleanup_expired",
        fake_cleanup,
    )
    monkeypatch.setattr(
        "app.core.nonce_cleanup.asyncio.sleep",
        fake_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await run_nonce_cleanup(store)

    assert cleanup_count == 3
    assert sleep_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        NonceStoreError("database unavailable"),
        RuntimeError("unexpected failure"),
    ],
)
async def test_cleanup_errors_do_not_stop_future_cycles(
    monkeypatch,
    error,
    caplog,
):
    store = make_store()
    cleanup_calls = 0
    sleep_calls = 0

    async def fake_cleanup(*, batch_size):
        nonlocal cleanup_calls
        cleanup_calls += 1
        raise error

    async def fake_sleep(seconds):
        nonlocal sleep_calls
        sleep_calls += 1

        if sleep_calls == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        store,
        "cleanup_expired",
        fake_cleanup,
    )
    monkeypatch.setattr(
        "app.core.nonce_cleanup.asyncio.sleep",
        fake_sleep,
    )

    with caplog.at_level(
        logging.ERROR,
        logger="gold_bot.nonce_cleanup",
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_nonce_cleanup(store)

    assert cleanup_calls == 2
    assert sleep_calls == 2
    assert "Nonce cleanup" in caplog.text


@pytest.mark.asyncio
async def test_cancelled_cleanup_propagates_immediately(
    monkeypatch,
):
    store = make_store()
    sleep_calls = 0

    async def fake_cleanup(*, batch_size):
        raise asyncio.CancelledError

    async def fake_sleep(seconds):
        nonlocal sleep_calls
        sleep_calls += 1

    monkeypatch.setattr(
        store,
        "cleanup_expired",
        fake_cleanup,
    )
    monkeypatch.setattr(
        "app.core.nonce_cleanup.asyncio.sleep",
        fake_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await run_nonce_cleanup(store)

    assert sleep_calls == 0


@pytest.mark.asyncio
async def test_logs_deleted_nonce_count(
    monkeypatch,
    caplog,
):
    store = make_store()
    sleep_calls = 0

    async def fake_cleanup(*, batch_size):
        return 17

    async def fake_sleep(seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(
        store,
        "cleanup_expired",
        fake_cleanup,
    )
    monkeypatch.setattr(
        "app.core.nonce_cleanup.asyncio.sleep",
        fake_sleep,
    )

    with caplog.at_level(
        logging.INFO,
        logger="gold_bot.nonce_cleanup",
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_nonce_cleanup(store)

    assert "Removed 17 expired authentication nonces" in caplog.text
    assert sleep_calls == 1


@pytest.mark.asyncio
async def test_zero_deleted_rows_do_not_log_removal(
    monkeypatch,
    caplog,
):
    store = make_store()

    async def fake_cleanup(*, batch_size):
        return 0

    async def fake_sleep(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        store,
        "cleanup_expired",
        fake_cleanup,
    )
    monkeypatch.setattr(
        "app.core.nonce_cleanup.asyncio.sleep",
        fake_sleep,
    )

    with caplog.at_level(
        logging.INFO,
        logger="gold_bot.nonce_cleanup",
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_nonce_cleanup(store)

    assert "Removed" not in caplog.text


@pytest.mark.asyncio
async def test_accepts_minimum_valid_interval_and_batch(
    monkeypatch,
):
    store = make_store()
    received = []

    async def fake_cleanup(*, batch_size):
        received.append(batch_size)
        return 0

    async def fake_sleep(seconds):
        assert seconds == 10
        raise asyncio.CancelledError

    monkeypatch.setattr(
        store,
        "cleanup_expired",
        fake_cleanup,
    )
    monkeypatch.setattr(
        "app.core.nonce_cleanup.asyncio.sleep",
        fake_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await run_nonce_cleanup(
            store,
            interval_seconds=10,
            batch_size=1,
        )

    assert received == [1]


@pytest.mark.asyncio
async def test_accepts_maximum_valid_batch_size(
    monkeypatch,
):
    store = make_store()
    received = []

    async def fake_cleanup(*, batch_size):
        received.append(batch_size)
        return 0

    async def fake_sleep(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        store,
        "cleanup_expired",
        fake_cleanup,
    )
    monkeypatch.setattr(
        "app.core.nonce_cleanup.asyncio.sleep",
        fake_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await run_nonce_cleanup(
            store,
            batch_size=10000,
        )

    assert received == [10000]
