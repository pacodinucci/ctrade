# app/config.py
from functools import lru_cache
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


EnvType = Literal["dev", "prod"]
CTraderEnvironment = Literal["demo", "live"]


class Settings(BaseSettings):
    # -------- App --------
    APP_NAME: str = "cTrader Bot API"
    ENV: EnvType = "dev"
    DEBUG: bool = True

    # -------- cTrader / Spotware Open API --------
    # Datos de tu app registrada en cTrader / Spotware
    CTRADER_CLIENT_ID: str
    CTRADER_CLIENT_SECRET: str

    # A dónde te devuelve cTrader luego del login (la que configuraste en el portal)
    CTRADER_REDIRECT_URI: str

    # Cuenta concreta donde vas a operar (ctidTraderAccountId)
    CTRADER_ACCOUNT_ID: int
    CTRADER_TRADER_ACCOUNT_ID: int

    # Demo o real
    CTRADER_ENV: CTraderEnvironment = "demo"

    # Endpoint base de la API (si después querés REST, etc.)
    CTRADER_API_BASE_URL: str = "https://api.spotware.com"  # ajustable

    # Tokens OAuth2 (por ahora pegados a mano, luego DB)
    CTRADER_ACCESS_TOKEN: Optional[str] = None
    CTRADER_REFRESH_TOKEN: Optional[str] = None

    # -------- DB (opcional, por si usás db.py) --------
    DATABASE_URL: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Singleton de Settings para reutilizar en toda la app."""
    return Settings()


settings: Settings = get_settings()
