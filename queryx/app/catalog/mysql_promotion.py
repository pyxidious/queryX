from __future__ import annotations

from pathlib import Path
from typing import Any

from queryx.app.catalog.fingerprint import schema_fingerprint
from queryx.app.catalog.models import SourceScanResult
from queryx.app.ingestion.storage import IngestionStorage
from queryx.app.query.mysql_catalog import mysql_asset_id, mysql_asset_version_id


class MySQLAssetPromoter:
    def __init__(self, db_path: Path) -> None:
        self.storage = IngestionStorage(db_path)

    def promote(self, result: SourceScanResult, database: str) -> None:
        tables = result.declared_metadata.get("tables", [])
        promoted: list[dict[str, Any]] = []
        for table in tables:
            if not isinstance(table, dict) or not isinstance(table.get("name"), str):
                continue
            name = table["name"]
            asset_id = mysql_asset_id(result.source_id, name)
            fingerprint = schema_fingerprint("mysql", {"tables": [table]}, {})
            fields = [
                {
                    "name": column["name"],
                    "data_type": str(column.get("type", "UNKNOWN")),
                    "nullable": bool(column.get("nullable", True)),
                }
                for column in table.get("columns", [])
                if isinstance(column, dict) and isinstance(column.get("name"), str)
            ]
            if not fields:
                continue
            promoted.append(
                {
                    "asset_id": asset_id,
                    "version_id": mysql_asset_version_id(asset_id, fingerprint),
                    "name": name,
                    "schema_fingerprint": fingerprint,
                    "technical_metadata": {
                        "asset_kind": "mysql_table",
                        "source_id": result.source_id,
                        "database": database,
                        "schema": database,
                        "table": name,
                        "fields": fields,
                        "primary_key": table.get("primary_key") or {},
                        "foreign_keys": table.get("foreign_keys") or [],
                        "indexes": table.get("indexes") or [],
                        "schema_fingerprint": fingerprint,
                    },
                }
            )
        self.storage.sync_mysql_assets(result.source_id, promoted)

    def mark_source_not_current(self, source_id: str) -> None:
        self.storage.sync_mysql_assets(source_id, [])
