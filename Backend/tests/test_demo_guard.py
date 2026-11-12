
import pytest

from app.core import demo_guard
from app.core.demo_guard import (
    TradingMode,
    TradingPermissionError,
    normalize_trading_mode,
    verify_demo_account,
    verify_automated_trading_enabled,
    authorize_trade,
    reject_real_account_payload,
)


def test_demo_mode_is_normalized():
    assert normalize_trading_mode("DEMO") is TradingMode.DEMO
    assert normalize_trading_mode(" demo ") is TradingMode.DEMO


def test_real_mode_is_normalized():
    assert normalize_trading_mode(" real ") is TradingMode.REAL


@pytest.mark.parametrize(
    "value",
    [None, 123, "", "UNKNOWN", "LIVE", "PAPER"],
)
def test_unknown_account_modes_are_rejected(value):
    assert normalize_trading_mode(value) is TradingMode.UNKNOWN


def test_confirmed_demo_account_is_accepted():
    verify_demo_account("DEMO")


@pytest.mark.parametrize(
    "value",
    ["REAL", "UNKNOWN", None, "", "LIVE"],
)
def test_non_demo_accounts_are_rejected(value):
    with pytest.raises(
        TradingPermissionError,
        match="confirmed DEMO",
    ):
        verify_demo_account(value)


def test_demo_only_configuration_is_mandatory(monkeypatch):
    monkeypatch.setattr(
        demo_guard.settings,
        "DEMO_ONLY",
        False,
    )

    with pytest.raises(
        TradingPermissionError,
        match="DEMO_ONLY must remain enabled",
    ):
        verify_demo_account("DEMO")


def test_automated_trading_is_rejected_when_disabled(monkeypatch):
    monkeypatch.setattr(
        demo_guard.settings,
        "DEMO_ONLY",
        True,
    )
    monkeypatch.setattr(
        demo_guard.settings,
        "AUTOMATED_TRADING_ENABLED",
        False,
    )

    with pytest.raises(
        TradingPermissionError,
        match="currently disabled",
    ):
        verify_automated_trading_enabled()


def test_automated_trading_rejects_non_demo_configuration(monkeypatch):
    monkeypatch.setattr(
        demo_guard.settings,
        "DEMO_ONLY",
        False,
    )
    monkeypatch.setattr(
        demo_guard.settings,
        "AUTOMATED_TRADING_ENABLED",
        True,
    )

    with pytest.raises(
        TradingPermissionError,
        match="DEMO_ONLY is not enabled",
    ):
        verify_automated_trading_enabled()


def test_manual_demo_authorization_is_preliminary_only():
    result = authorize_trade(
        account_mode="DEMO",
        automated=False,
    )

    assert result == {
        "account_mode": "DEMO",
        "permission": "PRELIMINARY_APPROVED",
        "execution_mode": "MANUAL",
        "mt5_verified": "false",
    }


def test_automated_authorization_is_rejected_by_default():
    with pytest.raises(TradingPermissionError):
        authorize_trade(
            account_mode="DEMO",
            automated=True,
        )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "DEMO",
        {},
        {"account_mode": "REAL"},
        {"account_mode": "UNKNOWN"},
        {"account_mode": None},
    ],
)
def test_invalid_or_non_demo_payloads_are_rejected(payload):
    with pytest.raises(TradingPermissionError):
        reject_real_account_payload(payload)


def test_demo_payload_alone_is_not_sufficient():
    with pytest.raises(
        TradingPermissionError,
        match="Independent MT5 account verification",
    ):
        reject_real_account_payload(
            {"account_mode": "DEMO"}
        )


def test_authorization_never_claims_mt5_verification():
    result = authorize_trade(
        account_mode="DEMO",
        automated=False,
    )

    assert result["mt5_verified"] == "false"
