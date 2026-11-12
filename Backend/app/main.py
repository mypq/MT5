
import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.market_data import router as market_data_router
from app.core.config import settings
from app.core.device_registry import DeviceRegistry
from app.core.device_secret_crypto import DeviceSecretCipher
from app.core.market_data_store import MarketDataStore
from app.core.market_time_store import MarketTimeStore
from app.core.nonce_cleanup import run_nonce_cleanup
from app.core.postgres_nonce_store import PostgresNonceStore
from app.core.windows_time_sync import inspect_windows_time_sync


logger = logging.getLogger("gold_bot.backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task: asyncio.Task | None = None

    app.state.shutting_down = False
    app.state.database_status = "NOT_CONNECTED"
    app.state.device_registry = None
    app.state.nonce_store = None
    app.state.market_data_store = None
    app.state.market_time_store = None
    app.state.nonce_cleanup_task = None

    logger.info("GOLD BOT backend starting")

    try:
        if settings.MARKET_DATA_INGESTION_ENABLED:
            database_url = (
                settings.DATABASE_URL.get_secret_value()
            )

            encryption_key = (
                settings.DEVICE_SECRET_ENCRYPTION_KEY
                .get_secret_value()
            )

            cipher = DeviceSecretCipher(encryption_key)

            nonce_store = PostgresNonceStore(database_url)

            device_registry = DeviceRegistry(
                database_url=database_url,
                cipher=cipher,
            )

            market_data_store = MarketDataStore(database_url)

            market_time_store = MarketTimeStore(database_url)

            await nonce_store.initialize()
            await device_registry.initialize()
            await market_data_store.initialize()
            await market_time_store.initialize()

            app.state.nonce_store = nonce_store
            app.state.device_registry = device_registry
            app.state.market_data_store = market_data_store
            app.state.market_time_store = market_time_store
            app.state.database_status = "CONNECTED"

            cleanup_task = asyncio.create_task(
                run_nonce_cleanup(nonce_store),
                name="gold-bot-nonce-cleanup",
            )

            app.state.nonce_cleanup_task = cleanup_task

            logger.info(
                "Market-data security, storage, and broker-time "
                "storage initialized"
            )

        else:
            app.state.database_status = "DISABLED"

        yield

    except Exception:
        app.state.database_status = "ERROR"

        logger.exception(
            "GOLD BOT backend startup or runtime failure"
        )

        raise

    finally:
        app.state.shutting_down = True

        if cleanup_task is not None:
            cleanup_task.cancel()

            try:
                await cleanup_task
            except asyncio.CancelledError:
                logger.info(
                    "Nonce cleanup task stopped"
                )

            app.state.nonce_cleanup_task = None

        logger.info("GOLD BOT backend shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "GOLD BOT secure analysis and DEMO trading backend."
    ),
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)


app.include_router(market_data_router)


@app.get("/", tags=["System"])
async def root():
    return {
        "application": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "trading_mode": "DEMO_ONLY",
    }


@app.get("/health", tags=["System"])
async def health():
    database_status = getattr(
        app.state,
        "database_status",
        "NOT_INITIALIZED",
    )

    ingestion_enabled = (
        settings.MARKET_DATA_INGESTION_ENABLED
    )

    healthy = (
        database_status == "CONNECTED"
        if ingestion_enabled
        else database_status == "DISABLED"
    )

    return {
        "status": "healthy" if healthy else "unhealthy",
        "service": "gold-bot-backend",
        "database_status": database_status,
        "market_data_ingestion_enabled": ingestion_enabled,
        "shutting_down": getattr(
            app.state,
            "shutting_down",
            False,
        ),
    }


@app.get("/api/v1/system/status", tags=["System"])
async def system_status():
    database_status = getattr(
        app.state,
        "database_status",
        "NOT_INITIALIZED",
    )

    ingestion_enabled = (
        settings.MARKET_DATA_INGESTION_ENABLED
    )

    backend_healthy = (
        database_status == "CONNECTED"
        if ingestion_enabled
        else database_status == "DISABLED"
    )

    cleanup_task = getattr(
        app.state,
        "nonce_cleanup_task",
        None,
    )

    cleanup_running = (
        cleanup_task is not None
        and not cleanup_task.done()
    )

    backend_clock_sync = await asyncio.to_thread(
        inspect_windows_time_sync
    )

    return {
        "application": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "trading_mode": "DEMO_ONLY",
        "automated_trading_enabled": (
            settings.AUTOMATED_TRADING_ENABLED
        ),
        "market_data_ingestion_enabled": ingestion_enabled,
        "analysis_mode_available": True,
        "backend_status": (
            "running" if backend_healthy else "unhealthy"
        ),
        "database_status": database_status,
        "backend_clock_sync": backend_clock_sync,
        "device_registry_status": (
            "initialized"
            if app.state.device_registry is not None
            else "disabled"
        ),
        "nonce_store_status": (
            "initialized"
            if app.state.nonce_store is not None
            else "disabled"
        ),
        "market_data_store_status": (
            "initialized"
            if app.state.market_data_store is not None
            else "disabled"
        ),
        "market_time_store_status": (
            "initialized"
            if app.state.market_time_store is not None
            else "disabled"
        ),
        "nonce_cleanup_status": (
            "running" if cleanup_running else "stopped"
        ),
        "mt5_connection_status": "NOT_CONNECTED",
    }


@app.exception_handler(Exception)
async def unexpected_exception_handler(
    request: Request,
    exc: Exception,
):
    logger.exception(
        "Unhandled exception on %s %s",
        request.method,
        request.url.path,
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected server error occurred.",
        },
    )
