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
    mysql_source_id: str = "mysql"
    mysql_source_name: str = "Demo MySQL"
    mysql_host: str = "mysql"
    mysql_port: int = 3306
    mysql_database: str = "queryx_demo"
    mysql_user: str = "queryx"
    mysql_password: str = "queryx"
    mysql_enabled: bool = True

    mongodb_url: str = "mongodb://queryx:queryx@mongodb:27017/queryx_demo?authSource=admin"
    mongodb_source_id: str = "mongodb"
    mongodb_source_name: str = "Demo MongoDB"
    mongodb_host: str = "mongodb"
    mongodb_port: int = 27017
    mongodb_database: str = "queryx_demo"
    mongodb_user: str = "queryx"
    mongodb_password: str = "queryx"
    mongodb_enabled: bool = True

    catalog_db_path: Path = Path("data/queryx_catalog.sqlite3")
    mongo_sample_size: int = Field(default=25, ge=1, le=1000)
    connection_timeout_seconds: int = Field(default=3, ge=1, le=30)

    profiling_enabled: bool = True
    profiling_max_records_per_entity: int = Field(default=25, ge=0)
    profiling_max_seconds_per_entity: float = Field(default=2.0, ge=0)
    profiling_max_entities: int = Field(default=100, ge=0)
    profiling_max_total_records: int = Field(default=500, ge=0)

    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
