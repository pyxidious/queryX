from __future__ import annotations

import threading
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from queryx.app.query.compiler import CompiledQuery
from queryx.app.worker.coordination import SharedFileLock, SharedLockTimeout


class QueryExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class DuckDBQueryExecutor:
    def __init__(
        self, database_path: Path, lock_path: Path, lock_timeout_seconds: float,
        timeout_seconds: float,
    ) -> None:
        self.database_path = database_path
        self.lock_path = lock_path
        self.lock_timeout_seconds = lock_timeout_seconds
        self.timeout_seconds = timeout_seconds

    def execute(self, compiled: CompiledQuery) -> tuple[list[str], list[list[Any]], bool, float]:
        started = time.perf_counter()
        timed_out = threading.Event()
        try:
            with SharedFileLock(self.lock_path, self.lock_timeout_seconds):
                with duckdb.connect(str(self.database_path), read_only=True) as connection:
                    timer = threading.Timer(
                        self.timeout_seconds, self._interrupt, args=(connection, timed_out)
                    )
                    timer.daemon = True
                    timer.start()
                    try:
                        cursor = connection.execute(compiled.sql, compiled.parameters)
                        columns = [item[0] for item in cursor.description]
                        raw_rows = cursor.fetchall()
                    finally:
                        timer.cancel()
        except SharedLockTimeout as exc:
            raise QueryExecutionError("duckdb_lock_timeout", "DuckDB is temporarily busy", 503) from exc
        except Exception as exc:
            if timed_out.is_set():
                raise QueryExecutionError("query_timeout", "Query execution timed out", 408) from exc
            raise QueryExecutionError("query_execution_failed", "Query execution failed") from exc
        elapsed_ms = (time.perf_counter() - started) * 1000
        if timed_out.is_set():
            raise QueryExecutionError("query_timeout", "Query execution timed out", 408)
        truncated = len(raw_rows) > compiled.result_limit
        rows = raw_rows[: compiled.result_limit]
        return columns, [[_json_value(value) for value in row] for row in rows], truncated, elapsed_ms

    @staticmethod
    def _interrupt(connection: duckdb.DuckDBPyConnection, timed_out: threading.Event) -> None:
        timed_out.set()
        connection.interrupt()


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return f"<binary:{len(value)} bytes>"
    return str(value)

