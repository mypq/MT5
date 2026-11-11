from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings


logger = logging.getLogger("gold_bot.backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.shutting_down = False

    logger.info("GOLD BOT backend starting")

    try:
        yield
    finally:
        app.state.shutting_down = True
        logger.info("GOLD BOT backend shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="GOLD BOT secure analysis and DEMO trading backend.",
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
    return {
        "status": "healthy",
        "service": "gold-bot-backend",
        "shutting_down": app.state.shutting_down,
    }


@app.get("/api/v1/system/status", tags=["System"])
async def system_status():
    return {
        "application": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "trading_mode": "DEMO_ONLY",
        "automated_trading_enabled": (
            settings.AUTOMATED_TRADING_ENABLED
        ),
        "analysis_mode_available": True,
        "backend_status": "running",
        "database_status": "NOT_CHECKED",
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
