from __future__ import annotations

import hashlib
import json
from typing import Any

from queryx.app.catalog.models import DatabaseType


def schema_fingerprint(
    database_type: DatabaseType,
    declared_metadata: dict[str, Any],
    inferred_metadata: dict[str, Any],
) -> str:
    normalized = normalized_schema(database_type, declared_metadata, inferred_metadata)
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalized_schema(
    database_type: DatabaseType,
    declared_metadata: dict[str, Any],
    inferred_metadata: dict[str, Any],
) -> dict[str, Any]:
    if database_type == "mysql":
        return _normalize_mysql(declared_metadata)
    return _normalize_mongodb(declared_metadata, inferred_metadata)


def _normalize_mysql(declared_metadata: dict[str, Any]) -> dict[str, Any]:
    tables = []
    for table in declared_metadata.get("tables", []):
        tables.append(
            {
                "name": table.get("name"),
                "columns": _sort_by_name(
                    {
                        "name": column.get("name"),
                        "type": column.get("type"),
                        "nullable": bool(column.get("nullable", True)),
                    }
                    for column in table.get("columns", [])
                ),
                "primary_key": {
                    "columns": sorted(table.get("primary_key", {}).get("columns", [])),
                },
                "foreign_keys": sorted(
                    (
                        {
                            "columns": sorted(foreign_key.get("columns", [])),
                            "referred_table": foreign_key.get("referred_table"),
                            "referred_columns": sorted(foreign_key.get("referred_columns", [])),
                        }
                        for foreign_key in table.get("foreign_keys", [])
                    ),
                    key=lambda item: (
                        item["referred_table"] or "",
                        ",".join(item["columns"]),
                        ",".join(item["referred_columns"]),
                    ),
                ),
                "indexes": _normalize_indexes(table.get("indexes", [])),
            }
        )
    return {"database_type": "mysql", "tables": _sort_by_name(tables)}


def _normalize_mongodb(
    declared_metadata: dict[str, Any],
    inferred_metadata: dict[str, Any],
) -> dict[str, Any]:
    inferred_by_name = {
        collection.get("name"): collection for collection in inferred_metadata.get("collections", [])
    }
    collections = []
    for collection in declared_metadata.get("collections", []):
        name = collection.get("name")
        inferred = inferred_by_name.get(name, {})
        collections.append(
            {
                "name": name,
                "indexes": _normalize_indexes(collection.get("indexes", [])),
                "validator": _canonicalize(collection.get("validator")),
                "fields": sorted(
                    (
                        {
                            "path": field.get("path"),
                            "types": sorted(field.get("types", [])),
                        }
                        for field in inferred.get("fields", [])
                    ),
                    key=lambda item: item["path"] or "",
                ),
            }
        )
    return {"database_type": "mongodb", "collections": _sort_by_name(collections)}


def _normalize_indexes(indexes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for index in indexes:
        name = index.get("name")
        unique = bool(index.get("unique", False))
        if name == "_id_":
            unique = True
        keys = index.get("keys", index.get("columns", []))
        normalized.append(
            {
                "name": name,
                "keys": _normalize_index_keys(keys),
                "unique": unique,
            }
        )
    return sorted(normalized, key=lambda item: (item["name"] or "", json.dumps(item["keys"])))


def _normalize_index_keys(keys: Any) -> list[Any]:
    if not isinstance(keys, list):
        return []
    normalized = []
    for key in keys:
        if isinstance(key, (list, tuple)) and len(key) >= 2:
            normalized.append([key[0], key[1]])
        else:
            normalized.append(key)
    return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))


def _sort_by_name(items: Any) -> list[dict[str, Any]]:
    return sorted(list(items), key=lambda item: item.get("name") or "")


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value
