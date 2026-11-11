from enum import Enum
from typing import Any

from app.core.config import settings


class TradingMode(str, Enum):
    DEMO = "DEMO"
    REAL = "REAL"
    UNKNOWN = "UNKNOWN"


class TradingPermissionError(Exception):
    """Raised when a trading operation violates safety policy."""


def normalize_trading_mode(value: Any) -> TradingMode:
    if not isinstance(value, str):
        return TradingMode.UNKNOWN

    normalized = value.strip().upper()

    if normalized == "DEMO":
        return TradingMode.DEMO

    if normalized == "REAL":
        return TradingMode.REAL

    return TradingMode.UNKNOWN


def verify_demo_account(account_mode: Any) -> None:
    """
    Preliminary mode check.

    This does not replace verification against the connected MT5
    terminal and broker account information.
    """
    if settings.DEMO_ONLY is not True:
        raise TradingPermissionError(
            "Invalid configuration: DEMO_ONLY must remain enabled."
        )

    mode = normalize_trading_mode(account_mode)

    if mode is not TradingMode.DEMO:
        raise TradingPermissionError(
            "Trading blocked: a confirmed DEMO account is required."
        )


def verify_automated_trading_enabled() -> None:
    """
    Automated trading remains disabled until the complete
    MT5 integration and independent safety checks are implemented.
    """
    if settings.DEMO_ONLY is not True:
        raise TradingPermissionError(
            "Trading blocked: DEMO_ONLY is not enabled."
        )

    if not settings.AUTOMATED_TRADING_ENABLED:
        raise TradingPermissionError(
            "Automated trading is currently disabled."
        )


def authorize_trade(
    account_mode: Any,
    automated: bool,
) -> dict[str, str]:
    """
    Preliminary application-level authorization only.

    Never use this result alone to execute an MT5 order.
    """
    verify_demo_account(account_mode)

    if automated:
        verify_automated_trading_enabled()

    return {
        "account_mode": TradingMode.DEMO.value,
        "permission": "PRELIMINARY_APPROVED",
        "execution_mode": "AUTOMATED" if automated else "MANUAL",
        "mt5_verified": "false",
    }


def reject_real_account_payload(payload: dict) -> None:
    """
    Reject incomplete or non-DEMO payloads.

    Payload values are untrusted and cannot prove the real MT5
    account mode. Independent MT5 verification is mandatory.
    """
    if not isinstance(payload, dict):
        raise TradingPermissionError(
            "Rejected: invalid account payload."
        )

    account_mode = payload.get("account_mode")

    if normalize_trading_mode(account_mode) is not TradingMode.DEMO:
        raise TradingPermissionError(
            "Rejected: account mode is not confirmed as DEMO."
        )

    raise TradingPermissionError(
        "Rejected: payload confirmation is insufficient. "
        "Independent MT5 account verification is required."
    )
