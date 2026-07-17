from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from queryx.app.catalog.models import SourceMetadata
from queryx.app.connectors.base import ConnectorError, MetadataConnector

logger = logging.getLogger(__name__)


class MySQLConnector(MetadataConnector):
    source = "mysql"
    database_type = "mysql"

    def __init__(self, url: str, timeout_seconds: int = 3) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
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
                source=self.source,
                database_type=self.database_type,
                declared={"tables": tables},
                inferred={},
            )
        except SQLAlchemyError as exc:
            logger.warning("MySQL scan failed: %s", exc)
            raise ConnectorError("MySQL is not reachable") from exc

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        return str(value)
