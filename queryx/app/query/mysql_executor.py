from __future__ import annotations

import time
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError

from queryx.app.query.executor import QueryExecutionError, _json_value
from queryx.app.query.mysql_compiler import MySQLCompiledQuery


class MySQLQueryExecutor:
    def __init__(
        self,
        url: str,
        timeout_seconds: float,
        *,
        engine: Engine | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.engine = engine or create_engine(
            url,
            pool_pre_ping=True,
            isolation_level="AUTOCOMMIT",
            connect_args={
                "connect_timeout": max(1, int(timeout_seconds)),
                "read_timeout": max(1, int(timeout_seconds)),
                "write_timeout": max(1, int(timeout_seconds)),
            },
        )

    def execute(
        self, compiled: MySQLCompiledQuery
    ) -> tuple[list[str], list[list[Any]], bool, float]:
        started = time.perf_counter()
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SET SESSION TRANSACTION READ ONLY"))
                connection.execute(
                    text("SET SESSION MAX_EXECUTION_TIME = :timeout_ms"),
                    {"timeout_ms": max(1, int(self.timeout_seconds * 1000))},
                )
                cursor = connection.execute(text(compiled.sql), compiled.parameters)
                columns = list(cursor.keys())
                raw_rows = cursor.fetchall()
        except OperationalError as exc:
            if _is_timeout(exc):
                raise QueryExecutionError(
                    "query_timeout", "MySQL query execution timed out", 408
                ) from exc
            if _is_connection_error(exc):
                raise QueryExecutionError(
                    "mysql_connection_failed", "MySQL connection failed", 503
                ) from exc
            raise QueryExecutionError(
                "mysql_query_execution_failed", "MySQL query execution failed"
            ) from exc
        except DBAPIError as exc:
            if _is_timeout(exc):
                raise QueryExecutionError(
                    "query_timeout", "MySQL query execution timed out", 408
                ) from exc
            if exc.connection_invalidated:
                raise QueryExecutionError(
                    "mysql_connection_failed", "MySQL connection failed", 503
                ) from exc
            raise QueryExecutionError(
                "mysql_query_execution_failed", "MySQL query execution failed"
            ) from exc
        except SQLAlchemyError as exc:
            raise QueryExecutionError(
                "mysql_connection_failed", "MySQL connection failed", 503
            ) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000
        truncated = len(raw_rows) > compiled.result_limit
        rows = raw_rows[: compiled.result_limit]
        return (
            columns,
            [[_json_value(value) for value in row] for row in rows],
            truncated,
            elapsed_ms,
        )


def _is_timeout(error: DBAPIError) -> bool:
    message = str(error.orig).casefold()
    code = error.orig.args[0] if getattr(error.orig, "args", ()) else None
    return code in {1317, 3024} or "timeout" in message or "maximum statement execution" in message


def _is_connection_error(error: DBAPIError) -> bool:
    message = str(error.orig).casefold()
    code = error.orig.args[0] if getattr(error.orig, "args", ()) else None
    return bool(error.connection_invalidated) or code in {
        1042, 1043, 2002, 2003, 2005, 2006, 2013,
    } or "connect" in message
