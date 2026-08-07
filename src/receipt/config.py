"""Configuración de la aplicación, leída del entorno."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False

    database_url: str
    test_database_url: str | None = None

    session_secret: str
    session_cookie_name: str = "receipt_session"
    session_max_age_days: int = 30

    azure_storage_connection_string: str
    blob_container: str = "receipts"

    public_base_url: str = "http://localhost:8000"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cookies_require_https(self) -> bool:
        """`Secure` en la cookie de sesión (§3.1), salvo en local sobre HTTP."""
        return self.is_production


@lru_cache
def get_settings() -> Settings:
    return Settings()
