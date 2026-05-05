from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.modes import OperationMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_name: str = "auto-trader-backend"
    default_mode: OperationMode = OperationMode.SIMULATION
    enable_live_trading: bool = False
    enable_ai_execution: bool = False
    enable_futures_live_trading: bool = False
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    database_url: str = "sqlite:///./data/auto_trader.db"
    market_data_provider: Literal["mock", "yfinance"] = "mock"

    enable_fill_polling:           bool = False
    fill_polling_interval_seconds: int  = 5

    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account_no: str = ""
    kis_is_paper: bool = True
    kis_rate_limit_calls:          int   = 5
    kis_rate_limit_window_seconds: float = 1.0

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
