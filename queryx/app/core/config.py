from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, env_file_encoding="utf-8", extra="ignore")

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
    ingestion_stale_job_seconds: int = Field(default=300, ge=1)
    duckdb_path: Path = Path("data/queryx.duckdb")
    duckdb_schema: str = Field(default="queryx_managed", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    processing_preview_rows: int = Field(default=10, ge=1, le=100)
    processing_stale_run_seconds: int = Field(default=300, ge=1)
    parquet_compression: str = Field(default="zstd", pattern=r"^(zstd|snappy|gzip|none)$")
    parquet_batch_rows: int = Field(default=10_000, ge=1, le=1_000_000)
    queryx_execution_mode: Literal["inline", "worker"] = "inline"
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_lease_seconds: int = Field(default=60, ge=2, le=3600)
    worker_heartbeat_seconds: int = Field(default=10, ge=1, le=600)
    worker_max_attempts: int = Field(default=3, ge=1, le=100)
    worker_retry_base_seconds: int = Field(default=2, ge=1, le=3600)
    worker_reconcile_seconds: int = Field(default=60, ge=1, le=86_400)
    worker_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    worker_shutdown_seconds: int = Field(default=30, ge=1, le=600)
    duckdb_lock_path: Path = Path("data/queryx.duckdb.lock")
    duckdb_lock_timeout_seconds: float = Field(default=5.0, gt=0, le=300)
    queryx_ui_enabled: bool = True
    queryx_ui_secret_key: str = Field(
        default="queryx-ui-development-key-change-me",
        min_length=16,
        repr=False,
    )
    queryx_ui_max_preview_columns: int = Field(default=50, ge=1, le=500)
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
    return Settings(_env_file=".env")
