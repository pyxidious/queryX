from __future__ import annotations

from dataclasses import dataclass

from fastapi import UploadFile

from queryx.app.core.config import Settings
from queryx.app.ingestion.models import IngestionJob, UploadResult
from queryx.app.ingestion.service import IngestionService
from queryx.app.processing.models import ProcessingRun
from queryx.app.processing.service import ProcessingService
from queryx.app.worker.models import TaskType, WorkItem, WorkStatus
from queryx.app.worker.storage import WorkerStorage


@dataclass(frozen=True)
class ProcessingSubmission:
    run: ProcessingRun
    work_item: WorkItem | None = None


class TaskCoordinator:
    """Shared application flow used by JSON and server-rendered routes."""

    def __init__(
        self,
        settings: Settings,
        ingestion: IngestionService | None = None,
        processing: ProcessingService | None = None,
        work_storage: WorkerStorage | None = None,
        *,
        initialize_ingestion: bool = True,
        initialize_processing: bool = True,
    ) -> None:
        self.settings = settings
        self.ingestion = ingestion or (IngestionService(settings) if initialize_ingestion else None)
        self.processing = processing or (ProcessingService(settings) if initialize_processing else None)
        self.work_storage = work_storage or WorkerStorage(settings.catalog_db_path)

    async def submit_ingestion(
        self,
        upload: UploadFile,
        asset_id: str | None = None,
        logical_name: str | None = None,
    ) -> UploadResult:
        if self.ingestion is None:  # pragma: no cover - construction invariant
            raise RuntimeError("ingestion service is not configured")
        if self.settings.queryx_execution_mode == "worker":
            return await self.ingestion.accept_upload(
                upload,
                asset_id=asset_id,
                logical_name=logical_name,
                enqueue=True,
            )
        return await self.ingestion.ingest_upload(
            upload,
            asset_id=asset_id,
            logical_name=logical_name,
        )

    def submit_processing(self, asset_id: str, version_id: str) -> ProcessingSubmission:
        if self.processing is None:  # pragma: no cover - construction invariant
            raise RuntimeError("processing service is not configured")
        if self.settings.queryx_execution_mode == "inline":
            return ProcessingSubmission(self.processing.prepare(asset_id, version_id))
        run, _, _ = self.processing.create_processing_run(asset_id, version_id)
        if run.status == "completed":
            return ProcessingSubmission(run)
        item, _ = self.work_storage.enqueue(
            TaskType.PROCESSING,
            run.id,
            self.settings.worker_max_attempts,
        )
        return ProcessingSubmission(run, item)

    def cancel_ingestion(self, job_id: str) -> tuple[IngestionJob | None, WorkItem | None]:
        if self.ingestion is None:  # pragma: no cover - construction invariant
            raise RuntimeError("ingestion service is not configured")
        item = self.work_storage.active_for(TaskType.INGESTION, job_id)
        if item is None:
            return self.ingestion.cancel(job_id), None
        cancelled_item = self.work_storage.request_cancel(item.id)
        job = self.ingestion.get_job(job_id)
        if cancelled_item.status == WorkStatus.CANCELLED:
            job = self.ingestion.cancel(job_id)
        return job, cancelled_item

    def cancel_processing(self, run_id: str) -> tuple[ProcessingRun | None, WorkItem | None]:
        if self.processing is None:  # pragma: no cover - construction invariant
            raise RuntimeError("processing service is not configured")
        item = self.work_storage.active_for(TaskType.PROCESSING, run_id)
        if item is None:
            return self.processing.cancel(run_id), None
        cancelled_item = self.work_storage.request_cancel(item.id)
        run = self.processing.get_run(run_id)
        if cancelled_item.status == WorkStatus.CANCELLED:
            run = self.processing.cancel(run_id)
        return run, cancelled_item
