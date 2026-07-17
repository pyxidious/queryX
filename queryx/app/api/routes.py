from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from queryx.app.agent.orchestrator import ScanOrchestrator
from queryx.app.catalog.service import CatalogService
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.core.config import Settings, get_settings
from queryx.app.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)
router = APIRouter()


def _build_orchestrator(settings: Settings | None = None) -> ScanOrchestrator:
    resolved = settings or get_settings()
    return ScanOrchestrator.from_settings(resolved)


def _registry(settings: Settings | None = None) -> SourceRegistry:
    return SourceRegistry(settings or get_settings())


def _catalog_service(settings: Settings | None = None) -> CatalogService:
    resolved = settings or get_settings()
    registry = _registry(resolved)
    storage = CatalogStorage(resolved.catalog_db_path)
    service = CatalogService(storage)
    service.upsert_sources(registry.list_sources())
    return service


def _not_found(resource: str, identifier: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": {"code": f"{resource}_not_found", "message": f"{resource} '{identifier}' not found"}},
    )


@router.get("/health")
def health() -> dict[str, Any]:
    orchestrator = _build_orchestrator()
    checks = orchestrator.health_checks()
    return {
        "status": "ok" if checks and all(check["ok"] for check in checks.values()) else "degraded",
        "checks": checks,
    }


@router.get("/sources")
def list_sources() -> dict[str, Any]:
    return {"sources": [source.model_dump(mode="json") for source in _registry().list_sources()]}


@router.get("/sources/{source_id}")
def get_source(source_id: str) -> dict[str, Any]:
    source = _registry().get_source(source_id)
    if source is None:
        raise _not_found("source", source_id)
    return source.model_dump(mode="json")


@router.post("/sources/{source_id}/scan")
def scan_source(source_id: str) -> dict[str, Any]:
    registry = _registry()
    source = registry.get_source(source_id)
    if source is None:
        raise _not_found("source", source_id)
    if not source.enabled:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "source_disabled", "message": f"source '{source_id}' is disabled"}},
        )
    try:
        return _build_orchestrator().scan(source_id=source_id)
    except Exception as exc:
        logger.exception("Source scan failed")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "scan_failed", "message": "Catalog scan failed"}},
        ) from exc


@router.post("/catalog/scan")
def scan_catalog() -> dict[str, Any]:
    try:
        return _build_orchestrator().scan()
    except Exception as exc:
        logger.exception("Catalog scan failed")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "scan_failed", "message": "Catalog scan failed"}},
        ) from exc


@router.get("/catalog/latest")
def latest_catalog() -> dict[str, Any]:
    snapshot = _catalog_service().latest()
    if snapshot is None:
        raise _not_found("catalog_snapshot", "latest")
    return snapshot.model_dump(mode="json")


@router.get("/catalog/current")
def current_catalog() -> dict[str, Any]:
    registry = _registry()
    current = _catalog_service().current_catalog(registry.list_sources(enabled_only=True))
    return current.model_dump(mode="json")


@router.get("/sources/{source_id}/catalog/latest")
def latest_source_catalog(source_id: str) -> dict[str, Any]:
    source = _registry().get_source(source_id)
    if source is None:
        raise _not_found("source", source_id)
    result = _catalog_service().latest_successful_source(source_id)
    if result is None:
        raise _not_found("source_catalog_snapshot", source_id)
    return result.model_dump(mode="json")


@router.get("/sources/{source_id}/catalog/history")
def source_catalog_history(source_id: str) -> dict[str, Any]:
    source = _registry().get_source(source_id)
    if source is None:
        raise _not_found("source", source_id)
    history = _catalog_service().source_history(source_id)
    return {"source_id": source_id, "history": [item.model_dump(mode="json") for item in history]}


@router.get("/sources/{source_id}/catalog/diff")
def source_catalog_diff(source_id: str) -> dict[str, Any]:
    source = _registry().get_source(source_id)
    if source is None:
        raise _not_found("source", source_id)
    return _catalog_service().source_diff(source).model_dump(mode="json")
