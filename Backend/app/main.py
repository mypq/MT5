from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.core.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
app.state.shutting_down = False
yield
app.state.shutting_down = True

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
}

@app.get("/api/v1/system/status", tags=["System"])
async def system_status():
return {
"application": settings.APP_NAME,
"version": settings.APP_VERSION,
"environment": settings.APP_ENV,
"trading_mode": "DEMO_ONLY",
"automated_trading_enabled": settings.AUTOMATED_TRADING_ENABLED,
"analysis_mode_available": True,
"backend_status": "running",
}

@app.exception_handler(Exception)
async def unexpected_exception_handler(request, exc):
return JSONResponse(
status_code=500,
content={
"error": "INTERNAL_SERVER_ERROR",
"message": "An unexpected server error occurred.",
},
)
