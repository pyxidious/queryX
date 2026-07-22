from __future__ import annotations

from pathlib import Path
from typing import Any

from queryx.app.catalog.models import SourceScanResult
from queryx.app.ingestion.storage import IngestionStorage
from queryx.app.query.mongodb_catalog import MongoDBCatalogAssets


class MongoDBAssetPromoter:
    def __init__(self, db_path: Path) -> None:
        self.storage = IngestionStorage(db_path)

    def promote(self, result: SourceScanResult, database: str) -> None:
        promoted: list[dict[str, Any]] = []
        for asset in MongoDBCatalogAssets._from_result(
            result.source_id, database, result
        ):
            promoted.append({
                "asset_id": asset.asset_id,
                "version_id": asset.asset_version_id,
                "name": asset.name,
                "schema_fingerprint": asset.schema_fingerprint,
                "technical_metadata": {
                    "asset_kind": "mongodb_collection",
                    "source_id": asset.source_id,
                    "database": asset.database,
                    "collection": asset.collection,
                    "fields": list(asset.fields.values()),
                    "indexes": asset.indexes,
                    "schema_fingerprint": asset.schema_fingerprint,
                },
            })
        self.storage.sync_mongodb_assets(result.source_id, promoted)

    def mark_source_not_current(self, source_id: str) -> None:
        self.storage.sync_mongodb_assets(source_id, [])
