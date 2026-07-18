from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from queryx.app.agent.orchestrator import ScanOrchestrator
from queryx.app.catalog.models import EnrichmentRequest
from queryx.app.catalog.service import CatalogService
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.core.config import Settings, get_settings
from queryx.app.llm.ollama_client import OllamaClient
from queryx.app.llm.semantic_enrichment import SemanticEnrichmentService
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


def _ollama_client(settings: Settings | None = None) -> OllamaClient:
    resolved = settings or get_settings()
    return OllamaClient(
        base_url=resolved.ollama_base_url,
        model=resolved.ollama_model,
        timeout_seconds=resolved.ollama_timeout_seconds,
        num_ctx=resolved.ollama_num_ctx,
        temperature=resolved.ollama_temperature,
        think=resolved.ollama_think,
        keep_alive=resolved.ollama_keep_alive,
    )


def _semantic_service(settings: Settings | None = None) -> SemanticEnrichmentService:
    resolved = settings or get_settings()
    return SemanticEnrichmentService(_catalog_service(resolved), _ollama_client(resolved), resolved)


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


@router.get("/llm/health")
def llm_health() -> dict[str, Any]:
    return _ollama_client().health()


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


@router.post("/sources/{source_id}/catalog/enrich")
def enrich_source_catalog(source_id: str, request: EnrichmentRequest | None = None) -> dict[str, Any]:
    source = _registry().get_source(source_id)
    if source is None:
        raise _not_found("source", source_id)
    try:
        run = _semantic_service().enrich_source(source, request or EnrichmentRequest())
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "technical_snapshot_not_found", "message": str(exc)}},
        ) from exc
    except Exception as exc:
        logger.exception("Semantic enrichment failed")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "semantic_enrichment_failed", "message": "Semantic enrichment failed"}},
        ) from exc
    return {
        "summary": {
            "run_id": run.id,
            "status": run.status,
            "reused": run.reused_result,
            "entities_processed": run.entities_processed,
            "fields_processed": run.fields_processed,
            "failures": run.failures,
        },
        "run": run.model_dump(mode="json"),
    }


@router.get("/sources/{source_id}/catalog/semantic/latest")
def latest_source_semantic_catalog(source_id: str) -> dict[str, Any]:
    source = _registry().get_source(source_id)
    if source is None:
        raise _not_found("source", source_id)
    run = _catalog_service().latest_enrichment_run(source_id)
    if run is None:
        raise _not_found("semantic_catalog", source_id)
    return run.model_dump(mode="json")


@router.get("/sources/{source_id}/catalog/semantic/history")
def source_semantic_history(source_id: str) -> dict[str, Any]:
    source = _registry().get_source(source_id)
    if source is None:
        raise _not_found("source", source_id)
    history = _catalog_service().enrichment_history(source_id)
    return {"source_id": source_id, "history": [run.model_dump(mode="json") for run in history]}


@router.get("/catalog/semantic/current")
def semantic_current_catalog() -> dict[str, Any]:
    registry = _registry()
    return _catalog_service().semantic_current(registry.list_sources(enabled_only=True))


@router.get("/enrichment/runs/{run_id}")
def get_enrichment_run(run_id: int) -> dict[str, Any]:
    run = _catalog_service().enrichment_run(run_id)
    if run is None:
        raise _not_found("enrichment_run", str(run_id))
    return run.model_dump(mode="json")
