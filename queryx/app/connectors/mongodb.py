from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from queryx.app.catalog.models import SourceMetadata
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
    ) -> None:
        self.url = url
        self.database_name = database
        self.sample_size = sample_size
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

            for collection_name in database.list_collection_names():
                collection = database[collection_name]
                indexes = [
                    {
                        "name": index["name"],
                        "keys": [[key, direction] for key, direction in index["key"].items()],
                        "unique": bool(index.get("unique", False)),
                    }
                    for index in collection.list_indexes()
                ]
                documents = list(collection.find({}, limit=self.sample_size))
                collections.append({"name": collection_name, "indexes": indexes})
                inferred_collections.append(
                    {
                        "name": collection_name,
                        "sample_size": len(documents),
                        "fields": infer_mongo_schema(documents),
                    }
                )

            return SourceMetadata(
                source=self.source,
                database_type=self.database_type,
                declared={"collections": collections},
                inferred={"collections": inferred_collections},
            )
        except PyMongoError as exc:
            logger.warning("MongoDB scan failed: %s", exc)
            raise ConnectorError("MongoDB is not reachable") from exc


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
