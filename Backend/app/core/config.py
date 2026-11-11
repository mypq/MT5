from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
model_config = SettingsConfigDict(
env_file=".env",
env_file_encoding="utf-8",
case_sensitive=False,
extra="ignore",
)

APP_NAME: str = "GOLD BOT"
APP_VERSION: str = "0.1.0"
APP_ENV: Literal["development", "production", "testing"] = "development"

API_HOST: str = "127.0.0.1"
API_PORT: int = Field(default=8000, ge=1, le=65535)

DATABASE_URL: SecretStr = SecretStr(
    "postgresql+psycopg://goldbot:CHANGE_ME@localhost:5432/goldbot"
)

JWT_SECRET: SecretStr = SecretStr("CHANGE_ME_BEFORE_DEPLOYMENT")
JWT_ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=5, le=1440)

DEMO_ONLY: bool = True
AUTOMATED_TRADING_ENABLED: bool = False

MAX_RISK_PER_TRADE_PERCENT: float = Field(default=0.5, gt=0, le=0.5)
MAX_DAILY_LOSS_PERCENT: float = Field(default=1.0, gt=0, le=1.0)
MAX_SIMULTANEOUS_POSITIONS: int = Field(default=1, ge=1, le=1)

MARKET_DATA_MAX_AGE_SECONDS: int = Field(default=10, ge=1, le=60)
COMMAND_EXPIRY_SECONDS: int = Field(default=30, ge=5, le=120)

BACKEND_API_KEY: SecretStr = SecretStr("CHANGE_ME_BEFORE_DEPLOYMENT")

@field_validator("JWT_SECRET", "BACKEND_API_KEY")
@classmethod
def validate_secrets(cls, value: SecretStr) -> SecretStr:
    if value.get_secret_value() in {
        "CHANGE_ME_BEFORE_DEPLOYMENT",
        "",
    }:
        return value
    if len(value.get_secret_value()) < 32:
        raise ValueError("Secrets must contain at least 32 characters")
    return value

@lru_cache
def get_settings() -> Settings:
return Settings()

settings = get_settings()
