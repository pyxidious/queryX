from __future__ import annotations

import logging
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from queryx.app.catalog.fingerprint import schema_fingerprint
from queryx.app.catalog.models import DataSource, RunStatus, ScanRun, SourceScanResult
from queryx.app.catalog.service import CatalogService
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.connectors.base import ConnectorError, MetadataConnector
from queryx.app.connectors.mongodb import MongoDBConnector
from queryx.app.connectors.mysql import MySQLConnector
from queryx.app.core.config import Settings
from queryx.app.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)


class ScanOrchestrator:
    def __init__(
        self,
        connectors: list[MetadataConnector],
        catalog: CatalogService,
        sources: list[DataSource] | None = None,
    ) -> None:
        self.connectors = connectors
        self.catalog = catalog
        self.sources = sources or []

    @classmethod
    def from_settings(cls, settings: Settings) -> ScanOrchestrator:
        registry = SourceRegistry(settings)
        sources = registry.list_sources(enabled_only=True)
        budget = registry.profiling_budget()
        connectors: list[MetadataConnector] = []
        for source in sources:
            if source.database_type == "mysql":
                connectors.append(
                    MySQLConnector(
                        registry.connection_url(source.id),
                        settings.connection_timeout_seconds,
                        source.id,
                        budget,
                    )
                )
            else:
                connectors.append(
                    MongoDBConnector(
                        registry.connection_url(source.id),
                        source.database,
                        settings.mongo_sample_size,
                        settings.connection_timeout_seconds,
                        source.id,
                        budget,
                    )
                )
        storage = CatalogStorage(settings.catalog_db_path)
        catalog = CatalogService(storage)
        catalog.upsert_sources(registry.list_sources())
        return cls(connectors=connectors, catalog=catalog, sources=sources)

    def health_checks(self) -> dict[str, dict[str, Any]]:
        return {self._connector_source_id(connector): connector.health_check() for connector in self.connectors}

    def scan(self, source_id: str | None = None) -> dict[str, Any]:
        selected = [
            connector
            for connector in self.connectors
            if source_id is None or self._connector_source_id(connector) == source_id
        ]
        started_at = datetime.now(timezone.utc)
        started = monotonic()
        results = [self._scan_connector(connector) for connector in selected]
        finished_at = datetime.now(timezone.utc)
        succeeded = sum(1 for result in results if result.scan_status == "completed")
        failed = sum(1 for result in results if result.scan_status == "failed")
        run = ScanRun(
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((monotonic() - started) * 1000),
            status=self._run_status(succeeded, failed, len(selected)),
            sources_succeeded=succeeded,
            sources_failed=failed,
            warnings=[
                warning
                for result in results
                for warning in result.warnings
            ],
            errors=[result.error for result in results if result.error is not None],
            results=results,
        )
        saved = self.catalog.save_run(run)
        latest_snapshot = self.catalog.latest()
        return {
            "summary": {
                "snapshot_id": saved.id,
                "created_at": saved.finished_at.isoformat(),
                "started_at": saved.started_at.isoformat(),
                "finished_at": saved.finished_at.isoformat(),
                "duration_ms": saved.duration_ms,
                "status": saved.status,
                "sources_scanned": saved.sources_succeeded,
                "sources_failed": saved.sources_failed,
                "warnings": saved.warnings,
                "errors": saved.errors,
            },
            "scan_run": saved.model_dump(mode="json"),
            "snapshot": latest_snapshot.model_dump(mode="json") if latest_snapshot else None,
        }

    def _scan_connector(self, connector: MetadataConnector) -> SourceScanResult:
        source_id = self._connector_source_id(connector)
        started_at = datetime.now(timezone.utc)
        started = monotonic()
        try:
            metadata = connector.scan()
            fingerprint = schema_fingerprint(
                metadata.database_type,
                metadata.declared,
                metadata.inferred,
            )
            finished_at = datetime.now(timezone.utc)
            return SourceScanResult(
                source_id=metadata.source,
                database_type=metadata.database_type,
                scan_status="completed",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=int((monotonic() - started) * 1000),
                fingerprint=fingerprint,
                declared_metadata=metadata.declared,
                inferred_metadata=metadata.inferred,
                profiling_metrics=metadata.profiling_metrics,
            )
        except ConnectorError as exc:
            logger.info("Skipping unavailable source %s: %s", source_id, exc)
            finished_at = datetime.now(timezone.utc)
            return SourceScanResult(
                source_id=source_id,
                database_type=connector.database_type,
                scan_status="failed",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=int((monotonic() - started) * 1000),
                warnings=["Source scan failed; previous successful metadata remains usable if available"],
                error={
                    "code": "source_unavailable",
                    "message": str(exc),
                    "source": source_id,
                    "source_id": source_id,
                },
            )

    @staticmethod
    def _connector_source_id(connector: MetadataConnector) -> str:
        return getattr(connector, "source_id", connector.source)

    @staticmethod
    def _run_status(succeeded: int, failed: int, requested: int) -> RunStatus:
        if requested == 0 or succeeded == 0:
            return "failed"
        if failed > 0:
            return "partial"
        return "completed"
