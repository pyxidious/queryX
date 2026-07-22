from __future__ import annotations

from pathlib import Path

import pytest

from queryx.app.agent.orchestrator import ScanAlreadyRunning, ScanOrchestrator
from queryx.app.catalog.models import DataSource, SourceMetadata
from queryx.app.catalog.service import CatalogService
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.connectors.base import ConnectorError, MetadataConnector
from queryx.app.ingestion.storage import IngestionStorage


class _OkConnector(MetadataConnector):
    database_type = "mysql"

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.source = source_id

    def health_check(self) -> dict[str, bool]:
        return {"ok": True}

    def scan(self) -> SourceMetadata:
        return SourceMetadata(
            source=self.source_id,
            database_type="mysql",
            declared={
                "tables": [
                    {
                        "name": "customers",
                        "columns": [{"name": "id", "type": "INTEGER", "nullable": False}],
                        "primary_key": {"columns": ["id"]},
                        "foreign_keys": [],
                        "indexes": [],
                    }
                ]
            },
        )


class _FailConnector(_OkConnector):
    def scan(self) -> SourceMetadata:
        raise ConnectorError("source is down")


def _orchestrator(tmp_path: Path, connectors: list[MetadataConnector]) -> ScanOrchestrator:
    return ScanOrchestrator(connectors, CatalogService(CatalogStorage(tmp_path / "catalog.sqlite3")))


def test_scan_run_statuses(tmp_path: Path) -> None:
    assert _orchestrator(tmp_path / "a", [_OkConnector("mysql"), _OkConnector("mongodb")]).scan()["summary"]["status"] == "completed"
    assert _orchestrator(tmp_path / "b", [_OkConnector("mysql"), _FailConnector("mongodb")]).scan()["summary"]["status"] == "partial"
    assert _orchestrator(tmp_path / "c", [_FailConnector("mysql"), _FailConnector("mongodb")]).scan()["summary"]["status"] == "failed"


def test_latest_successful_snapshot_and_current_catalog_stale(tmp_path: Path) -> None:
    storage = CatalogStorage(tmp_path / "catalog.sqlite3")
    catalog = CatalogService(storage)
    ScanOrchestrator([_OkConnector("mysql"), _OkConnector("mongodb")], catalog).scan()
    ScanOrchestrator([_OkConnector("mysql"), _FailConnector("mongodb")], catalog).scan()

    latest_mongo = catalog.latest_successful_source("mongodb")
    current = catalog.current_catalog(
        [
            DataSource(id="mysql", name="MySQL", database_type="mysql", host="x", port=3306, database="db"),
            DataSource(id="mongodb", name="MongoDB", database_type="mongodb", host="x", port=27017, database="db"),
        ]
    )
    by_source = {source.source_id: source for source in current.sources}

    assert latest_mongo is not None
    assert latest_mongo.scan_status == "completed"
    assert by_source["mysql"].freshness_status == "current"
    assert by_source["mongodb"].freshness_status == "stale"
    assert by_source["mongodb"].latest_scan_failed is True
    assert "mongodb" in by_source


def test_history_keeps_multiple_scans_in_descending_order(tmp_path: Path) -> None:
    catalog = CatalogService(CatalogStorage(tmp_path / "catalog.sqlite3"))
    ScanOrchestrator([_OkConnector("mysql")], catalog).scan()
    ScanOrchestrator([_OkConnector("mysql")], catalog).scan()

    history = catalog.source_history("mysql")

    assert len(history) == 2
    assert history[0].scan_run_id is not None
    assert history[1].scan_run_id is not None
    assert history[0].scan_run_id > history[1].scan_run_id


def test_completed_mysql_scan_promotes_asset_and_concurrent_scan_is_rejected(
    tmp_path: Path,
) -> None:
    storage = CatalogStorage(tmp_path / "catalog.sqlite3")
    catalog = CatalogService(storage)
    source = DataSource(
        id="mysql", name="MySQL", database_type="mysql", host="x",
        port=3306, database="db", enabled=True,
    )
    catalog.upsert_sources([source])
    orchestrator = ScanOrchestrator([_OkConnector("mysql")], catalog, [source])

    result = orchestrator.scan("mysql")
    assets = IngestionStorage(storage.db_path).list_assets()
    assert result["run_id"] is not None
    assert [(asset.name, str(asset.asset_kind)) for asset in assets] == [
        ("customers", "mysql_table")
    ]

    assert catalog.acquire_source_scan("mysql", "other-job") is True
    try:
        with pytest.raises(ScanAlreadyRunning):
            orchestrator.scan("mysql")
    finally:
        catalog.release_source_scan("mysql", "other-job")
