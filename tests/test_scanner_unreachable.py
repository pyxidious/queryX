from __future__ import annotations

from pathlib import Path

from queryx.app.agent.orchestrator import ScanOrchestrator
from queryx.app.catalog.models import SourceMetadata
from queryx.app.catalog.service import CatalogService
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.connectors.base import ConnectorError, MetadataConnector


class _BrokenConnector(MetadataConnector):
    source = "mysql"
    database_type = "mysql"

    def health_check(self) -> dict[str, bool]:
        return {"ok": False}

    def scan(self) -> SourceMetadata:
        raise ConnectorError("MySQL is not reachable")


def test_scan_records_error_for_unreachable_source(tmp_path: Path) -> None:
    orchestrator = ScanOrchestrator(
        connectors=[_BrokenConnector()],
        catalog=CatalogService(CatalogStorage(tmp_path / "catalog.sqlite3")),
    )

    result = orchestrator.scan()

    assert result["summary"]["sources_scanned"] == 0
    assert result["summary"]["sources_failed"] == 1
    assert result["summary"]["errors"][0]["source"] == "mysql"
    assert result["snapshot"]["sources"] == []
