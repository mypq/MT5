import re
from functools import lru_cache
from typing import Literal

from pydantic import (
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    APP_NAME: str = "GOLD BOT"
    APP_VERSION: str = "0.1.0"
    APP_ENV: Literal[
        "development",
        "production",
        "testing",
    ] = "development"

    API_HOST: str = "127.0.0.1"
    API_PORT: int = Field(default=8000, ge=1, le=65535)

    DATABASE_URL: SecretStr = SecretStr(
        "postgresql+psycopg://goldbot:CHANGE_ME@localhost:5432/goldbot"
    )

    JWT_SECRET: SecretStr = SecretStr(
        "CHANGE_ME_BEFORE_DEPLOYMENT"
    )
    JWT_ALGORITHM: Literal["HS256"] = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=30,
        ge=5,
        le=1440,
    )

    DEMO_ONLY: bool = True
    AUTOMATED_TRADING_ENABLED: bool = False

    # Market-data ingestion remains disabled until the
    # device registry and authenticated API are integrated.
    MARKET_DATA_INGESTION_ENABLED: bool = False

    # Exactly 32 bytes represented as 64 hexadecimal characters.
    # This key is used for AES-256-GCM device-secret encryption.
    DEVICE_SECRET_ENCRYPTION_KEY: SecretStr = SecretStr(
        "CHANGE_ME_BEFORE_DEPLOYMENT"
    )

    MAX_RISK_PER_TRADE_PERCENT: float = Field(
        default=0.5,
        gt=0,
        le=0.5,
    )
    MAX_DAILY_LOSS_PERCENT: float = Field(
        default=1.0,
        gt=0,
        le=1.0,
    )
    MAX_SIMULTANEOUS_POSITIONS: int = Field(
        default=1,
        ge=1,
        le=1,
    )

    MARKET_DATA_MAX_AGE_SECONDS: int = Field(
        default=10,
        ge=1,
        le=60,
    )
    COMMAND_EXPIRY_SECONDS: int = Field(
        default=30,
        ge=5,
        le=120,
    )

    BACKEND_API_KEY: SecretStr = SecretStr(
        "CHANGE_ME_BEFORE_DEPLOYMENT"
    )

    @field_validator("JWT_SECRET", "BACKEND_API_KEY")
    @classmethod
    def validate_secrets(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()

        if secret in {
            "CHANGE_ME_BEFORE_DEPLOYMENT",
            "",
        }:
            return value

        if len(secret) < 32:
            raise ValueError(
                "Secrets must contain at least 32 characters"
            )

        return value

    @field_validator("DEVICE_SECRET_ENCRYPTION_KEY")
    @classmethod
    def validate_device_encryption_key(
        cls,
        value: SecretStr,
    ) -> SecretStr:
        key = value.get_secret_value()

        if key == "CHANGE_ME_BEFORE_DEPLOYMENT":
            return value

        if not re.fullmatch(r"[0-9a-fA-F]{64}", key):
            raise ValueError(
                "DEVICE_SECRET_ENCRYPTION_KEY must be "
                "exactly 64 hexadecimal characters"
            )

        return value

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(
        cls,
        value: SecretStr,
    ) -> SecretStr:
        database_url = value.get_secret_value().strip()

        if not database_url:
            raise ValueError("DATABASE_URL cannot be empty")

        return value

    @model_validator(mode="after")
    def validate_security_configuration(self):
        if self.DEMO_ONLY is not True:
            raise ValueError(
                "GOLD BOT is restricted to DEMO accounts"
            )

        if self.MARKET_DATA_INGESTION_ENABLED:
            encryption_key = (
                self.DEVICE_SECRET_ENCRYPTION_KEY.get_secret_value()
            )

            if encryption_key == "CHANGE_ME_BEFORE_DEPLOYMENT":
                raise ValueError(
                    "Device secret encryption key is required "
                    "when market-data ingestion is enabled"
                )

        if self.APP_ENV == "production":
            jwt_secret = self.JWT_SECRET.get_secret_value()
            backend_key = self.BACKEND_API_KEY.get_secret_value()
            database_url = self.DATABASE_URL.get_secret_value()

            if jwt_secret == "CHANGE_ME_BEFORE_DEPLOYMENT":
                raise ValueError(
                    "JWT_SECRET must be configured in production"
                )

            if backend_key == "CHANGE_ME_BEFORE_DEPLOYMENT":
                raise ValueError(
                    "BACKEND_API_KEY must be configured in production"
                )

            if "CHANGE_ME" in database_url:
                raise ValueError(
                    "DATABASE_URL must be configured in production"
                )

            if self.MARKET_DATA_INGESTION_ENABLED:
                encryption_key = (
                    self.DEVICE_SECRET_ENCRYPTION_KEY.get_secret_value()
                )

                if encryption_key == "CHANGE_ME_BEFORE_DEPLOYMENT":
                    raise ValueError(
                        "DEVICE_SECRET_ENCRYPTION_KEY must be "
                        "configured in production"
                    )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
