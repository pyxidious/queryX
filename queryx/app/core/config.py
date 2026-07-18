from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
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
    data_raw_dir: Path = Path("data/raw")
    data_staging_dir: Path = Path("data/staging")
    data_normalized_dir: Path = Path("data/normalized")
    ingestion_max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    ingestion_preview_rows: int = Field(default=10, ge=1, le=100)
    ingestion_inspection_rows: int = Field(default=100, ge=1, le=10_000)
    ingestion_csv_count_rows: int = Field(default=10_000, ge=1)
    mongo_sample_size: int = Field(default=25, ge=1, le=1000)
    connection_timeout_seconds: int = Field(default=3, ge=1, le=30)

    profiling_enabled: bool = True
    profiling_max_records_per_entity: int = Field(default=25, ge=0)
    profiling_max_seconds_per_entity: float = Field(default=2.0, ge=0)
    profiling_max_entities: int = Field(default=100, ge=0)
    profiling_max_total_records: int = Field(default=500, ge=0)

    ollama_base_url: str = Field(
        default="http://host.docker.internal:11434",
        validation_alias=AliasChoices("OLLAMA_BASE_URL", "OLLAMA_URL"),
    )
    ollama_model: str = "qwen3.5:9b"
    ollama_timeout_seconds: int = Field(default=120, ge=1)
    ollama_num_ctx: int = Field(default=8192, ge=512)
    ollama_temperature: float = Field(default=0, ge=0)
    ollama_think: bool = False
    ollama_keep_alive: str = "10m"
    ollama_debug_prompts: bool = False

    queryx_enrichment_max_entities: int = Field(default=50, ge=1)
    queryx_enrichment_max_fields_per_request: int = Field(default=40, ge=1)
    queryx_enrichment_max_retries: int = Field(default=1, ge=0)
    queryx_enrichment_max_prompt_chars: int = Field(default=12000, ge=1000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
