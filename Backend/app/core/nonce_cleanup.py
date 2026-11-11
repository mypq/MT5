import asyncio
import logging

from app.core.postgres_nonce_store import (
    NonceStoreError,
    PostgresNonceStore,
)


logger = logging.getLogger("gold_bot.nonce_cleanup")


async def run_nonce_cleanup(
    nonce_store: PostgresNonceStore,
    *,
    interval_seconds: int = 300,
    batch_size: int = 1000,
) -> None:
    """
    Periodically remove expired authentication nonces.

    The coroutine is intended to run as a managed asyncio task.
    Cancel the task during application shutdown.
    """

    if not isinstance(nonce_store, PostgresNonceStore):
        raise TypeError(
            "nonce_store must be a PostgresNonceStore"
        )

    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, int)
        or interval_seconds < 10
    ):
        raise ValueError(
            "interval_seconds must be an integer of at least 10"
        )

    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or not 1 <= batch_size <= 10000
    ):
        raise ValueError(
            "batch_size must be between 1 and 10000"
        )

    logger.info(
        "Nonce cleanup task started; interval=%s seconds",
        interval_seconds,
    )

    while True:
        try:
            deleted_count = await nonce_store.cleanup_expired(
                batch_size=batch_size
            )

            if deleted_count:
                logger.info(
                    "Removed %s expired authentication nonces",
                    deleted_count,
                )

        except NonceStoreError:
            logger.exception(
                "Nonce cleanup failed; will retry later"
            )

        except Exception:
            logger.exception(
                "Unexpected nonce cleanup error; will retry later"
            )

        await asyncio.sleep(interval_seconds)
