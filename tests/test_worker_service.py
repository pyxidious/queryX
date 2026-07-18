from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.datastructures import UploadFile

from queryx.app.core.config import Settings
from queryx.app.ingestion.models import IngestionStatus
from queryx.app.ingestion.service import IngestionService, IngestionServiceError
from queryx.app.processing.models import ProcessingStatus
from queryx.app.processing.service import ProcessingService
from queryx.app.worker.handlers import WorkItemHandler
from queryx.app.worker.models import TaskType, WorkStatus
from queryx.app.worker.service import WorkerService
from queryx.app.worker.storage import WorkerStorage


def _settings(tmp_path: Path, **updates: object) -> Settings:
    values: dict[str, object] = {
        "catalog_db_path": tmp_path / "data" / "catalog.sqlite3",
        "data_raw_dir": tmp_path / "data" / "raw",
        "data_staging_dir": tmp_path / "data" / "staging",
        "data_normalized_dir": tmp_path / "data" / "normalized",
        "duckdb_path": tmp_path / "data" / "queryx.duckdb",
        "duckdb_lock_path": tmp_path / "data" / "queryx.duckdb.lock",
        "queryx_execution_mode": "worker",
        "worker_lease_seconds": 10,
        "worker_retry_base_seconds": 1,
        "worker_max_attempts": 2,
        "ingestion_stale_job_seconds": 300,
        "processing_stale_run_seconds": 300,
        "mysql_enabled": False,
        "mongodb_enabled": False,
    }
    values.update(updates)
    return Settings(**values)


def _upload(filename: str = "data.csv", content: bytes = b"id\n1\n") -> UploadFile:
    stream = tempfile.SpooledTemporaryFile()
    stream.write(content)
    stream.seek(0)
    return UploadFile(stream, filename=filename)


def test_worker_executes_ingestion_then_processing_and_survives_restart(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ingestion = IngestionService(settings)
    accepted = asyncio.run(ingestion.accept_upload(_upload(), enqueue=True))
    worker = WorkerService(settings, worker_id="worker-a")

    ingestion_item = worker.run_once()
    job = ingestion.get_job(accepted.job_id)
    assert ingestion_item is not None and ingestion_item.status == WorkStatus.COMPLETED
    assert job is not None and job.status == IngestionStatus.READY

    processing = ProcessingService(settings)
    run, mode, _ = processing.create_processing_run(job.asset_id or "", job.asset_version_id or "")
    assert mode == "new"
    WorkerStorage(settings.catalog_db_path).enqueue(TaskType.PROCESSING, run.id, 2)
    restarted = WorkerService(settings, worker_id="worker-b")
    processing_item = restarted.run_once()

    assert processing_item is not None and processing_item.status == WorkStatus.COMPLETED
    assert processing.get_run(run.id).status == ProcessingStatus.COMPLETED  # type: ignore[union-attr]


def test_permanent_error_does_not_retry_and_transient_error_does(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    storage = WorkerStorage(settings.catalog_db_path)

    class PermanentIngestion:
        def execute_ingestion_job(self, *args: object, **kwargs: object) -> object:
            raise IngestionServiceError("unsupported_format", "unsupported", 415)

    permanent, _ = storage.enqueue(TaskType.INGESTION, "permanent", 3)
    claimed = storage.claim("worker", 10)
    assert claimed is not None
    handler = WorkItemHandler(settings, storage, "worker", ingestion=PermanentIngestion())  # type: ignore[arg-type]
    assert handler.handle(claimed).status == WorkStatus.FAILED

    class TransientIngestion:
        def execute_ingestion_job(self, *args: object, **kwargs: object) -> object:
            raise OSError("private filesystem detail")

    transient, _ = storage.enqueue(TaskType.INGESTION, "transient", 3)
    claimed_transient = storage.claim("worker", 10)
    assert claimed_transient is not None and claimed_transient.id == transient.id
    handler = WorkItemHandler(settings, storage, "worker", ingestion=TransientIngestion())  # type: ignore[arg-type]
    result = handler.handle(claimed_transient)
    assert result.status == WorkStatus.RETRY_WAIT
    assert result.last_error == {
        "code": "transient_storage_error",
        "message": "A temporary filesystem operation failed",
    }


def test_reconciliation_completes_leased_aggregate_and_recreates_missing_work(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ingestion = IngestionService(settings)
    uploaded = asyncio.run(ingestion.ingest_upload(_upload()))
    storage = WorkerStorage(settings.catalog_db_path)
    lease_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    completed_candidate, _ = storage.enqueue(
        TaskType.INGESTION,
        uploaded.job_id,
        3,
        available_at=lease_start,
    )
    storage.claim("dead-worker", 1, lease_start)

    pending = asyncio.run(ingestion.accept_upload(_upload("next.csv", b"id\n2\n"), enqueue=False))
    worker = WorkerService(settings, storage=storage, ingestion=ingestion, worker_id="worker")
    report = worker.reconcile(lease_start + timedelta(seconds=2))

    assert storage.get(completed_candidate.id).status == WorkStatus.COMPLETED  # type: ignore[union-attr]
    assert completed_candidate.id in report.completed_from_aggregate
    recreated = storage.active_for(TaskType.INGESTION, pending.job_id)
    assert recreated is not None and recreated.id in report.recreated_items


def test_graceful_shutdown_stops_claiming_new_work(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    storage = WorkerStorage(settings.catalog_db_path)
    storage.enqueue(TaskType.INGESTION, "job", 3)
    worker = WorkerService(settings, storage=storage, worker_id="worker")
    worker.request_shutdown()

    assert worker.run_once() is None
    assert storage.counts()["queued"] == 1
