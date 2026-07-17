from __future__ import annotations

from queryx.app.catalog.drift import detect_schema_drift
from queryx.app.catalog.models import (
    CatalogSnapshot,
    CurrentCatalog,
    DataSource,
    DriftReport,
    ScanRun,
    SourceMetadata,
    SourceScanResult,
)
from queryx.app.catalog.storage import CatalogStorage


class CatalogService:
    def __init__(self, storage: CatalogStorage) -> None:
        self.storage = storage

    def save_scan(self, sources: list[SourceMetadata]) -> CatalogSnapshot:
        return self.storage.save_snapshot(sources)

    def latest(self) -> CatalogSnapshot | None:
        return self.storage.get_latest_snapshot()

    def upsert_sources(self, sources: list[DataSource]) -> None:
        self.storage.upsert_sources(sources)

    def save_run(self, run: ScanRun) -> ScanRun:
        return self.storage.save_scan_run(run)

    def latest_run(self) -> ScanRun | None:
        return self.storage.get_latest_scan_run()

    def latest_successful_source(self, source_id: str) -> SourceScanResult | None:
        return self.storage.get_latest_successful_source_result(source_id)

    def source_history(self, source_id: str) -> list[SourceScanResult]:
        return self.storage.get_source_history(source_id)

    def current_catalog(self, sources: list[DataSource]) -> CurrentCatalog:
        return self.storage.get_current_catalog(sources)

    def source_diff(self, source: DataSource) -> DriftReport:
        latest_two = self.storage.get_two_latest_successful_source_results(source.id)
        if len(latest_two) < 2:
            current = latest_two[0] if latest_two else None
            return detect_schema_drift(source.database_type, None, current)
        return detect_schema_drift(source.database_type, latest_two[1], latest_two[0])
