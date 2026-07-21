from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from queryx.app.agent.orchestrator import ScanOrchestrator
from queryx.app.catalog.service import CatalogService
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.core.config import Settings
from queryx.app.ingestion.models import DatasetProvenance, SourceProvider
from queryx.app.ingestion.service import IngestionService, IngestionServiceError
from queryx.app.processing.service import ProcessingService, ProcessingServiceError
from queryx.app.sources.registry import SourceRegistry
from queryx.app.ui.view_models import (
    AlertVM,
    asset_vm,
    badge,
    binding_vm,
    job_vm,
    preview_vm,
    processing_run_vm,
    schema_vm,
    scan_vm,
    source_vm,
    version_vm,
)
from queryx.app.worker.facade import TaskCoordinator
from queryx.app.worker.models import TaskType
from queryx.app.worker.service import WorkerService
from queryx.app.worker.storage import WorkItemConflictError, WorkerStorage


router = APIRouter(prefix="/ui", include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
_CSRF_COOKIE = "queryx_ui_csrf"
_STATIC_FILES = {
    "queryx.css": "text/css; charset=utf-8",
    "queryx-polling.js": "text/javascript; charset=utf-8",
}


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _services(request: Request) -> tuple[IngestionService, ProcessingService, WorkerStorage]:
    settings = _settings(request)
    return (
        IngestionService(settings),
        ProcessingService(settings),
        WorkerStorage(settings.catalog_db_path),
    )


def _coordinator(request: Request) -> TaskCoordinator:
    settings = _settings(request)
    ingestion, processing, storage = _services(request)
    return TaskCoordinator(settings, ingestion, processing, storage)


def _signed_csrf(secret: str) -> str:
    nonce = secrets.token_urlsafe(24)
    signature = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{signature}"


def _csrf_valid(token: str | None, cookie: str | None, secret: str) -> bool:
    if not token or not cookie or not hmac.compare_digest(token, cookie):
        return False
    try:
        nonce, signature = token.rsplit(".", 1)
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _csrf_token(request: Request) -> tuple[str, bool]:
    settings = _settings(request)
    current = request.cookies.get(_CSRF_COOKIE)
    if current and _csrf_valid(current, current, settings.queryx_ui_secret_key):
        return current, False
    return _signed_csrf(settings.queryx_ui_secret_key), True


def _render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    token, is_new = _csrf_token(request)
    payload = {
        "request": request,
        "csrf_token": token,
        "execution_mode": _settings(request).queryx_execution_mode,
        **(context or {}),
    }
    response = templates.TemplateResponse(request=request, name=name, context=payload, status_code=status_code)
    if is_new:
        response.set_cookie(
            _CSRF_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
    return response


def _error(request: Request, status_code: int, title: str, message: str) -> HTMLResponse:
    return _render(
        request,
        "error.html",
        {"error_status": status_code, "error_title": title, "error_message": message},
        status_code,
    )


def _require_csrf(request: Request, token: str | None) -> HTMLResponse | None:
    if _csrf_valid(token, request.cookies.get(_CSRF_COOKIE), _settings(request).queryx_ui_secret_key):
        return None
    return _error(request, 403, "Richiesta non valida", "Token CSRF mancante o non valido.")


def install_exception_handlers(app: Any) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def ui_http_exception(request: Request, exc: StarletteHTTPException) -> Response:
        if request.url.path.startswith("/ui"):
            message = exc.detail if isinstance(exc.detail, str) else "La risorsa richiesta non è disponibile."
            return _error(request, exc.status_code, f"Errore {exc.status_code}", message)
        return await http_exception_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def ui_validation_exception(request: Request, exc: RequestValidationError) -> Response:
        if request.url.path.startswith("/ui"):
            return _error(request, 422, "Dati non validi", "Controlla i campi inviati e riprova.")
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def ui_unhandled_exception(request: Request, exc: Exception) -> Response:
        if request.url.path.startswith("/ui"):
            return _error(request, 500, "Errore interno", "La pagina non può essere generata in questo momento.")
        raise exc


@router.get("/static/{filename}", include_in_schema=False)
async def ui_static(filename: str) -> Response:
    media_type = _STATIC_FILES.get(filename)
    if media_type is None:
        raise StarletteHTTPException(status_code=404, detail="Static asset non trovato")
    content = (Path(__file__).parent / "static" / filename).read_bytes()
    return Response(content, media_type=media_type, headers={"Cache-Control": "public, max-age=3600"})


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    settings = _settings(request)
    ingestion, processing, work_storage = _services(request)
    checks = ScanOrchestrator.from_settings(settings).health_checks()
    worker = WorkerService(settings).status()
    sources = _source_view_models(settings)
    jobs = [
        job_vm(job, work_storage.latest_for(TaskType.INGESTION, job.id))
        for job in ingestion.list_jobs(8)
    ]
    runs = [
        processing_run_vm(run, work_storage.latest_for(TaskType.PROCESSING, run.id))
        for run in processing.list_runs(8)
    ]
    alerts: list[AlertVM] = []
    if settings.queryx_execution_mode == "worker" and worker["status"] != "online":
        alerts.append(AlertVM("danger", f"Worker {worker['status']}: i lavori asincroni non avanzano."))
    health_ok = bool(checks) and all(bool(value.get("ok")) for value in checks.values())
    return _render(
        request,
        "dashboard.html",
        {
            "title": "Dashboard",
            "health": badge("ok" if health_ok else "degraded"),
            "worker": worker,
            "worker_badge": badge(worker["status"]),
            "assets_count": len(ingestion.list_assets()),
            "jobs": jobs,
            "runs": runs,
            "sources": sources,
            "alerts": alerts,
        },
    )


@router.get("/ingestions/new", response_class=HTMLResponse)
async def new_ingestion(request: Request) -> HTMLResponse:
    return _render(
        request,
        "ingestion/new.html",
        {
            "title": "Importa dataset",
            "max_upload": _settings(request).ingestion_max_upload_bytes,
            "assets": [asset_vm(asset) for asset in IngestionService(_settings(request)).list_assets()],
        },
    )


@router.post("/ingestions", response_class=HTMLResponse)
async def create_ingestion(
    request: Request,
    file: UploadFile = File(...),
    csrf_token: str | None = Form(default=None),
    logical_name: str | None = Form(default=None),
    asset_id: str | None = Form(default=None),
    source_provider: SourceProvider = Form(default=SourceProvider.MANUAL),
    source_reference: str | None = Form(default=None, max_length=512),
    source_version: str | None = Form(default=None, max_length=128),
    dataset_title: str | None = Form(default=None, max_length=256),
    license_name: str | None = Form(default=None, max_length=128),
    provenance_notes: str | None = Form(default=None, max_length=1000),
) -> Response:
    invalid = _require_csrf(request, csrf_token)
    if invalid:
        await file.close()
        return invalid
    try:
        provenance = DatasetProvenance(
            source_provider=source_provider,
            source_reference=source_reference,
            source_version=source_version,
            dataset_title=dataset_title,
            license_name=license_name,
            notes=provenance_notes,
        )
        result = await _coordinator(request).submit_ingestion(
            file,
            asset_id=asset_id or None,
            logical_name=logical_name,
            provenance=provenance,
        )
        return RedirectResponse(f"/ui/ingestions/{result.job_id}", status_code=303)
    except ValidationError:
        await file.close()
        return _error(request, 422, "Provenienza non valida", "Controlla i metadata di provenienza.")
    except IngestionServiceError as exc:
        return _error(request, exc.status_code, "Importazione non accettata", exc.message)


@router.get("/ingestions/{job_id}", response_class=HTMLResponse)
async def ingestion_detail(request: Request, job_id: str) -> HTMLResponse:
    ingestion, _, storage = _services(request)
    job = ingestion.get_job(job_id)
    if job is None:
        return _error(request, 404, "Job non trovato", "Il job di ingestion richiesto non esiste.")
    preview = None
    preview_alert = None
    if str(job.status) == "ready":
        try:
            payload = ingestion.get_preview(job.id)
            if payload:
                preview = preview_vm(payload, "raw", _settings(request).queryx_ui_max_preview_columns)
        except IngestionServiceError as exc:
            preview_alert = AlertVM("warning", exc.message)
    return _render(
        request,
        "ingestion/job.html",
        {
            "title": "Ingestion job",
            "job": job_vm(job, storage.latest_for(TaskType.INGESTION, job.id)),
            "preview": preview,
            "preview_alert": preview_alert,
        },
    )


@router.get("/ingestions/{job_id}/status", response_class=HTMLResponse)
async def ingestion_status(request: Request, job_id: str) -> HTMLResponse:
    ingestion, _, storage = _services(request)
    job = ingestion.get_job(job_id)
    if job is None:
        return _error(request, 404, "Job non trovato", "Il job richiesto non esiste.")
    return _render(
        request,
        "components/job_status.html",
        {"job": job_vm(job, storage.latest_for(TaskType.INGESTION, job.id)), "fragment": True},
    )


@router.post("/ingestions/{job_id}/cancel", response_class=HTMLResponse)
async def cancel_ingestion_ui(
    request: Request,
    job_id: str,
    csrf_token: str | None = Form(default=None),
) -> Response:
    invalid = _require_csrf(request, csrf_token)
    if invalid:
        return invalid
    try:
        _coordinator(request).cancel_ingestion(job_id)
        return RedirectResponse(f"/ui/ingestions/{job_id}", status_code=303)
    except (IngestionServiceError, WorkItemConflictError) as exc:
        return _error(request, getattr(exc, "status_code", 409), "Cancellazione non disponibile", str(exc))


@router.get("/assets", response_class=HTMLResponse)
async def assets_list(request: Request) -> HTMLResponse:
    assets = [asset_vm(asset) for asset in IngestionService(_settings(request)).list_assets()]
    return _render(request, "assets/list.html", {"title": "Asset", "assets": assets})


@router.get("/assets/{asset_id}", response_class=HTMLResponse)
async def asset_detail(request: Request, asset_id: str) -> HTMLResponse:
    ingestion = IngestionService(_settings(request))
    asset = ingestion.get_asset(asset_id)
    versions = ingestion.list_versions(asset_id)
    if asset is None or versions is None:
        return _error(request, 404, "Asset non trovato", "L'asset richiesto non esiste.")
    return _render(
        request,
        "assets/detail.html",
        {
            "title": asset.name,
            "asset": asset_vm(asset),
            "versions": [version_vm(version) for version in versions],
        },
    )


@router.get("/assets/{asset_id}/versions/{version_id}", response_class=HTMLResponse)
async def version_detail(request: Request, asset_id: str, version_id: str) -> HTMLResponse:
    settings = _settings(request)
    ingestion, processing, storage = _services(request)
    version = ingestion.get_version(asset_id, version_id)
    if version is None:
        return _error(request, 404, "Versione non trovata", "La versione richiesta non esiste.")
    inspection = ingestion.get_version_inspection(version_id)
    bindings = processing.list_bindings(asset_id, version_id) or []
    runs = processing.list_runs_for_version(version_id)
    raw_preview = None
    serving_preview = None
    alerts: list[AlertVM] = []
    job = next((item for item in ingestion.list_jobs(100) if item.asset_version_id == version_id), None)
    if job is not None:
        try:
            payload = ingestion.get_preview(job.id)
            if payload:
                raw_preview = preview_vm(payload, "raw", settings.queryx_ui_max_preview_columns)
        except IngestionServiceError as exc:
            alerts.append(AlertVM("warning", exc.message))
    try:
        serving_payload = processing.data_preview(asset_id, version_id, settings.processing_preview_rows)
        serving_preview = preview_vm(serving_payload, "DuckDB", settings.queryx_ui_max_preview_columns)
    except ProcessingServiceError as exc:
        if exc.code not in {"serving_binding_missing", "duckdb_view_missing"}:
            alerts.append(AlertVM("warning", exc.message))
    latest_run = runs[0] if runs else None
    binding_models = [binding_vm(binding) for binding in bindings]
    bindings_by_role = {
        role: [binding for binding in binding_models if binding.role == role]
        for role in ("raw", "normalized", "serving")
    }
    return _render(
        request,
        "assets/version.html",
        {
            "title": f"Versione {version.version_number}",
            "version": version_vm(version),
            "observed_schema": schema_vm(
                [field.model_dump(mode="json") for field in inspection.fields] if inspection else []
            ),
            "canonical_schema": schema_vm(latest_run.canonical_schema if latest_run else []),
            "serving_schema": schema_vm(latest_run.serving_schema if latest_run else []),
            "bindings_by_role": bindings_by_role,
            "runs": [
                processing_run_vm(run, storage.latest_for(TaskType.PROCESSING, run.id)) for run in runs
            ],
            "raw_preview": raw_preview,
            "serving_preview": serving_preview,
            "alerts": alerts,
            "can_prepare": str(version.status) == "ready",
        },
    )


@router.post("/assets/{asset_id}/versions/{version_id}/prepare", response_class=HTMLResponse)
async def prepare_version_ui(
    request: Request,
    asset_id: str,
    version_id: str,
    csrf_token: str | None = Form(default=None),
) -> Response:
    invalid = _require_csrf(request, csrf_token)
    if invalid:
        return invalid
    try:
        submission = _coordinator(request).submit_processing(asset_id, version_id)
        return RedirectResponse(f"/ui/processing/runs/{submission.run.id}", status_code=303)
    except ProcessingServiceError as exc:
        return _error(request, exc.status_code, "Preparazione non disponibile", exc.message)


@router.get("/processing/runs/{run_id}", response_class=HTMLResponse)
async def processing_run_detail(request: Request, run_id: str) -> HTMLResponse:
    _, processing, storage = _services(request)
    run = processing.get_run(run_id)
    if run is None:
        return _error(request, 404, "Run non trovato", "Il ProcessingRun richiesto non esiste.")
    return _render(
        request,
        "processing/run.html",
        {
            "title": "Processing run",
            "run": processing_run_vm(run, storage.latest_for(TaskType.PROCESSING, run.id)),
            "canonical_schema": schema_vm(run.canonical_schema),
            "serving_schema": schema_vm(run.serving_schema),
        },
    )


@router.get("/processing/runs/{run_id}/status", response_class=HTMLResponse)
async def processing_run_status(request: Request, run_id: str) -> HTMLResponse:
    _, processing, storage = _services(request)
    run = processing.get_run(run_id)
    if run is None:
        return _error(request, 404, "Run non trovato", "Il ProcessingRun richiesto non esiste.")
    return _render(
        request,
        "components/job_status.html",
        {"run": processing_run_vm(run, storage.latest_for(TaskType.PROCESSING, run.id)), "fragment": True},
    )


@router.post("/processing/runs/{run_id}/cancel", response_class=HTMLResponse)
async def cancel_processing_ui(
    request: Request,
    run_id: str,
    csrf_token: str | None = Form(default=None),
) -> Response:
    invalid = _require_csrf(request, csrf_token)
    if invalid:
        return invalid
    try:
        _coordinator(request).cancel_processing(run_id)
        return RedirectResponse(f"/ui/processing/runs/{run_id}", status_code=303)
    except (ProcessingServiceError, WorkItemConflictError) as exc:
        return _error(request, getattr(exc, "status_code", 409), "Cancellazione non disponibile", str(exc))


@router.get("/sources", response_class=HTMLResponse)
async def sources_list(request: Request) -> HTMLResponse:
    return _render(
        request,
        "sources/list.html",
        {"title": "Sources", "sources": _source_view_models(_settings(request))},
    )


@router.get("/sources/{source_id}", response_class=HTMLResponse)
async def source_detail(request: Request, source_id: str) -> HTMLResponse:
    settings = _settings(request)
    registry = SourceRegistry(settings)
    source = registry.get_source(source_id)
    if source is None:
        return _error(request, 404, "Sorgente non trovata", "La sorgente richiesta non esiste.")
    catalog = CatalogService(CatalogStorage(settings.catalog_db_path))
    history = catalog.source_history(source_id)[:10]
    current = {item.source_id: item for item in catalog.current_catalog(registry.list_sources()).sources}
    return _render(
        request,
        "sources/detail.html",
        {
            "title": source.name,
            "source": source_vm(source, current.get(source_id)),
            "history": [scan_vm(item) for item in history],
        },
    )


def _source_view_models(settings: Settings) -> list[Any]:
    registry = SourceRegistry(settings)
    catalog = CatalogService(CatalogStorage(settings.catalog_db_path))
    try:
        current = {item.source_id: item for item in catalog.current_catalog(registry.list_sources()).sources}
    except Exception:
        current = {}
    return [source_vm(source, current.get(source.id)) for source in registry.list_sources()]
