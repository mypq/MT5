from typing import Type
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

from app.core.device_registry import DeviceRegistry
from app.core.market_data_auth import (
    MarketDataAuthenticationError,
    authenticate_market_data_request,
)
from app.core.market_data_store import (
    MarketDataMessageConflictError,
    MarketDataStore,
    MarketDataStoreError,
)
from app.core.postgres_nonce_store import PostgresNonceStore
from app.schemas.market_data import (
    AccountSnapshot,
    MarketDataBatch,
    OpenPositionsSnapshot,
)


router = APIRouter(
    prefix="/api/v1/market-data",
    tags=["Market Data"],
)


async def _ingest_message(
    *,
    request: Request,
    message_type: str,
    model_type: Type[BaseModel],
) -> dict:
    device_registry = getattr(
        request.app.state,
        "device_registry",
        None,
    )

    nonce_store = getattr(
        request.app.state,
        "nonce_store",
        None,
    )

    market_data_store = getattr(
        request.app.state,
        "market_data_store",
        None,
    )

    if not all(
        (
            isinstance(device_registry, DeviceRegistry),
            isinstance(nonce_store, PostgresNonceStore),
            isinstance(market_data_store, MarketDataStore),
        )
    ):
        raise HTTPException(
            status_code=503,
            detail="Market-data ingestion is unavailable",
        )

    try:
        raw_body = await authenticate_market_data_request(
            request=request,
            device_registry=device_registry,
            nonce_store=nonce_store,
        )
    except MarketDataAuthenticationError as exc:
        raise HTTPException(
            status_code=401,
            detail="Market-data authentication failed",
        ) from exc

    try:
        message = model_type.model_validate_json(raw_body)
    except ValidationError as exc:
        safe_errors = [
            {
                "location": error["loc"],
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]

        raise HTTPException(
            status_code=422,
            detail={
                "error": "INVALID_MARKET_DATA",
                "validation_errors": safe_errors,
            },
        ) from exc

    source_id = message.source_id
    message_id: UUID = message.message_id
    sent_at = message.sent_at
    payload = message.model_dump(mode="json")

    try:
        inserted = await market_data_store.store_message(
            message_type=message_type,
            source_id=source_id,
            message_id=message_id,
            sent_at=sent_at,
            payload=payload,
            raw_body=raw_body,
        )
    except MarketDataMessageConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Message ID conflicts with previously stored data",
        ) from exc
    except MarketDataStoreError as exc:
        raise HTTPException(
            status_code=503,
            detail="Market-data storage is unavailable",
        ) from exc

    return {
        "status": "stored" if inserted else "duplicate",
        "message_id": str(message_id),
        "message_type": message_type,
        "source_id": source_id,
    }


@router.post("/candles")
async def ingest_candles(request: Request):
    return await _ingest_message(
        request=request,
        message_type="candles",
        model_type=MarketDataBatch,
    )


@router.post("/account")
async def ingest_account(request: Request):
    return await _ingest_message(
        request=request,
        message_type="account",
        model_type=AccountSnapshot,
    )


@router.post("/positions")
async def ingest_positions(request: Request):
    return await _ingest_message(
        request=request,
        message_type="positions",
        model_type=OpenPositionsSnapshot,
    )
