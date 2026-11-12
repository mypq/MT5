
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
    assert settings.APP_ENV == "development"
    assert settings.API_PORT == 8000
    assert settings.JWT_ALGORITHM == "HS256"


@pytest.mark.parametrize(
    "environment",
    ["development", "production", "testing"],
)
def test_valid_app_environments_are_accepted(environment):
    settings = make_settings(APP_ENV=environment)
    assert settings.APP_ENV == environment


@pytest.mark.parametrize(
    "environment",
    ["staging", "test", "DEVELOP", "", "invalid"],
)
def test_invalid_app_environments_are_rejected(environment):
    with pytest.raises(ValidationError):
        make_settings(APP_ENV=environment)


@pytest.mark.parametrize("port", [1, 8000, 65535])
def test_valid_api_ports_are_accepted(port):
    settings = make_settings(API_PORT=port)
    assert settings.API_PORT == port


@pytest.mark.parametrize(
    "port",
    [0, -1, 65536, 70000, 1.5],
)
def test_invalid_api_ports_are_rejected(port):
    with pytest.raises(ValidationError):
        make_settings(API_PORT=port)


@pytest.mark.parametrize(
    "minutes",
    [5, 30, 1440],
)
def test_valid_access_token_expiry_is_accepted(minutes):
    settings = make_settings(
        ACCESS_TOKEN_EXPIRE_MINUTES=minutes
    )
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == minutes


@pytest.mark.parametrize(
    "minutes",
    [0, 4, 1441, -10],
)
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


@pytest.mark.parametrize(
    "field",
    ["JWT_SECRET", "BACKEND_API_KEY"],
)
def test_32_character_security_secrets_are_accepted(field):
    settings = make_settings(**{field: "x" * 32})

    assert (
        getattr(settings, field).get_secret_value()
        == "x" * 32
    )


def test_valid_security_secrets_are_accepted():
    settings = make_settings(
        JWT_SECRET="j" * 32,
        BACKEND_API_KEY="b" * 32,
    )

    assert settings.JWT_SECRET.get_secret_value() == "j" * 32
    assert settings.BACKEND_API_KEY.get_secret_value() == "b" * 32


def test_default_secret_placeholders_are_allowed_in_development():
    settings = make_settings()

    assert (
        settings.JWT_SECRET.get_secret_value()
        == "CHANGE_ME_BEFORE_DEPLOYMENT"
    )
    assert (
        settings.BACKEND_API_KEY.get_secret_value()
        == "CHANGE_ME_BEFORE_DEPLOYMENT"
    )


def test_valid_device_encryption_key_is_accepted():
    settings = make_settings(
        DEVICE_SECRET_ENCRYPTION_KEY="ab" * 32
    )

    assert (
        settings.DEVICE_SECRET_ENCRYPTION_KEY.get_secret_value()
        == "ab" * 32
    )


def test_uppercase_device_encryption_key_is_accepted():
    settings = make_settings(
        DEVICE_SECRET_ENCRYPTION_KEY="AB" * 32
    )

    assert (
        settings.DEVICE_SECRET_ENCRYPTION_KEY.get_secret_value()
        == "AB" * 32
    )


@pytest.mark.parametrize(
    "key",
    [
        "a" * 63,
        "a" * 65,
        "g" * 64,
        "not-a-key",
        " " * 64,
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


def test_ingestion_accepts_configured_encryption_key():
    settings = make_settings(
        MARKET_DATA_INGESTION_ENABLED=True,
        DEVICE_SECRET_ENCRYPTION_KEY="ab" * 32,
    )

    assert settings.MARKET_DATA_INGESTION_ENABLED is True


def test_blank_database_url_is_rejected():
    with pytest.raises(
        ValidationError,
        match="DATABASE_URL cannot be empty",
    ):
        make_settings(DATABASE_URL="   ")


def test_valid_database_url_is_accepted():
    settings = make_settings(
        DATABASE_URL="postgresql+psycopg://user:pass@localhost/db"
    )

    assert (
        settings.DATABASE_URL.get_secret_value()
        == "postgresql+psycopg://user:pass@localhost/db"
    )


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


def test_production_ingestion_requires_configured_encryption_key():
    with pytest.raises(
        ValidationError,
        match="encryption key is required",
    ):
        make_settings(
            APP_ENV="production",
            JWT_SECRET="j" * 32,
            BACKEND_API_KEY="b" * 32,
            DATABASE_URL="postgresql+psycopg://user:pass@localhost/db",
            MARKET_DATA_INGESTION_ENABLED=True,
        )


def test_production_accepts_ingestion_with_all_security_values():
    settings = make_settings(
        APP_ENV="production",
        JWT_SECRET="j" * 32,
        BACKEND_API_KEY="b" * 32,
        DATABASE_URL="postgresql+psycopg://user:pass@localhost/db",
        DEVICE_SECRET_ENCRYPTION_KEY="ab" * 32,
        MARKET_DATA_INGESTION_ENABLED=True,
    )

    assert settings.MARKET_DATA_INGESTION_ENABLED is True


@pytest.mark.parametrize(
    "value",
    [0.0001, 0.1, 0.5],
)
def test_valid_risk_limits_are_accepted(value):
    settings = make_settings(
        MAX_RISK_PER_TRADE_PERCENT=value
    )
    assert settings.MAX_RISK_PER_TRADE_PERCENT == value


@pytest.mark.parametrize(
    "value",
    [0, -0.1, 0.5001, 1.0],
)
def test_invalid_risk_limits_are_rejected(value):
    with pytest.raises(ValidationError):
        make_settings(
            MAX_RISK_PER_TRADE_PERCENT=value
        )


def test_risk_limit_cannot_exceed_half_percent():
    with pytest.raises(ValidationError):
        make_settings(MAX_RISK_PER_TRADE_PERCENT=0.51)


@pytest.mark.parametrize(
    "value",
    [0.0001, 0.5, 1.0],
)
def test_valid_daily_loss_limits_are_accepted(value):
    settings = make_settings(
        MAX_DAILY_LOSS_PERCENT=value
    )
    assert settings.MAX_DAILY_LOSS_PERCENT == value


@pytest.mark.parametrize(
    "value",
    [0, -0.1, 1.01, 2.0],
)
def test_invalid_daily_loss_limits_are_rejected(value):
    with pytest.raises(ValidationError):
        make_settings(
            MAX_DAILY_LOSS_PERCENT=value
        )


def test_daily_loss_limit_cannot_exceed_one_percent():
    with pytest.raises(ValidationError):
        make_settings(MAX_DAILY_LOSS_PERCENT=1.01)


def test_only_one_simultaneous_position_is_allowed():
    settings = make_settings(
        MAX_SIMULTANEOUS_POSITIONS=1
    )
    assert settings.MAX_SIMULTANEOUS_POSITIONS == 1


@pytest.mark.parametrize(
    "value",
    [0, 2, 3, -1],
)
def test_invalid_simultaneous_position_limits_are_rejected(value):
    with pytest.raises(ValidationError):
        make_settings(
            MAX_SIMULTANEOUS_POSITIONS=value
        )


@pytest.mark.parametrize(
    "value",
    [1, 10, 60],
)
def test_valid_market_data_max_age_is_accepted(value):
    settings = make_settings(
        MARKET_DATA_MAX_AGE_SECONDS=value
    )
    assert settings.MARKET_DATA_MAX_AGE_SECONDS == value


@pytest.mark.parametrize(
    "value",
    [0, -1, 61, 120],
)
def test_invalid_market_data_max_age_is_rejected(value):
    with pytest.raises(ValidationError):
        make_settings(
            MARKET_DATA_MAX_AGE_SECONDS=value
        )


@pytest.mark.parametrize(
    "value",
    [5, 30, 120],
)
def test_valid_command_expiry_is_accepted(value):
    settings = make_settings(
        COMMAND_EXPIRY_SECONDS=value
    )
    assert settings.COMMAND_EXPIRY_SECONDS == value


@pytest.mark.parametrize(
    "value",
    [0, 4, 121, 300],
)
def test_invalid_command_expiry_is_rejected(value):
    with pytest.raises(ValidationError):
        make_settings(
            COMMAND_EXPIRY_SECONDS=value
        )


def test_jwt_algorithm_is_restricted_to_hs256():
    settings = make_settings(JWT_ALGORITHM="HS256")
    assert settings.JWT_ALGORITHM == "HS256"


@pytest.mark.parametrize(
    "algorithm",
    ["HS384", "HS512", "RS256", "none", ""],
)
def test_unsupported_jwt_algorithms_are_rejected(algorithm):
    with pytest.raises(ValidationError):
        make_settings(JWT_ALGORITHM=algorithm)
