from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.device_registry import DeviceRegistry
from app.core.device_secret_crypto import DeviceSecretCipher
from app.core.postgres_nonce_store import PostgresNonceStore


logger = logging.getLogger("gold_bot.backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.shutting_down = False
    app.state.database_status = "NOT_CONNECTED"
    app.state.device_registry = None
    app.state.nonce_store = None

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

            # Fail startup if either security store cannot initialize.
            await nonce_store.initialize()
            await device_registry.initialize()

            app.state.nonce_store = nonce_store
            app.state.device_registry = device_registry
            app.state.database_status = "CONNECTED"

            logger.info(
                "Market-data security stores initialized"
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

    return {
        "status": (
            "healthy"
            if database_status != "ERROR"
            else "unhealthy"
        ),
        "service": "gold-bot-backend",
        "database_status": database_status,
        "market_data_ingestion_enabled": (
            settings.MARKET_DATA_INGESTION_ENABLED
        ),
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

    return {
        "application": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "trading_mode": "DEMO_ONLY",
        "automated_trading_enabled": (
            settings.AUTOMATED_TRADING_ENABLED
        ),
        "market_data_ingestion_enabled": (
            settings.MARKET_DATA_INGESTION_ENABLED
        ),
        "analysis_mode_available": True,
        "backend_status": (
            "running"
            if database_status != "ERROR"
            else "unhealthy"
        ),
        "database_status": database_status,
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
        "mt5_connection_status": "NOT_CONNECTED",
    }


@app.exception_handler(Exception)
async def unexpected_exception_handler(
    request: Request,
    exc: Exception,
):
    logger.exception(
        "Unhandled backend exception on %s %s",
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
