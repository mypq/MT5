
import pytest
from pydantic import ValidationError

from app.core.config import Settings


def make_settings(**overrides):
    return Settings(_env_file=None, **overrides)


def test_default_settings_are_demo_only():
    settings = make_settings()

    assert settings.DEMO_ONLY is True
    assert settings.AUTOMATED_TRADING_ENABLED is False
    assert settings.MARKET_DATA_INGESTION_ENABLED is False


@pytest.mark.parametrize("port", [1, 8000, 65535])
def test_valid_api_ports_are_accepted(port):
    settings = make_settings(API_PORT=port)
    assert settings.API_PORT == port


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_invalid_api_ports_are_rejected(port):
    with pytest.raises(ValidationError):
        make_settings(API_PORT=port)


@pytest.mark.parametrize("minutes", [5, 30, 1440])
def test_valid_access_token_expiry_is_accepted(minutes):
    settings = make_settings(
        ACCESS_TOKEN_EXPIRE_MINUTES=minutes
    )
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == minutes


@pytest.mark.parametrize("minutes", [0, 4, 1441])
def test_invalid_access_token_expiry_is_rejected(minutes):
    with pytest.raises(ValidationError):
        make_settings(ACCESS_TOKEN_EXPIRE_MINUTES=minutes)


def test_non_demo_mode_is_rejected():
    with pytest.raises(
        ValidationError,
        match="restricted to DEMO",
    ):
        make_settings(DEMO_ONLY=False)


@pytest.mark.parametrize(
    "field",
    ["JWT_SECRET", "BACKEND_API_KEY"],
)
def test_short_security_secrets_are_rejected(field):
    with pytest.raises(
        ValidationError,
        match="at least 32 characters",
    ):
        make_settings(**{field: "x" * 31})


def test_valid_security_secrets_are_accepted():
    settings = make_settings(
        JWT_SECRET="j" * 32,
        BACKEND_API_KEY="b" * 32,
    )

    assert settings.JWT_SECRET.get_secret_value() == "j" * 32
    assert settings.BACKEND_API_KEY.get_secret_value() == "b" * 32


def test_valid_device_encryption_key_is_accepted():
    settings = make_settings(
        DEVICE_SECRET_ENCRYPTION_KEY="ab" * 32
    )

    assert (
        settings.DEVICE_SECRET_ENCRYPTION_KEY.get_secret_value()
        == "ab" * 32
    )


@pytest.mark.parametrize(
    "key",
    [
        "a" * 63,
        "a" * 65,
        "g" * 64,
        "not-a-key",
    ],
)
def test_invalid_device_encryption_keys_are_rejected(key):
    with pytest.raises(
        ValidationError,
        match="64 hexadecimal characters",
    ):
        make_settings(
            DEVICE_SECRET_ENCRYPTION_KEY=key
        )


def test_ingestion_requires_device_encryption_key():
    with pytest.raises(
        ValidationError,
        match="encryption key is required",
    ):
        make_settings(
            MARKET_DATA_INGESTION_ENABLED=True
        )


def test_blank_database_url_is_rejected():
    with pytest.raises(
        ValidationError,
        match="DATABASE_URL cannot be empty",
    ):
        make_settings(DATABASE_URL="   ")


def test_production_rejects_default_jwt_secret():
    with pytest.raises(
        ValidationError,
        match="JWT_SECRET must be configured",
    ):
        make_settings(
            APP_ENV="production",
            BACKEND_API_KEY="b" * 32,
            DATABASE_URL="postgresql+psycopg://user:pass@localhost/db",
        )


def test_production_rejects_default_backend_api_key():
    with pytest.raises(
        ValidationError,
        match="BACKEND_API_KEY must be configured",
    ):
        make_settings(
            APP_ENV="production",
            JWT_SECRET="j" * 32,
            DATABASE_URL="postgresql+psycopg://user:pass@localhost/db",
        )


def test_production_rejects_placeholder_database_url():
    with pytest.raises(
        ValidationError,
        match="DATABASE_URL must be configured",
    ):
        make_settings(
            APP_ENV="production",
            JWT_SECRET="j" * 32,
            BACKEND_API_KEY="b" * 32,
            DATABASE_URL="postgresql+psycopg://user:CHANGE_ME@localhost/db",
        )


def test_production_accepts_configured_security_values():
    settings = make_settings(
        APP_ENV="production",
        JWT_SECRET="j" * 32,
        BACKEND_API_KEY="b" * 32,
        DATABASE_URL="postgresql+psycopg://user:pass@localhost/db",
    )

    assert settings.APP_ENV == "production"


def test_risk_limit_cannot_exceed_half_percent():
    with pytest.raises(ValidationError):
        make_settings(MAX_RISK_PER_TRADE_PERCENT=0.51)


def test_daily_loss_limit_cannot_exceed_one_percent():
    with pytest.raises(ValidationError):
        make_settings(MAX_DAILY_LOSS_PERCENT=1.01)


def test_simultaneous_positions_cannot_exceed_one():
    with pytest.raises(ValidationError):
        make_settings(MAX_SIMULTANEOUS_POSITIONS=2)
