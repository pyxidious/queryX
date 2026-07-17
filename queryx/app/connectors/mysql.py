from __future__ import annotations

import logging
from time import monotonic
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from queryx.app.catalog.models import ProfilingBudget, SourceMetadata
from queryx.app.connectors.base import ConnectorError, MetadataConnector

logger = logging.getLogger(__name__)


class MySQLConnector(MetadataConnector):
    source = "mysql"
    database_type = "mysql"

    def __init__(
        self,
        url: str,
        timeout_seconds: int = 3,
        source_id: str = "mysql",
        profiling_budget: ProfilingBudget | None = None,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.source_id = source_id
        self.profiling_budget = profiling_budget or ProfilingBudget()
        self.engine = self._create_engine()

    def _create_engine(self) -> Engine:
        return create_engine(
            self.url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": self.timeout_seconds},
        )

    def health_check(self) -> dict[str, Any]:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return {"ok": True}
        except SQLAlchemyError as exc:
            logger.warning("MySQL health check failed: %s", exc)
            return {"ok": False, "error": "MySQL is not reachable"}

    def scan(self) -> SourceMetadata:
        try:
            inspector = inspect(self.engine)
            tables: list[dict[str, Any]] = []
            for table_name in inspector.get_table_names():
                columns = inspector.get_columns(table_name)
                primary_key = inspector.get_pk_constraint(table_name)
                foreign_keys = inspector.get_foreign_keys(table_name)
                indexes = inspector.get_indexes(table_name)
                tables.append(
                    {
                        "name": table_name,
                        "columns": [
                            {
                                "name": column["name"],
                                "type": str(column["type"]),
                                "nullable": bool(column.get("nullable", True)),
                                "default": self._string_or_none(column.get("default")),
                            }
                            for column in columns
                        ],
                        "primary_key": {
                            "name": primary_key.get("name"),
                            "columns": primary_key.get("constrained_columns", []),
                        },
                        "foreign_keys": [
                            {
                                "name": foreign_key.get("name"),
                                "columns": foreign_key.get("constrained_columns", []),
                                "referred_table": foreign_key.get("referred_table"),
                                "referred_columns": foreign_key.get("referred_columns", []),
                            }
                            for foreign_key in foreign_keys
                        ],
                        "indexes": [
                            {
                                "name": index.get("name"),
                                "columns": index.get("column_names", []),
                                "unique": bool(index.get("unique", False)),
                            }
                            for index in indexes
                        ],
                    }
                )
            return SourceMetadata(
                source=self.source_id,
                database_type=self.database_type,
                declared={"tables": tables},
                inferred={},
                profiling_metrics=self._profile_tables([table["name"] for table in tables]),
            )
        except SQLAlchemyError as exc:
            logger.warning("MySQL scan failed: %s", exc)
            raise ConnectorError("MySQL is not reachable") from exc

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        return str(value)

    def _profile_tables(self, table_names: list[str]) -> dict[str, Any]:
        budget = self.profiling_budget
        metrics: dict[str, Any] = {
            "enabled": budget.enabled,
            "entities": [],
            "total_records_sampled": 0,
            "entities_not_profiled": [],
            "limits_reached": [],
            "timeouts": [],
        }
        if not budget.enabled:
            metrics["entities_not_profiled"] = table_names
            return metrics

        profiled_entities = 0
        total_records = 0
        with self.engine.connect() as connection:
            for table_name in table_names:
                if profiled_entities >= budget.max_entities:
                    metrics["entities_not_profiled"].append(table_name)
                    metrics["limits_reached"].append("max_entities")
                    continue
                remaining = budget.max_total_records - total_records
                if remaining <= 0:
                    metrics["entities_not_profiled"].append(table_name)
                    metrics["limits_reached"].append("max_total_records")
                    continue
                limit = min(budget.max_records_per_entity, remaining)
                started = monotonic()
                sampled = 0
                if limit > 0:
                    quoted_table = table_name.replace("`", "``")
                    result = connection.execute(text(f"SELECT 1 FROM `{quoted_table}` LIMIT :limit"), {"limit": limit})
                    sampled = len(result.fetchall())
                duration = monotonic() - started
                if duration > budget.max_seconds_per_entity:
                    metrics["timeouts"].append(table_name)
                metrics["entities"].append(
                    {
                        "name": table_name,
                        "records_sampled": sampled,
                        "sample_scope": "limited_rows",
                    }
                )
                total_records += sampled
                profiled_entities += 1
        metrics["total_records_sampled"] = total_records
        return metrics
