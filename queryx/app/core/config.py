from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "QueryX"
    log_level: str = "INFO"

    mysql_url: str = Field(
        default="mysql+pymysql://queryx:queryx@mysql:3306/queryx_demo",
        description="SQLAlchemy URL for the MySQL metadata source.",
    )
    mongodb_url: str = "mongodb://queryx:queryx@mongodb:27017/queryx_demo?authSource=admin"
    mongodb_database: str = "queryx_demo"

    catalog_db_path: Path = Path("data/queryx_catalog.sqlite3")
    mongo_sample_size: int = Field(default=25, ge=1, le=1000)
    connection_timeout_seconds: int = Field(default=3, ge=1, le=30)

    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
