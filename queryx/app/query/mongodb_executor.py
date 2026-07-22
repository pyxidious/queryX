from __future__ import annotations

import time
from typing import Any, Callable

from pymongo import MongoClient
from pymongo.errors import (
    ConnectionFailure,
    ExecutionTimeout,
    NetworkTimeout,
    PyMongoError,
    ServerSelectionTimeoutError,
)

from queryx.app.query.executor import QueryExecutionError, _json_value
from queryx.app.query.mongodb_compiler import MongoDBCompiledQuery


class MongoDBQueryExecutor:
    def __init__(
        self,
        url: str,
        timeout_seconds: float,
        *,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        timeout_ms = max(1, int(timeout_seconds * 1000))
        self.client_factory = client_factory or (
            lambda: MongoClient(
                self.url,
                serverSelectionTimeoutMS=timeout_ms,
                connectTimeoutMS=timeout_ms,
                socketTimeoutMS=timeout_ms,
            )
        )

    def execute(
        self, compiled: MongoDBCompiledQuery
    ) -> tuple[list[str], list[list[Any]], bool, float]:
        started = time.perf_counter()
        client = None
        try:
            client = self.client_factory()
            collection = client[compiled.database][compiled.collection]
            documents = list(
                collection.aggregate(
                    compiled.pipeline,
                    maxTimeMS=max(1, int(self.timeout_seconds * 1000)),
                )
            )
        except (ExecutionTimeout, NetworkTimeout) as exc:
            raise QueryExecutionError(
                "query_timeout", "MongoDB query execution timed out", 408
            ) from exc
        except (ServerSelectionTimeoutError, ConnectionFailure) as exc:
            raise QueryExecutionError(
                "mongodb_connection_failed", "MongoDB connection failed", 503
            ) from exc
        except PyMongoError as exc:
            raise QueryExecutionError(
                "mongodb_query_execution_failed", "MongoDB query execution failed"
            ) from exc
        finally:
            if client is not None:
                client.close()
        elapsed_ms = (time.perf_counter() - started) * 1000
        truncated = len(documents) > compiled.result_limit
        documents = documents[: compiled.result_limit]
        columns = [field.name for field in compiled.output_schema]
        rows = [
            [_json_value(document.get(column)) for column in columns]
            for document in documents
        ]
        return columns, rows, truncated, elapsed_ms
