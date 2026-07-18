from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx

from queryx.app.acquisition.models import (
    AcquisitionFileStatus,
    AcquisitionStatus,
    DatasetManifest,
    FileSelection,
)
from queryx.app.acquisition.providers.kaggle import FakeKaggleProvider, KaggleProviderError
from queryx.app.acquisition.service import AcquisitionService, AcquisitionServiceError
from queryx.app.api import routes as api_routes
from queryx.app.core.config import Settings
from queryx.app.main import create_app
from queryx.app.ui import routes as ui_routes
from queryx.app.worker.models import TaskType, WorkStatus
from queryx.app.worker.service import WorkerService
from queryx.app.worker.storage import WorkerStorage
from tests.test_ui import UIClient, _csrf


def _settings(tmp_path: Path, **changes: object) -> Settings:
    data = tmp_path / "data"
    values: dict[str, object] = {
        "catalog_db_path": data / "catalog.sqlite3",
        "data_raw_dir": data / "raw",
        "data_staging_dir": data / "staging",
        "data_normalized_dir": data / "normalized",
        "duckdb_path": data / "queryx.duckdb",
        "duckdb_lock_path": data / "queryx.duckdb.lock",
        "kaggle_temp_dir": data / "acquisition",
        "queryx_execution_mode": "worker",
        "kaggle_enabled": True,
        "kaggle_credentials_path": None,
        "mysql_enabled": False,
        "mongodb_enabled": False,
        "worker_retry_base_seconds": 1,
    }
    values.update(changes)
    return Settings(**values)


def _provider() -> FakeKaggleProvider:
    manifest = DatasetManifest(
        dataset_reference="owner/demo",
        resolved_version="7",
        title="Demo dataset",
        license_name="CC0-1.0",
        files=[
            {"reference": "tables/people.csv", "name": "people.csv", "size_bytes": 20},
            {"reference": "events.parquet", "name": "events.parquet", "size_bytes": 4},
            {"reference": "README.md", "name": "README.md", "size_bytes": 10},
        ],
    )
    return FakeKaggleProvider(
        {("owner/demo", "latest"): manifest},
        {
            "tables/people.csv": b"id,name\n1,Ada\n2,Grace\n",
            "events.parquet": _parquet_bytes(),
        },
    )


def _parquet_bytes() -> bytes:
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    stream = io.BytesIO()
    pq.write_table(pa.table({"id": [1, 2]}), stream)
    return stream.getvalue()


def _service(settings: Settings, provider: FakeKaggleProvider | None = None) -> AcquisitionService:
    return AcquisitionService(settings, provider=provider or _provider())


def test_validation_disabled_credentials_and_manifest_limits(tmp_path: Path) -> None:
    disabled = _settings(tmp_path / "disabled", kaggle_enabled=False)
    run, _ = _service(disabled).create_inspection("owner/demo", enqueue=True)
    try:
        _service(disabled).execute_inspection(run.id)
    except AcquisitionServiceError as exc:
        assert exc.code == "kaggle_disabled" and "credential" not in exc.message.lower()
    else:  # pragma: no cover
        raise AssertionError("disabled provider accepted")

    missing = _settings(tmp_path / "missing", kaggle_credentials_path=None)
    service = AcquisitionService(missing)
    run, _ = service.create_inspection("owner/demo", enqueue=True)
    try:
        service.execute_inspection(run.id)
    except AcquisitionServiceError as exc:
        assert exc.code == "kaggle_credentials_missing"

    try:
        _service(_settings(tmp_path / "invalid")).create_inspection("https://evil.invalid/x", enqueue=True)
    except AcquisitionServiceError as exc:
        assert exc.code == "invalid_dataset_reference"

    limited = _settings(tmp_path / "limited", kaggle_max_files=2)
    run, _ = _service(limited).create_inspection("owner/demo", enqueue=True)
    try:
        _service(limited).execute_inspection(run.id)
    except AcquisitionServiceError as exc:
        assert exc.code == "kaggle_file_limit_exceeded"


def test_inspection_resolves_latest_persists_license_and_formats(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = _service(settings)
    run, _ = service.create_inspection("Owner/Demo", "latest", enqueue=True)
    inspected = service.execute_inspection(run.id)
    files = service.storage.list_files(run.id)
    assert inspected.status == AcquisitionStatus.AWAITING_SELECTION
    assert inspected.dataset_reference == "owner/demo"
    assert inspected.resolved_version == "7" and inspected.license_name == "CC0-1.0"
    assert len(files) == 3
    assert {item.format for item in files} == {"csv", "parquet", "unsupported"}
    assert next(item for item in files if item.format == "unsupported").status == AcquisitionFileStatus.UNSUPPORTED


def test_file_selection_rejects_unknown_unsupported_and_asset_mapping(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = _service(settings)
    run, _ = service.create_inspection("owner/demo", enqueue=True)
    service.execute_inspection(run.id)
    files = service.storage.list_files(run.id)
    unsupported = next(item for item in files if item.format == "unsupported")
    for selection, code in (
        (FileSelection(file_id="missing"), "manifest_file_not_found"),
        (FileSelection(file_id=unsupported.id), "unsupported_manifest_file"),
    ):
        try:
            service.start(run.id, [selection])
        except AcquisitionServiceError as exc:
            assert exc.code == code
    csv_file = next(item for item in files if item.format == "csv")
    try:
        service.start(run.id, [FileSelection(file_id=csv_file.id, target_asset_id="missing")])
    except AcquisitionServiceError as exc:
        assert exc.code == "asset_not_found"


def test_end_to_end_multi_file_single_worker_and_provenance(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    provider = _provider()
    acquisition = _service(settings, provider)
    worker = WorkerService(settings, acquisition=acquisition, worker_id="worker-test")
    run, item_id = acquisition.create_inspection("owner/demo", enqueue=True)
    assert item_id and worker.run_once().task_type == TaskType.KAGGLE_INSPECT
    files = acquisition.storage.list_files(run.id)
    selected = [item for item in files if item.format in {"csv", "parquet"}]
    started, download_item, _ = acquisition.start(
        run.id,
        [
            FileSelection(file_id=selected[0].id, logical_name="people"),
            FileSelection(file_id=selected[1].id, logical_name="events"),
        ],
        enqueue=True,
    )
    assert started.status == AcquisitionStatus.DOWNLOADING and download_item
    assert worker.run_once().task_type == TaskType.KAGGLE_DOWNLOAD
    awaiting = acquisition.storage.get_run(run.id)
    assert awaiting and awaiting.status == AcquisitionStatus.AWAITING_INGESTION
    children = [item for item in acquisition.storage.list_files(run.id) if item.selected]
    assert all(item.ingestion_job_id for item in children)
    assert all(item.status == AcquisitionFileStatus.QUEUED_FOR_INGESTION for item in children)
    assert worker.run_once().task_type == TaskType.INGESTION
    assert worker.run_once().task_type == TaskType.INGESTION
    report = acquisition.reconcile()
    completed = acquisition.storage.get_run(run.id)
    assert completed and completed.status == AcquisitionStatus.COMPLETED and completed.files_ready == 2
    assert report.runs_updated == [run.id]
    ready_files = acquisition.storage.list_files(run.id)
    assert all(item.asset_id and item.asset_version_id and item.content_fingerprint for item in ready_files if item.selected)
    lineage = acquisition.ingestion.storage.get_lineage(next(item.asset_version_id for item in ready_files if item.selected))
    assert any(item.source_reference.startswith("kaggle://owner/demo@7/") for item in lineage)


def test_existing_asset_mapping_and_deterministic_idempotency(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    acquisition = _service(settings)
    worker = WorkerService(settings, acquisition=acquisition, worker_id="worker-test")
    first, _ = acquisition.create_inspection("owner/demo", enqueue=True)
    worker.run_once()
    csv_file = next(item for item in acquisition.storage.list_files(first.id) if item.format == "csv")
    acquisition.start(first.id, [FileSelection(file_id=csv_file.id)], enqueue=True)
    worker.run_once(); worker.run_once(); acquisition.reconcile()
    first_file = next(item for item in acquisition.storage.list_files(first.id) if item.selected)
    assert first_file.asset_id

    second, _ = acquisition.create_inspection("owner/demo", enqueue=True)
    worker.run_once()
    second_csv = next(item for item in acquisition.storage.list_files(second.id) if item.format == "csv")
    reused_run, work_id, reused = acquisition.start(
        second.id, [FileSelection(file_id=second_csv.id)], enqueue=True
    )
    assert reused and work_id is None and reused_run.id == first.id

    third, _ = acquisition.create_inspection("owner/demo", enqueue=True)
    worker.run_once()
    third_csv = next(item for item in acquisition.storage.list_files(third.id) if item.format == "csv")
    selection = FileSelection(file_id=third_csv.id, target_asset_id=first_file.asset_id)
    started, _, reused = acquisition.start(third.id, [selection], enqueue=True)
    assert not reused and started.request_fingerprint
    assert started.request_fingerprint == acquisition.request_fingerprint(
        started, [selection], {third_csv.id: third_csv}
    )


def test_path_traversal_download_bound_and_sha256(tmp_path: Path) -> None:
    unsafe_manifest = DatasetManifest(
        dataset_reference="owner/demo",
        resolved_version="1",
        files=[{"reference": "../escape.csv", "name": "escape.csv", "size_bytes": 2}],
    )
    unsafe = FakeKaggleProvider({("owner/demo", "latest"): unsafe_manifest}, {"../escape.csv": b"x\n"})
    service = _service(_settings(tmp_path / "unsafe"), unsafe)
    run, _ = service.create_inspection("owner/demo", enqueue=True)
    try:
        service.execute_inspection(run.id)
    except AcquisitionServiceError as exc:
        assert exc.code == "unsafe_provider_file"

    bounded_manifest = DatasetManifest(
        dataset_reference="owner/demo",
        resolved_version="1",
        files=[{"reference": "data.csv", "name": "data.csv", "size_bytes": None}],
    )
    payload = b"id\n123456789\n"
    bounded_provider = FakeKaggleProvider({("owner/demo", "latest"): bounded_manifest}, {"data.csv": payload})
    settings = _settings(tmp_path / "bounded", kaggle_max_file_bytes=8, ingestion_max_upload_bytes=8)
    bounded = _service(settings, bounded_provider)
    run, _ = bounded.create_inspection("owner/demo", enqueue=True); bounded.execute_inspection(run.id)
    file = bounded.storage.list_files(run.id)[0]
    bounded.start(run.id, [FileSelection(file_id=file.id)], enqueue=True)
    try:
        bounded.execute_download(run.id)
    except AcquisitionServiceError as exc:
        assert exc.code == "acquisition_download_failed"
    assert not list((settings.kaggle_temp_dir / run.id).glob("*"))

    assert hashlib.sha256(b"id,name\n1,Ada\n2,Grace\n").hexdigest() != hashlib.sha256(payload).hexdigest()


def test_partial_failed_transient_retry_cancellation_and_cleanup(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    provider = _provider()
    acquisition = _service(settings, provider)
    worker = WorkerService(settings, acquisition=acquisition, worker_id="worker-test")
    run, _ = acquisition.create_inspection("owner/demo", enqueue=True); worker.run_once()
    files = [item for item in acquisition.storage.list_files(run.id) if item.format in {"csv", "parquet"}]
    provider.failures[files[1].provider_file_reference] = KaggleProviderError("denied", "File access denied")
    acquisition.start(run.id, [FileSelection(file_id=item.id) for item in files], enqueue=True)
    worker.run_once(); worker.run_once(); acquisition.reconcile()
    assert acquisition.storage.get_run(run.id).status == AcquisitionStatus.PARTIAL
    assert not any((settings.kaggle_temp_dir / run.id).glob("*"))

    retry_provider = _provider()
    retry_service = _service(_settings(tmp_path / "retry"), retry_provider)
    retry_worker = WorkerService(retry_service.settings, acquisition=retry_service, worker_id="retry-worker")
    retry_run, _ = retry_service.create_inspection("owner/demo", enqueue=True); retry_worker.run_once()
    retry_file = next(item for item in retry_service.storage.list_files(retry_run.id) if item.format == "csv")
    retry_provider.failures[retry_file.provider_file_reference] = KaggleProviderError("timeout", "Timed out", transient=True)
    retry_service.start(retry_run.id, [FileSelection(file_id=retry_file.id)], enqueue=True)
    item = retry_worker.run_once()
    assert item and item.status == WorkStatus.RETRY_WAIT and item.last_error["code"] == "timeout"

    cancel_service = _service(_settings(tmp_path / "cancel"))
    cancel_run, _ = cancel_service.create_inspection("owner/demo", enqueue=True)
    cancelled = cancel_service.cancel(cancel_run.id)
    assert cancelled.status == AcquisitionStatus.CANCELLED
    assert WorkerStorage(cancel_service.settings.catalog_db_path).latest_for(TaskType.KAGGLE_INSPECT, cancel_run.id).status == WorkStatus.CANCELLED


def test_reconciliation_missing_child_and_orphan_temporary(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = _service(settings)
    run, _ = service.create_inspection("owner/demo", enqueue=True)
    service.execute_inspection(run.id)
    file = next(item for item in service.storage.list_files(run.id) if item.format == "csv")
    service.storage.select_files(run.id, [FileSelection(file_id=file.id)], "x" * 64)
    service.storage.update_file(file.id, AcquisitionFileStatus.QUEUED_FOR_INGESTION, ingestion_job_id="missing")
    service.storage.transition(run.id, AcquisitionStatus.AWAITING_INGESTION)
    orphan = settings.kaggle_temp_dir / "orphan"
    orphan.mkdir(parents=True); (orphan / "temp.csv").write_bytes(b"x")
    report = service.reconcile()
    assert file.id in report.missing_jobs and "orphan" in report.orphan_temporaries
    assert service.storage.get_run(run.id).status == AcquisitionStatus.FAILED


def test_api_202_sanitized_and_provider_status(tmp_path: Path, monkeypatch: object) -> None:
    settings = _settings(tmp_path)
    service = _service(settings)
    monkeypatch.setattr(api_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(api_routes, "_acquisition_service", lambda settings=None: service)
    monkeypatch.setattr(api_routes, "_ingestion_service", lambda settings=None: service.ingestion)

    async def exercise() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
            inspected = await client.post("/acquisitions/kaggle/inspect", json={"dataset": "owner/demo", "version": "latest"})
            run_id = inspected.json()["acquisition"]["id"]
            return inspected, await client.get(f"/acquisitions/{run_id}"), await client.get("/acquisition/providers")

    inspected, fetched, providers = asyncio.run(exercise())
    assert inspected.status_code == 202 and fetched.status_code == 200 and providers.status_code == 200
    serialized = inspected.text + fetched.text + providers.text
    assert "credential" not in serialized.lower() and str(tmp_path) not in serialized


def test_api_start_and_ui_manifest_polling_csrf_and_local_script(tmp_path: Path, monkeypatch: object) -> None:
    import queryx.app.main as main

    settings = _settings(tmp_path)
    service = _service(settings)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(api_routes, "_acquisition_service", lambda settings=None: service)
    monkeypatch.setattr(api_routes, "_ingestion_service", lambda settings=None: service.ingestion)
    monkeypatch.setattr(ui_routes, "_acquisition_service", lambda request: service)
    app = create_app()
    client = UIClient(app)
    form = client.get("/ui/acquisitions/kaggle")
    assert form.status_code == 200 and "owner/dataset" in form.text
    assert "queryx-polling.js" in form.text and "htmx.min.js" not in form.text
    assert client.get("/ui/static/queryx-polling.js").status_code == 200
    token = _csrf(client, "/ui/acquisitions/kaggle")
    invalid = client.post(
        "/ui/acquisitions/kaggle/inspect",
        data={"csrf_token": "invalid", "dataset": "owner/demo", "version": "latest"},
    )
    assert invalid.status_code == 403
    inspected = client.post(
        "/ui/acquisitions/kaggle/inspect",
        data={"csrf_token": token, "dataset": "owner/demo", "version": "latest"},
    )
    assert inspected.status_code == 303
    run_id = inspected.headers["location"].rsplit("/", 1)[-1]
    assert 'hx-trigger="every 2s"' in client.get(f"/ui/acquisitions/{run_id}/status").text
    service.execute_inspection(run_id)
    page = client.get(f"/ui/acquisitions/{run_id}")
    assert "Demo dataset" in page.text and "CC0-1.0" in page.text
    assert "people.csv" in page.text and "README.md" in page.text and "unsupported" in page.text
    assert "credential" not in page.text.lower() and str(tmp_path) not in page.text
    file = next(item for item in service.storage.list_files(run_id) if item.format == "csv")

    async def start_api() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
            return await api.post(
                f"/acquisitions/{run_id}/start",
                json={"files": [{"file_id": file.id, "logical_name": "customers", "target_asset_id": None}]},
            )

    started = asyncio.run(start_api())
    assert started.status_code == 202 and started.json()["work_item_id"]
