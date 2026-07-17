from __future__ import annotations

import logging
from typing import Any

from queryx.app.catalog.models import ScanError, ScanSummary, SourceMetadata
from queryx.app.catalog.service import CatalogService
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.connectors.base import ConnectorError, MetadataConnector
from queryx.app.connectors.mongodb import MongoDBConnector
from queryx.app.connectors.mysql import MySQLConnector
from queryx.app.core.config import Settings

logger = logging.getLogger(__name__)


class ScanOrchestrator:
    def __init__(self, connectors: list[MetadataConnector], catalog: CatalogService) -> None:
        self.connectors = connectors
        self.catalog = catalog

    @classmethod
    def from_settings(cls, settings: Settings) -> ScanOrchestrator:
        connectors: list[MetadataConnector] = [
            MySQLConnector(settings.mysql_url, settings.connection_timeout_seconds),
            MongoDBConnector(
                settings.mongodb_url,
                settings.mongodb_database,
                settings.mongo_sample_size,
                settings.connection_timeout_seconds,
            ),
        ]
        storage = CatalogStorage(settings.catalog_db_path)
        return cls(connectors=connectors, catalog=CatalogService(storage))

    def health_checks(self) -> dict[str, dict[str, Any]]:
        return {connector.source: connector.health_check() for connector in self.connectors}

    def scan(self) -> dict[str, Any]:
        scanned: list[SourceMetadata] = []
        errors: list[ScanError] = []

        for connector in self.connectors:
            try:
                scanned.append(connector.scan())
            except ConnectorError as exc:
                logger.info("Skipping unavailable source %s: %s", connector.source, exc)
                errors.append(
                    ScanError(
                        source=connector.source,
                        database_type=connector.database_type,
                        message=str(exc),
                    )
                )

        snapshot = self.catalog.save_scan(scanned)
        summary = ScanSummary(
            snapshot_id=snapshot.id,
            created_at=snapshot.created_at,
            sources_scanned=len(scanned),
            sources_failed=len(errors),
            errors=errors,
        )
        return {
            "summary": summary.model_dump(mode="json"),
            "snapshot": snapshot.model_dump(mode="json"),
        }
