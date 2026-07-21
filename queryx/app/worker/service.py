from __future__ import annotations

import logging
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from queryx.app.core.config import Settings
from queryx.app.ingestion.models import IngestionStatus
from queryx.app.ingestion.service import IngestionService
from queryx.app.processing.models import ProcessingStatus
from queryx.app.processing.service import ProcessingService
from queryx.app.worker.handlers import WorkItemHandler
from queryx.app.worker.models import (
    TaskType,
    WorkItem,
    WorkReconciliationReport,
    WorkStatus,
)
from queryx.app.worker.storage import WorkerStorage


logger = logging.getLogger(__name__)


class WorkerService:
    def __init__(
        self,
        settings: Settings,
        storage: WorkerStorage | None = None,
        ingestion: IngestionService | None = None,
        processing: ProcessingService | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage or WorkerStorage(settings.catalog_db_path)
        self.ingestion = ingestion or IngestionService(settings)
        self.processing = processing or ProcessingService(settings)
        self.worker_id = worker_id or settings.worker_id or _process_worker_id()
        self.handler = WorkItemHandler(
            settings,
            self.storage,
            self.worker_id,
            self.ingestion,
            self.processing,
            shutdown_expired=self._shutdown_expired,
        )
        self._shutdown_requested = False
        self._shutdown_deadline: float | None = None

    def request_shutdown(self) -> None:
        self._shutdown_requested = True
        self._shutdown_deadline = time.monotonic() + self.settings.worker_shutdown_seconds

    def _shutdown_expired(self) -> bool:
        return self._shutdown_deadline is not None and time.monotonic() >= self._shutdown_deadline

    def run_once(self) -> WorkItem | None:
        if self._shutdown_requested:
            return None
        self.storage.touch_worker(self.worker_id)
        item = self.storage.claim(
            self.worker_id,
            self.settings.worker_lease_seconds,
        )
        if item is None:
            return None
        logger.info(
            "work_item_claimed worker_id=%s item_id=%s task_type=%s attempt=%s",
            self.worker_id,
            item.id,
            item.task_type,
            item.attempt_count,
        )
        result = self.handler.handle(item)
        logger.info(
            "work_item_finished worker_id=%s item_id=%s status=%s",
            self.worker_id,
            result.id,
            result.status,
        )
        self.storage.touch_worker(self.worker_id)
        return result

    def run(self) -> None:
        self.reconcile()
        next_reconciliation = time.monotonic() + self.settings.worker_reconcile_seconds
        logger.info("worker_started worker_id=%s", self.worker_id)
        while not self._shutdown_requested:
            self.run_once()
            if time.monotonic() >= next_reconciliation:
                self.reconcile()
                next_reconciliation = time.monotonic() + self.settings.worker_reconcile_seconds
            if not self._shutdown_requested:
                time.sleep(self.settings.worker_poll_seconds)
        logger.info("worker_stopped worker_id=%s", self.worker_id)

    def reconcile(self, now: datetime | None = None) -> WorkReconciliationReport:
        resolved = now or _now()
        ingestion_report = self.ingestion.reconcile(resolved)
        processing_report = self.processing.reconcile(resolved)
        report = self._reconcile_work_items(resolved)
        metrics = {
            "work_items": report.model_dump(mode="json"),
            "ingestion": ingestion_report.model_dump(mode="json"),
            "processing": processing_report.model_dump(mode="json"),
        }
        self.storage.record_reconciliation(self.worker_id, metrics)
        logger.info(
            "worker_reconciliation worker_id=%s expired=%s failed=%s recreated=%s",
            self.worker_id,
            len(report.expired_leases_requeued),
            len(report.exhausted_items) + len(report.missing_aggregates),
            len(report.recreated_items),
        )
        return report

    def status(self, now: datetime | None = None) -> dict[str, object]:
        resolved = now or _now()
        runtime = self.storage.latest_worker()
        state = "offline"
        if runtime.heartbeat_at is not None:
            stale_after = timedelta(seconds=max(self.settings.worker_lease_seconds, self.settings.worker_heartbeat_seconds * 2))
            state = "online" if runtime.heartbeat_at > resolved - stale_after else "stale"
        counts = self.storage.counts()
        return {
            "execution_mode": self.settings.queryx_execution_mode,
            "worker_id": runtime.worker_id,
            "heartbeat_at": runtime.heartbeat_at,
            "status": state,
            "queued": counts[WorkStatus.QUEUED.value],
            "leased": counts[WorkStatus.LEASED.value],
            "retry_wait": counts[WorkStatus.RETRY_WAIT.value],
            "last_reconciliation_at": runtime.reconciliation_at,
        }

    def _reconcile_work_items(self, now: datetime) -> WorkReconciliationReport:
        report = WorkReconciliationReport()
        items = self.storage.list_items()
        for item in items:
            aggregate = self._aggregate_state(item)
            if aggregate == "missing":
                if item.status in {WorkStatus.QUEUED, WorkStatus.LEASED, WorkStatus.RETRY_WAIT}:
                    self.storage.force_status(
                        item.id,
                        WorkStatus.FAILED,
                        {"code": "aggregate_missing", "message": "Domain aggregate does not exist"},
                    )
                    report.missing_aggregates.append(item.id)
                continue
            if aggregate == "completed":
                if item.status != WorkStatus.COMPLETED:
                    self.storage.force_status(item.id, WorkStatus.COMPLETED)
                    report.completed_from_aggregate.append(item.id)
                continue
            if item.status == WorkStatus.COMPLETED:
                self.storage.force_status(
                    item.id,
                    WorkStatus.FAILED,
                    {"code": "aggregate_not_completed", "message": "Completed work has inconsistent domain state"},
                )
                report.inconsistent_completed.append(item.id)
                continue
            if aggregate in {"failed", "cancelled"} and item.status in {
                WorkStatus.QUEUED,
                WorkStatus.LEASED,
                WorkStatus.RETRY_WAIT,
            }:
                target = WorkStatus.CANCELLED if aggregate == "cancelled" else WorkStatus.FAILED
                self.storage.force_status(item.id, target)
                continue
            if (
                item.status == WorkStatus.LEASED
                and item.cancellation_requested
                and item.lease_expires_at is not None
                and item.lease_expires_at <= now
            ):
                self._cancel_aggregate(item)
                self.storage.force_status(item.id, WorkStatus.CANCELLED)
                continue
            if (
                item.status == WorkStatus.LEASED
                and item.lease_expires_at is not None
                and item.lease_expires_at <= now
            ):
                if item.attempt_count >= item.max_attempts:
                    self.storage.force_status(
                        item.id,
                        WorkStatus.FAILED,
                        {"code": "max_attempts_exceeded", "message": "Expired lease exhausted retries"},
                    )
                    report.exhausted_items.append(item.id)
                    self._fail_aggregate(item, "max_attempts_exceeded", "Work item exhausted its retry budget")
                else:
                    self.storage.force_status(item.id, WorkStatus.RETRY_WAIT, available_at=now)
                    report.expired_leases_requeued.append(item.id)

        active_keys: set[tuple[TaskType, str]] = {
            (item.task_type, item.aggregate_id)
            for item in self.storage.list_items(
                (WorkStatus.QUEUED, WorkStatus.LEASED, WorkStatus.RETRY_WAIT)
            )
        }
        ingestion_jobs = self.ingestion.storage.list_jobs_in_statuses(
            (IngestionStatus.ACQUIRING, IngestionStatus.INSPECTING)
        )
        for job in ingestion_jobs:
            key = (TaskType.INGESTION, job.id)
            if key not in active_keys and self._ingestion_recoverable(job.id):
                item, reused = self.storage.enqueue(
                    TaskType.INGESTION,
                    job.id,
                    self.settings.worker_max_attempts,
                )
                if not reused:
                    report.recreated_items.append(item.id)
        runs = self.processing.storage.list_runs((ProcessingStatus.CREATED, ProcessingStatus.PARTIAL))
        for run in runs:
            key = (TaskType.PROCESSING, run.id)
            if key not in active_keys:
                item, reused = self.storage.enqueue(
                    TaskType.PROCESSING,
                    run.id,
                    self.settings.worker_max_attempts,
                )
                if not reused:
                    report.recreated_items.append(item.id)
        return report

    def _aggregate_state(self, item: WorkItem) -> str:
        if item.task_type == TaskType.INGESTION:
            job = self.ingestion.get_job(item.aggregate_id)
            if job is None:
                return "missing"
            if job.status in {IngestionStatus.READY, IngestionStatus.COMPLETED}:
                return "completed"
            if job.status == IngestionStatus.CANCELLED:
                return "cancelled"
            if job.status in {IngestionStatus.FAILED, IngestionStatus.PARTIAL}:
                return "failed"
            return "active"
        run = self.processing.get_run(item.aggregate_id)
        if run is None:
            return "missing"
        if run.status == ProcessingStatus.COMPLETED:
            return "completed"
        if run.status == ProcessingStatus.CANCELLED:
            return "cancelled"
        if run.status == ProcessingStatus.FAILED:
            return "failed"
        return "active"

    def _ingestion_recoverable(self, job_id: str) -> bool:
        job = self.ingestion.get_job(job_id)
        if job is None or not job.source_reference:
            return False
        if job.source_reference.startswith("staging/"):
            try:
                path = self.ingestion._path_from_reference(  # noqa: SLF001 - controlled recovery boundary
                    job.source_reference,
                    self.ingestion.staging_dir,
                    "staging",
                )
                if path.is_file():
                    return True
            except Exception:
                return False
        return bool(job.asset_version_id and self.ingestion.storage.get_prepared_details(job.asset_version_id))

    def _fail_aggregate(self, item: WorkItem, code: str, message: str) -> None:
        if item.task_type == TaskType.INGESTION:
            self.ingestion.fail_execution(item.aggregate_id, code, message)
        elif item.task_type == TaskType.PROCESSING:
            self.processing.fail_execution(item.aggregate_id, code, message)

    def _cancel_aggregate(self, item: WorkItem) -> None:
        try:
            if item.task_type == TaskType.INGESTION:
                self.ingestion.cancel(item.aggregate_id)
            elif item.task_type == TaskType.PROCESSING:
                self.processing.cancel(item.aggregate_id)
        except Exception:
            logger.warning(
                "aggregate_cancellation_reconciliation_failed task_type=%s aggregate_id=%s",
                item.task_type,
                item.aggregate_id,
            )


def _process_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)
