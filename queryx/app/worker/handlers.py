from __future__ import annotations

import logging
import sqlite3
from typing import Callable, TypeAlias

from queryx.app.acquisition.service import (
    AcquisitionCancelledError,
    AcquisitionService,
    AcquisitionServiceError,
)
from queryx.app.acquisition.models import AcquisitionStatus
from queryx.app.core.config import Settings
from queryx.app.ingestion.models import IngestionStatus
from queryx.app.ingestion.service import (
    IngestionCancelledError,
    IngestionService,
    IngestionServiceError,
)
from queryx.app.processing.models import ProcessingStatus
from queryx.app.processing.service import (
    ProcessingCancelledError,
    ProcessingService,
    ProcessingServiceError,
)
from queryx.app.processing.serving.duckdb import DuckDBLockTimeout
from queryx.app.worker.coordination import ExecutionInterruptedError
from queryx.app.worker.models import TaskType, WorkItem
from queryx.app.worker.storage import LeaseLostError, WorkerStorage


logger = logging.getLogger(__name__)
CancellationError: TypeAlias = (
    type[IngestionCancelledError] | type[ProcessingCancelledError] | type[AcquisitionCancelledError]
)


_PERMANENT_CODES = {
    "asset_not_found",
    "asset_version_not_found",
    "asset_version_not_ready",
    "ingestion_job_not_found",
    "ingestion_not_executable",
    "observed_schema_missing",
    "raw_binding_missing",
    "raw_file_missing",
    "staged_file_missing",
    "unsupported_format",
    "unsafe_filename",
    "empty_file",
    "upload_too_large",
    "strict_conversion_failed",
    "schema_mismatch",
    "recipe_mismatch",
}


class WorkItemHandler:
    def __init__(
        self,
        settings: Settings,
        storage: WorkerStorage,
        worker_id: str,
        ingestion: IngestionService | None = None,
        processing: ProcessingService | None = None,
        acquisition: AcquisitionService | None = None,
        shutdown_expired: Callable[[], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.worker_id = worker_id
        self.ingestion = ingestion or IngestionService(settings)
        self.processing = processing or ProcessingService(settings)
        self.acquisition = acquisition or AcquisitionService(
            settings, ingestion=self.ingestion, work_storage=storage
        )
        self.shutdown_expired = shutdown_expired or (lambda: False)

    def handle(self, item: WorkItem) -> WorkItem:
        try:
            if item.task_type == TaskType.INGESTION:
                return self._ingestion(item)
            if item.task_type == TaskType.PROCESSING:
                return self._processing(item)
            return self._acquisition(item)
        except (LeaseLostError, ExecutionInterruptedError):
            logger.warning("work_item_lease_lost item_id=%s task_type=%s", item.id, item.task_type)
            current = self.storage.get(item.id)
            return current or item
        except (sqlite3.OperationalError, OSError, DuckDBLockTimeout) as exc:
            return self._retry(item, "transient_storage_error", _safe_message(exc))
        except (IngestionServiceError, ProcessingServiceError, AcquisitionServiceError) as exc:
            code = exc.code
            if isinstance(exc, AcquisitionServiceError) and exc.transient:
                return self._retry(item, code, exc.message)
            if code in _PERMANENT_CODES or exc.status_code < 500:
                return self._fail(item, code, exc.message)
            return self._retry(item, code, exc.message)
        except Exception as exc:
            logger.exception("work_item_unexpected_failure item_id=%s task_type=%s", item.id, item.task_type)
            return self._fail(item, "unexpected_error", _safe_message(exc))

    def _ingestion(self, item: WorkItem) -> WorkItem:
        try:
            result = self.ingestion.execute_ingestion_job(
                item.aggregate_id,
                checkpoint=lambda: self._checkpoint(item, IngestionCancelledError),
                allow_retry=True,
            )
        except IngestionCancelledError:
            current = self.ingestion.get_job(item.aggregate_id)
            if current and current.status not in {IngestionStatus.CANCELLED, IngestionStatus.READY}:
                self.ingestion.cancel(item.aggregate_id)
            return self.storage.cancel_owned(item.id, self.worker_id)
        if result.status != IngestionStatus.READY:
            return self._fail(item, "incoherent_ingestion_state", "Ingestion did not reach ready")
        return self.storage.complete(item.id, self.worker_id)

    def _processing(self, item: WorkItem) -> WorkItem:
        try:
            run = self.processing.execute_processing_run(
                item.aggregate_id,
                checkpoint=lambda: self._checkpoint(item, ProcessingCancelledError),
            )
        except ProcessingCancelledError:
            current = self.processing.get_run(item.aggregate_id)
            if current and current.status not in {ProcessingStatus.CANCELLED, ProcessingStatus.COMPLETED}:
                self.processing.cancel(item.aggregate_id)
            return self.storage.cancel_owned(item.id, self.worker_id)
        if run.status == ProcessingStatus.COMPLETED:
            return self.storage.complete(item.id, self.worker_id)
        if run.status == ProcessingStatus.PARTIAL:
            return self._retry(item, "duckdb_registration_failed", "Serving registration can be retried")
        if run.status == ProcessingStatus.CANCELLED:
            return self.storage.cancel_owned(item.id, self.worker_id)
        return self._fail(item, "processing_failed", "Processing did not produce usable outputs")

    def _acquisition(self, item: WorkItem) -> WorkItem:
        try:
            if item.task_type == TaskType.KAGGLE_INSPECT:
                self.acquisition.execute_inspection(
                    item.aggregate_id,
                    checkpoint=lambda: self._checkpoint(item, AcquisitionCancelledError),
                )
            else:
                self.acquisition.execute_download(
                    item.aggregate_id,
                    checkpoint=lambda: self._checkpoint(item, AcquisitionCancelledError),
                )
        except AcquisitionCancelledError:
            current = self.acquisition.storage.get_run(item.aggregate_id)
            if current and current.status not in {
                AcquisitionStatus.COMPLETED,
                AcquisitionStatus.PARTIAL,
                AcquisitionStatus.FAILED,
                AcquisitionStatus.CANCELLED,
            }:
                self.acquisition.cancel(item.aggregate_id)
            return self.storage.cancel_owned(item.id, self.worker_id)
        return self.storage.complete(item.id, self.worker_id)

    def _checkpoint(self, item: WorkItem, cancellation_error: CancellationError) -> None:
        if self.shutdown_expired():
            raise ExecutionInterruptedError("Worker shutdown deadline reached")
        current = self.storage.get(item.id)
        if current is None or current.claimed_by != self.worker_id or current.status != "leased":
            raise LeaseLostError("Work item lease is no longer owned")
        if current.cancellation_requested:
            raise cancellation_error("Cancellation requested")
        self.storage.heartbeat(item.id, self.worker_id, self.settings.worker_lease_seconds)
        self.storage.touch_worker(self.worker_id)

    def _retry(self, item: WorkItem, code: str, message: str) -> WorkItem:
        result = self.storage.retry(
            item.id,
            self.worker_id,
            {"code": code, "message": message},
            self.settings.worker_retry_base_seconds,
        )
        if result.status == "failed":
            self._fail_aggregate(item, "max_attempts_exceeded", "Work item exhausted its retry budget")
        return result

    def _fail(self, item: WorkItem, code: str, message: str) -> WorkItem:
        result = self.storage.fail(
            item.id,
            self.worker_id,
            {"code": code, "message": message},
        )
        self._fail_aggregate(item, code, message)
        return result

    def _fail_aggregate(self, item: WorkItem, code: str, message: str) -> None:
        if item.task_type == TaskType.INGESTION:
            fail = getattr(self.ingestion, "fail_execution", None)
            if fail is not None:
                fail(item.aggregate_id, code, message)
        elif item.task_type == TaskType.PROCESSING:
            self.processing.fail_execution(item.aggregate_id, code, message)
        else:
            self.acquisition.fail_execution(item.aggregate_id, code, message)


def _safe_message(exc: Exception) -> str:
    if isinstance(exc, OSError):
        return "A temporary filesystem operation failed"
    if isinstance(exc, sqlite3.OperationalError):
        return "SQLite is temporarily unavailable"
    if isinstance(exc, DuckDBLockTimeout):
        return "DuckDB lock timed out"
    return "Work item execution failed"
