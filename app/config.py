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

    # Cuenta concreta donde vas a operar
    CTRADER_ACCOUNT_ID: int

    # Demo o real (por ahora solo cambia la intención, podemos usarlo en broker/ctrader.py)
    CTRADER_ENV: CTraderEnvironment = "demo"

    # Endpoint base de la API (lo dejamos configurable por si cambia o querés mockear)
    CTRADER_API_BASE_URL: str = "https://api.spotware.com"  # ajustable

    # Tokens OAuth2 (al principio podés pegarlos a mano;
    # luego idealmente se persisten en DB y se refrescan con lógica propia)
    CTRADER_ACCESS_TOKEN: Optional[str] = None
    CTRADER_REFRESH_TOKEN: Optional[str] = None

    # -------- DB (opcional, por si usás db.py) --------
    DATABASE_URL: Optional[str] = None

    # Configuración general de pydantic-settings
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # ignora variables extras en .env
    )


@lru_cache
def get_settings() -> Settings:
    """Singleton de Settings para reutilizar en toda la app."""
    return Settings()
