from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from queryx.app.api import routes
from queryx.app.agent.orchestrator import ScanOrchestrator
from queryx.app.catalog.models import DataSource
from queryx.app.catalog.service import CatalogService
from queryx.app.catalog.storage import CatalogStorage
from tests.test_scan_runs_and_current import _FailConnector, _OkConnector


class _Registry:
    def __init__(self) -> None:
        self.sources = [
            DataSource(id="mysql", name="MySQL", database_type="mysql", host="x", port=3306, database="db"),
            DataSource(id="mongodb", name="MongoDB", database_type="mongodb", host="x", port=27017, database="db"),
        ]

    def list_sources(self, enabled_only: bool = False) -> list[DataSource]:
        return self.sources

    def get_source(self, source_id: str) -> DataSource | None:
        return next((source for source in self.sources if source.id == source_id), None)


def test_api_unknown_source_returns_structured_error() -> None:
    original = routes._registry
    routes._registry = lambda settings=None: _Registry()  # type: ignore[assignment]
    try:
        with pytest.raises(HTTPException) as exc:
            routes.get_source("missing")
    finally:
        routes._registry = original

    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["code"] == "source_not_found"


def test_api_latest_snapshot_missing_returns_structured_error(tmp_path: Path) -> None:
    original = routes._catalog_service
    routes._catalog_service = lambda settings=None: CatalogService(CatalogStorage(tmp_path / "empty.sqlite3"))  # type: ignore[assignment]
    try:
        with pytest.raises(HTTPException) as exc:
            routes.latest_catalog()
    finally:
        routes._catalog_service = original

    assert exc.value.status_code == 404
    assert "Traceback" not in str(exc.value.detail)


def test_api_current_catalog_returns_stale_source(tmp_path: Path) -> None:
    catalog = CatalogService(CatalogStorage(tmp_path / "catalog.sqlite3"))
    ScanOrchestrator([_OkConnector("mysql"), _OkConnector("mongodb")], catalog).scan()
    ScanOrchestrator([_OkConnector("mysql"), _FailConnector("mongodb")], catalog).scan()
    registry = _Registry()
    original_registry = routes._registry
    original_catalog = routes._catalog_service
    routes._registry = lambda settings=None: registry  # type: ignore[assignment]
    routes._catalog_service = lambda settings=None: catalog  # type: ignore[assignment]
    try:
        response = routes.current_catalog()
    finally:
        routes._registry = original_registry
        routes._catalog_service = original_catalog

    by_source = {source["source_id"]: source for source in response["sources"]}
    assert by_source["mongodb"]["freshness_status"] == "stale"
    assert by_source["mongodb"]["latest_scan_failed"] is True


def test_api_scan_error_response_has_no_stack_trace() -> None:
    original = routes._build_orchestrator
    routes._build_orchestrator = lambda settings=None: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[assignment]
    try:
        with pytest.raises(HTTPException) as exc:
            routes.scan_catalog()
    finally:
        routes._build_orchestrator = original

    assert exc.value.status_code == 500
    assert exc.value.detail["error"]["code"] == "scan_failed"
    assert "Traceback" not in str(exc.value.detail)
