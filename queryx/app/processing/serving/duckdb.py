from __future__ import annotations

import re
import threading
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DDL_LOCK = threading.RLock()


class DuckDBServingError(RuntimeError):
    pass


class DuckDBServingAdapter:
    def __init__(self, database_path: Path, schema: str) -> None:
        if not _SAFE_IDENTIFIER.fullmatch(schema):
            raise ValueError("Unsafe DuckDB schema identifier")
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.schema = schema

    def register_view(self, relation_name: str, parquet_path: Path) -> list[dict[str, Any]]:
        relation = self._identifier(relation_name)
        quoted_schema = self._quote_identifier(self.schema)
        quoted_relation = self._quote_identifier(relation)
        escaped_path = str(parquet_path.resolve()).replace("'", "''")
        with _DDL_LOCK, duckdb.connect(str(self.database_path)) as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}")
                connection.execute(
                    f"CREATE OR REPLACE VIEW {quoted_schema}.{quoted_relation} "
                    f"AS SELECT * FROM read_parquet('{escaped_path}')"
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get_schema(relation)

    def drop_view(self, relation_name: str) -> None:
        relation = self._identifier(relation_name)
        with _DDL_LOCK, duckdb.connect(str(self.database_path)) as connection:
            connection.execute(
                f"DROP VIEW IF EXISTS {self._quote_identifier(self.schema)}.{self._quote_identifier(relation)}"
            )

    def view_exists(self, relation_name: str) -> bool:
        relation = self._identifier(relation_name)
        if not self.database_path.exists():
            return False
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            row = connection.execute(
                """SELECT 1 FROM information_schema.views
                   WHERE table_schema = ? AND table_name = ? LIMIT 1""",
                [self.schema, relation],
            ).fetchone()
        return row is not None

    def get_schema(self, relation_name: str) -> list[dict[str, Any]]:
        relation = self._identifier(relation_name)
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            rows = connection.execute(
                f"DESCRIBE SELECT * FROM {self._quote_identifier(self.schema)}.{self._quote_identifier(relation)}"
            ).fetchall()
        return [
            {"name": row[0], "data_type": row[1], "nullable": str(row[2]).upper() == "YES"}
            for row in rows
        ]

    def preview(self, relation_name: str, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        relation = self._identifier(relation_name)
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            cursor = connection.execute(
                f"SELECT * FROM {self._quote_identifier(self.schema)}.{self._quote_identifier(relation)} LIMIT ?",
                [limit],
            )
            names = [item[0] for item in cursor.description]
            rows = [
                {name: _json_value(value) for name, value in zip(names, row, strict=True)}
                for row in cursor.fetchall()
            ]
        return self.get_schema(relation), rows

    def list_views(self) -> list[str]:
        if not self.database_path.exists():
            return []
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            rows = connection.execute(
                "SELECT table_name FROM information_schema.views WHERE table_schema = ? ORDER BY table_name",
                [self.schema],
            ).fetchall()
        return [row[0] for row in rows]

    @staticmethod
    def _identifier(value: str) -> str:
        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise DuckDBServingError("Unsafe DuckDB relation identifier")
        return value

    @staticmethod
    def _quote_identifier(value: str) -> str:
        return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return f"<binary:{len(value)} bytes>"
    return str(value)
