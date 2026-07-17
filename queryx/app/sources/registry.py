from __future__ import annotations

from urllib.parse import quote_plus

from sqlalchemy.engine import URL

from queryx.app.catalog.models import DataSource, ProfilingBudget
from queryx.app.core.config import Settings


class SourceRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._sources = self._build_sources()

    def list_sources(self, enabled_only: bool = False) -> list[DataSource]:
        sources = list(self._sources.values())
        if enabled_only:
            return [source for source in sources if source.enabled]
        return sources

    def get_source(self, source_id: str) -> DataSource | None:
        return self._sources.get(source_id)

    def require_source(self, source_id: str) -> DataSource:
        source = self.get_source(source_id)
        if source is None:
            raise KeyError(source_id)
        return source

    def connection_url(self, source_id: str) -> str:
        source = self.require_source(source_id)
        if source.database_type == "mysql":
            if self.settings.mysql_url:
                return self.settings.mysql_url
            return str(
                URL.create(
                    "mysql+pymysql",
                    username=self.settings.mysql_user,
                    password=self.settings.mysql_password,
                    host=source.host,
                    port=source.port,
                    database=source.database,
                )
            )
        if self.settings.mongodb_url:
            return self.settings.mongodb_url
        return (
            f"mongodb://{quote_plus(self.settings.mongodb_user)}:"
            f"{quote_plus(self.settings.mongodb_password)}@{source.host}:{source.port}/"
            f"{source.database}?authSource=admin"
        )

    def profiling_budget(self) -> ProfilingBudget:
        return ProfilingBudget(
            enabled=self.settings.profiling_enabled,
            max_records_per_entity=self.settings.profiling_max_records_per_entity,
            max_seconds_per_entity=self.settings.profiling_max_seconds_per_entity,
            max_entities=self.settings.profiling_max_entities,
            max_total_records=self.settings.profiling_max_total_records,
        )

    def _build_sources(self) -> dict[str, DataSource]:
        mysql = DataSource(
            id=self.settings.mysql_source_id,
            name=self.settings.mysql_source_name,
            database_type="mysql",
            host=self.settings.mysql_host,
            port=self.settings.mysql_port,
            database=self.settings.mysql_database,
            enabled=self.settings.mysql_enabled,
        )
        mongodb = DataSource(
            id=self.settings.mongodb_source_id,
            name=self.settings.mongodb_source_name,
            database_type="mongodb",
            host=self.settings.mongodb_host,
            port=self.settings.mongodb_port,
            database=self.settings.mongodb_database,
            enabled=self.settings.mongodb_enabled,
        )
        return {source.id: source for source in (mysql, mongodb)}
