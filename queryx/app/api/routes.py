from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status

from queryx.app.agent.orchestrator import ScanOrchestrator
from queryx.app.catalog.models import EnrichmentRequest
from queryx.app.catalog.service import CatalogService
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.core.config import Settings, get_settings
from queryx.app.llm.ollama_client import OllamaClient
from queryx.app.llm.semantic_enrichment import SemanticEnrichmentService
from queryx.app.ingestion.service import IngestionService, IngestionServiceError
from queryx.app.processing.service import ProcessingService, ProcessingServiceError
from queryx.app.sources.registry import SourceRegistry
from queryx.app.worker.facade import TaskCoordinator
from queryx.app.worker.service import WorkerService
from queryx.app.worker.storage import WorkItemConflictError

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


def _ingestion_service(settings: Settings | None = None) -> IngestionService:
    return IngestionService(settings or get_settings())


def _processing_service(settings: Settings | None = None) -> ProcessingService:
    return ProcessingService(settings or get_settings())


def _worker_service(settings: Settings | None = None) -> WorkerService:
    return WorkerService(settings or get_settings())


def _task_coordinator(settings: Settings | None = None) -> TaskCoordinator:
    ingestion = _ingestion_service()
    resolved = settings or ingestion.settings
    return TaskCoordinator(
        resolved,
        ingestion=ingestion,
        initialize_processing=False,
    )


def _processing_coordinator() -> TaskCoordinator:
    processing = _processing_service()
    return TaskCoordinator(
        processing.settings,
        processing=processing,
        initialize_ingestion=False,
    )


def _not_found(resource: str, identifier: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": {"code": f"{resource}_not_found", "message": f"{resource} '{identifier}' not found"}},
    )


def health() -> dict[str, Any]:
    settings = get_settings()
    orchestrator = _build_orchestrator(settings)
    checks = orchestrator.health_checks()
    worker_status: dict[str, object] | None = None
    worker_ok = True
    if settings.queryx_execution_mode == "worker":
        worker_status = _worker_service(settings).status()
        worker_ok = worker_status["status"] == "online"
        checks["worker"] = {"ok": worker_ok, "status": worker_status["status"]}
    return {
        "status": "ok" if checks and all(check["ok"] for check in checks.values()) and worker_ok else "degraded",
        "checks": checks,
    }


@router.get("/health")
async def health_endpoint() -> dict[str, Any]:
    return health()


@router.get("/worker/status")
async def worker_status() -> dict[str, object]:
    return _worker_service().status()


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


@router.post("/ingestions/uploads", status_code=status.HTTP_201_CREATED)
async def upload_ingestion(
    response: Response,
    file: UploadFile = File(...),
    asset_id: str | None = Form(default=None),
) -> dict[str, Any]:
    try:
        coordinator = _task_coordinator()
        result = await coordinator.submit_ingestion(file, asset_id=asset_id)
        if coordinator.settings.queryx_execution_mode == "worker":
            response.status_code = status.HTTP_202_ACCEPTED
        return result.model_dump(mode="json")
    except IngestionServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "error": {"code": exc.code, "message": exc.message},
                "job_id": exc.job_id,
            },
        ) from exc


@router.get("/ingestions/{job_id}")
async def get_ingestion(job_id: str) -> dict[str, Any]:
    job = _ingestion_service().get_job(job_id)
    if job is None:
        raise _not_found("ingestion_job", job_id)
    return job.model_dump(mode="json")


@router.get("/ingestions/{job_id}/preview")
async def get_ingestion_preview(job_id: str) -> dict[str, Any]:
    try:
        preview = _ingestion_service().get_preview(job_id)
    except IngestionServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": {"code": exc.code, "message": exc.message}},
        ) from exc
    if preview is None:
        raise _not_found("ingestion_job", job_id)
    return preview


@router.post("/ingestions/{job_id}/cancel")
async def cancel_ingestion(job_id: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        job, cancelled_item = _task_coordinator(settings).cancel_ingestion(job_id)
        return {
            "job": job.model_dump(mode="json") if job else None,
            "work_item": cancelled_item.model_dump(mode="json") if cancelled_item else None,
        }
    except (IngestionServiceError, WorkItemConflictError) as exc:
        code = getattr(exc, "code", "ingestion_not_cancellable")
        message = getattr(exc, "message", str(exc))
        status_code = getattr(exc, "status_code", 409)
        raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}}) from exc


@router.get("/assets")
async def list_assets() -> dict[str, Any]:
    assets = _ingestion_service().list_assets()
    return {"assets": [asset.model_dump(mode="json") for asset in assets]}


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: str) -> dict[str, Any]:
    asset = _ingestion_service().get_asset(asset_id)
    if asset is None:
        raise _not_found("asset", asset_id)
    return asset.model_dump(mode="json")


@router.get("/assets/{asset_id}/versions")
async def list_asset_versions(asset_id: str) -> dict[str, Any]:
    versions = _ingestion_service().list_versions(asset_id)
    if versions is None:
        raise _not_found("asset", asset_id)
    return {"asset_id": asset_id, "versions": [version.model_dump(mode="json") for version in versions]}


@router.get("/assets/{asset_id}/versions/{version_id}")
async def get_asset_version(asset_id: str, version_id: str) -> dict[str, Any]:
    version = _ingestion_service().get_version(asset_id, version_id)
    if version is None:
        if _ingestion_service().get_asset(asset_id) is None:
            raise _not_found("asset", asset_id)
        raise _not_found("asset_version", version_id)
    return version.model_dump(mode="json")


@router.get("/assets/{asset_id}/diff")
async def get_asset_diff(asset_id: str) -> dict[str, Any]:
    if _ingestion_service().get_asset(asset_id) is None:
        raise _not_found("asset", asset_id)
    diff = _ingestion_service().get_latest_diff(asset_id)
    if diff is None:
        raise _not_found("asset_diff", asset_id)
    return diff.model_dump(mode="json")


@router.get("/assets/{asset_id}/versions/{version_id}/diff")
async def get_asset_version_diff(asset_id: str, version_id: str) -> dict[str, Any]:
    if _ingestion_service().get_asset(asset_id) is None:
        raise _not_found("asset", asset_id)
    diff = _ingestion_service().get_version_diff(asset_id, version_id)
    if diff is None:
        raise _not_found("asset_version", version_id)
    return diff.model_dump(mode="json")


@router.post("/assets/{asset_id}/versions/{version_id}/prepare")
async def prepare_asset_version(asset_id: str, version_id: str, response: Response) -> dict[str, Any]:
    try:
        coordinator = _processing_coordinator()
        submission = coordinator.submit_processing(asset_id, version_id)
        payload = submission.run.model_dump(mode="json")
        if submission.work_item is not None:
            response.status_code = status.HTTP_202_ACCEPTED
            payload["work_item_id"] = submission.work_item.id
        return payload
    except ProcessingServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": {"code": exc.code, "message": exc.message}, "run_id": exc.run_id},
        ) from exc


@router.get("/processing/runs/{run_id}")
async def get_processing_run(run_id: str) -> dict[str, Any]:
    run = _processing_service().get_run(run_id)
    if run is None:
        raise _not_found("processing_run", run_id)
    return run.model_dump(mode="json")


@router.post("/processing/runs/{run_id}/cancel")
async def cancel_processing_run(run_id: str) -> dict[str, Any]:
    try:
        run, cancelled_item = _processing_coordinator().cancel_processing(run_id)
        return {
            "run": run.model_dump(mode="json") if run else None,
            "work_item": cancelled_item.model_dump(mode="json") if cancelled_item else None,
        }
    except (ProcessingServiceError, WorkItemConflictError) as exc:
        code = getattr(exc, "code", "processing_not_cancellable")
        message = getattr(exc, "message", str(exc))
        status_code = getattr(exc, "status_code", 409)
        raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}}) from exc


@router.get("/assets/{asset_id}/versions/{version_id}/bindings")
async def get_asset_version_bindings(asset_id: str, version_id: str) -> dict[str, Any]:
    bindings = _processing_service().list_bindings(asset_id, version_id)
    if bindings is None:
        raise _not_found("asset_version", version_id)
    return {
        "asset_id": asset_id,
        "asset_version_id": version_id,
        "bindings": [binding.model_dump(mode="json") for binding in bindings],
    }


@router.get("/assets/{asset_id}/versions/{version_id}/data-preview")
async def get_asset_version_data_preview(
    asset_id: str,
    version_id: str,
    request: Request,
    limit: int = 10,
) -> dict[str, Any]:
    unsupported = set(request.query_params.keys()) - {"limit"}
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "unsupported_preview_parameter",
                    "message": "Only the bounded 'limit' parameter is supported",
                }
            },
        )
    try:
        return _processing_service().data_preview(asset_id, version_id, limit)
    except ProcessingServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": {"code": exc.code, "message": exc.message}},
        ) from exc
