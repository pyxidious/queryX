from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from queryx.app.catalog.storage import CatalogStorage
from queryx.app.catalog.fingerprint import schema_fingerprint
from queryx.app.sources.registry import SourceRegistry


class MySQLCatalogAssetError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MySQLCatalogAsset:
    asset_id: str
    asset_version_id: str
    source_id: str
    name: str
    schema: str
    table: str
    fields: dict[str, dict[str, Any]]


class MySQLCatalogAssets:
    def __init__(self, storage: CatalogStorage, registry: SourceRegistry) -> None:
        self.storage = storage
        self.registry = registry

    def list_ready_assets(self) -> list[MySQLCatalogAsset]:
        assets: list[MySQLCatalogAsset] = []
        for source in self.registry.list_sources(enabled_only=True):
            if source.database_type != "mysql":
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
    ) -> MySQLCatalogAsset | None:
        for source in self.registry.list_sources(enabled_only=False):
            if source.database_type != "mysql":
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
                raise MySQLCatalogAssetError(
                    "mysql_source_not_ready",
                    "The MySQL catalog source is not current and ready",
                )
            if asset_version_id and asset_version_id != matched.asset_version_id:
                raise MySQLCatalogAssetError(
                    "asset_version_not_ready",
                    "The requested MySQL catalog version is not current",
                )
            return matched
        return None

    @staticmethod
    def _from_result(source_id: str, schema: str, result: Any) -> list[MySQLCatalogAsset]:
        if result.scan_run_id is None:
            return []
        tables = result.declared_metadata.get("tables", [])
        assets: list[MySQLCatalogAsset] = []
        for table in tables:
            if not isinstance(table, dict) or not isinstance(table.get("name"), str):
                continue
            table_name = table["name"]
            asset_id = mysql_asset_id(source_id, table_name)
            table_fingerprint = schema_fingerprint(
                "mysql", {"tables": [table]}, {}
            )
            fields = {
                column["name"]: {
                    "name": column["name"],
                    "data_type": str(column.get("type", "UNKNOWN")),
                    "nullable": bool(column.get("nullable", True)),
                }
                for column in table.get("columns", [])
                if isinstance(column, dict) and isinstance(column.get("name"), str)
            }
            if not fields:
                continue
            assets.append(
                MySQLCatalogAsset(
                    asset_id=asset_id,
                    asset_version_id=mysql_asset_version_id(asset_id, table_fingerprint),
                    source_id=source_id,
                    name=table_name,
                    schema=schema,
                    table=table_name,
                    fields=fields,
                )
            )
        return assets


def mysql_asset_id(source_id: str, table: str) -> str:
    digest = hashlib.sha256(f"{source_id}\0{table}".encode()).hexdigest()[:32]
    return f"mysql_{digest}"


def mysql_asset_version_id(asset_id: str, table_fingerprint: str) -> str:
    digest = hashlib.sha256(f"{asset_id}\0{table_fingerprint}".encode()).hexdigest()[:32]
    return f"mysqlv_{digest}"
