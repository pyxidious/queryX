from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from queryx.app.catalog.fingerprint import schema_fingerprint
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.sources.registry import SourceRegistry


class MongoDBCatalogAssetError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MongoDBCatalogAsset:
    asset_id: str
    asset_version_id: str
    source_id: str
    name: str
    database: str
    collection: str
    fields: dict[str, dict[str, Any]]
    indexes: list[dict[str, Any]]
    schema_fingerprint: str


class MongoDBCatalogAssets:
    def __init__(self, storage: CatalogStorage, registry: SourceRegistry) -> None:
        self.storage = storage
        self.registry = registry

    def list_ready_assets(self) -> list[MongoDBCatalogAsset]:
        assets: list[MongoDBCatalogAsset] = []
        for source in self.registry.list_sources(enabled_only=True):
            if source.database_type != "mongodb":
                continue
            latest = self.storage.get_latest_source_result(source.id)
            successful = self.storage.get_latest_successful_source_result(source.id)
            if (
                latest is None
                or successful is None
                or latest.scan_status != "completed"
                or latest.scan_run_id != successful.scan_run_id
            ):
                continue
            assets.extend(self._from_result(source.id, source.database, successful))
        return assets

    def resolve(
        self, asset_id: str, asset_version_id: str | None = None
    ) -> MongoDBCatalogAsset | None:
        for source in self.registry.list_sources(enabled_only=False):
            if source.database_type != "mongodb":
                continue
            successful = self.storage.get_latest_successful_source_result(source.id)
            if successful is None:
                continue
            matched = next(
                (
                    asset
                    for asset in self._from_result(source.id, source.database, successful)
                    if asset.asset_id == asset_id
                ),
                None,
            )
            if matched is None:
                continue
            latest = self.storage.get_latest_source_result(source.id)
            if (
                not source.enabled
                or latest is None
                or latest.scan_status != "completed"
                or latest.scan_run_id != successful.scan_run_id
            ):
                raise MongoDBCatalogAssetError(
                    "mongodb_source_not_ready",
                    "The MongoDB catalog source is not current and ready",
                )
            if asset_version_id and asset_version_id != matched.asset_version_id:
                raise MongoDBCatalogAssetError(
                    "asset_version_not_ready",
                    "The requested MongoDB catalog version is not current",
                )
            return matched
        return None

    @staticmethod
    def _from_result(
        source_id: str, database: str, result: Any
    ) -> list[MongoDBCatalogAsset]:
        if result.scan_run_id is None:
            return []
        inferred_by_name = {
            item.get("name"): item
            for item in result.inferred_metadata.get("collections", [])
            if isinstance(item, dict)
        }
        assets: list[MongoDBCatalogAsset] = []
        for declared in result.declared_metadata.get("collections", []):
            if not isinstance(declared, dict) or not isinstance(declared.get("name"), str):
                continue
            name = declared["name"]
            inferred = inferred_by_name.get(name, {})
            fingerprint = schema_fingerprint(
                "mongodb",
                {"collections": [declared]},
                {"collections": [inferred]},
            )
            fields = mongo_fields(inferred)
            if not fields:
                continue
            asset_id = mongodb_asset_id(source_id, name)
            assets.append(
                MongoDBCatalogAsset(
                    asset_id=asset_id,
                    asset_version_id=mongodb_asset_version_id(asset_id, fingerprint),
                    source_id=source_id,
                    name=name,
                    database=database,
                    collection=name,
                    fields=fields,
                    indexes=list(declared.get("indexes") or []),
                    schema_fingerprint=fingerprint,
                )
            )
        return assets


def mongo_fields(inferred: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sample_size = int(inferred.get("sample_size") or 0)
    fields: dict[str, dict[str, Any]] = {}
    for field in inferred.get("fields", []):
        if not isinstance(field, dict) or not isinstance(field.get("path"), str):
            continue
        path = field["path"]
        types = [str(value) for value in field.get("types", [])]
        present = int(field.get("documents_present") or 0)
        fields[path] = {
            "name": path,
            "data_type": _mongo_data_type(types),
            "nullable": "null" in types or sample_size == 0 or present < sample_size,
            "observed_types": types,
        }
    return fields


def _mongo_data_type(types: list[str]) -> str:
    non_null = {value.casefold() for value in types if value.casefold() != "null"}
    if non_null and non_null <= {"int", "float", "decimal"}:
        return "DOUBLE" if "float" in non_null else "DECIMAL"
    if non_null == {"str"}:
        return "STRING"
    if non_null == {"datetime"}:
        return "TIMESTAMP"
    if non_null == {"bool"}:
        return "BOOLEAN"
    if non_null == {"object_id"}:
        return "OBJECT_ID"
    if non_null == {"array"}:
        return "ARRAY"
    if non_null == {"object"}:
        return "OBJECT"
    return "MIXED" if non_null else "NULL"


def mongodb_asset_id(source_id: str, collection: str) -> str:
    digest = hashlib.sha256(f"{source_id}\0{collection}".encode()).hexdigest()[:32]
    return f"mongodb_{digest}"


def mongodb_asset_version_id(asset_id: str, fingerprint: str) -> str:
    digest = hashlib.sha256(f"{asset_id}\0{fingerprint}".encode()).hexdigest()[:32]
    return f"mongodbv_{digest}"
