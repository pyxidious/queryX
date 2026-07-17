from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from time import monotonic
from typing import Any

from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from queryx.app.catalog.models import ProfilingBudget, SourceMetadata
from queryx.app.connectors.base import ConnectorError, MetadataConnector

logger = logging.getLogger(__name__)


class MongoDBConnector(MetadataConnector):
    source = "mongodb"
    database_type = "mongodb"

    def __init__(
        self,
        url: str,
        database: str,
        sample_size: int = 25,
        timeout_seconds: int = 3,
        source_id: str = "mongodb",
        profiling_budget: ProfilingBudget | None = None,
    ) -> None:
        self.url = url
        self.database_name = database
        self.sample_size = sample_size
        self.source_id = source_id
        self.profiling_budget = profiling_budget or ProfilingBudget()
        self.client: MongoClient[Any] = MongoClient(
            url,
            serverSelectionTimeoutMS=timeout_seconds * 1000,
            connectTimeoutMS=timeout_seconds * 1000,
        )

    def health_check(self) -> dict[str, Any]:
        try:
            self.client.admin.command("ping")
            return {"ok": True}
        except PyMongoError as exc:
            logger.warning("MongoDB health check failed: %s", exc)
            return {"ok": False, "error": "MongoDB is not reachable"}

    def scan(self) -> SourceMetadata:
        try:
            database = self.client[self.database_name]
            collections: list[dict[str, Any]] = []
            inferred_collections: list[dict[str, Any]] = []
            profiling_metrics: dict[str, Any] = {
                "enabled": self.profiling_budget.enabled,
                "entities": [],
                "total_records_sampled": 0,
                "entities_not_profiled": [],
                "limits_reached": [],
                "timeouts": [],
            }

            for collection_name in database.list_collection_names():
                collection = database[collection_name]
                indexes = [
                    {
                        "name": index["name"],
                        "keys": [[key, direction] for key, direction in index["key"].items()],
                        "unique": bool(index.get("unique", False) or index["name"] == "_id_"),
                    }
                    for index in collection.list_indexes()
                ]
                options = database.command("listCollections", filter={"name": collection_name})["cursor"][
                    "firstBatch"
                ][0].get("options", {})
                validator = options.get("validator")
                documents = self._sample_documents(collection, collection_name, profiling_metrics)
                collections.append({"name": collection_name, "indexes": indexes})
                if validator is not None:
                    collections[-1]["validator"] = validator
                inferred_collections.append(
                    {
                        "name": collection_name,
                        "sample_size": len(documents),
                        "sample_scope": "limited_documents",
                        "fields": infer_mongo_schema(documents),
                    }
                )

            return SourceMetadata(
                source=self.source_id,
                database_type=self.database_type,
                declared={"collections": collections},
                inferred={"collections": inferred_collections},
                profiling_metrics=profiling_metrics,
            )
        except PyMongoError as exc:
            logger.warning("MongoDB scan failed: %s", exc)
            raise ConnectorError("MongoDB is not reachable") from exc

    def _sample_documents(
        self,
        collection: Any,
        collection_name: str,
        metrics: dict[str, Any],
    ) -> list[dict[str, Any]]:
        budget = self.profiling_budget
        if not budget.enabled:
            metrics["entities_not_profiled"].append(collection_name)
            return []
        if len(metrics["entities"]) >= budget.max_entities:
            metrics["entities_not_profiled"].append(collection_name)
            metrics["limits_reached"].append("max_entities")
            return []
        remaining = budget.max_total_records - int(metrics["total_records_sampled"])
        if remaining <= 0:
            metrics["entities_not_profiled"].append(collection_name)
            metrics["limits_reached"].append("max_total_records")
            return []
        limit = min(self.sample_size, budget.max_records_per_entity, remaining)
        started = monotonic()
        documents = list(collection.find({}, limit=limit)) if limit > 0 else []
        duration = monotonic() - started
        if duration > budget.max_seconds_per_entity:
            metrics["timeouts"].append(collection_name)
        metrics["entities"].append(
            {
                "name": collection_name,
                "records_sampled": len(documents),
                "sample_scope": "limited_documents",
            }
        )
        metrics["total_records_sampled"] += len(documents)
        return documents


def infer_mongo_schema(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    total = len(documents)

    for document in documents:
        seen_in_document: set[str] = set()
        _visit_document(document, "", stats, seen_in_document)
        for path in seen_in_document:
            stats[path]["documents_present"] += 1

    fields: list[dict[str, Any]] = []
    for path, values in sorted(stats.items()):
        documents_present = int(values["documents_present"])
        fields.append(
            {
                "path": path,
                "types": sorted(values["types"]),
                "documents_present": documents_present,
                "presence": documents_present / total if total else 0.0,
            }
        )
    return fields


def _visit_document(
    value: Any,
    prefix: str,
    stats: dict[str, dict[str, Any]],
    seen_in_document: set[str],
) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            _record(stats, seen_in_document, path, nested_value)
            _visit_document(nested_value, path, stats, seen_in_document)
    elif isinstance(value, list):
        item_path = f"{prefix}[]"
        for item in value:
            _record(stats, seen_in_document, item_path, item)
            _visit_document(item, item_path, stats, seen_in_document)


def _record(
    stats: dict[str, dict[str, Any]],
    seen_in_document: set[str],
    path: str,
    value: Any,
) -> None:
    entry = stats.setdefault(
        path,
        {"types": set(), "documents_present": 0},
    )
    entry["types"].add(_type_name(value))
    seen_in_document.add(path)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, str):
        return "str"
    if isinstance(value, (datetime, date)):
        return "datetime"
    if isinstance(value, ObjectId):
        return "object_id"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
