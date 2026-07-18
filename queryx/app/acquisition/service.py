from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from queryx.app.acquisition.models import (
    AcquisitionFile,
    AcquisitionFileStatus,
    AcquisitionReconciliationReport,
    AcquisitionRun,
    AcquisitionStatus,
    FileSelection,
)
from queryx.app.acquisition.providers.base import AcquisitionProvider
from queryx.app.acquisition.providers.kaggle import KaggleProvider, KaggleProviderError
from queryx.app.acquisition.storage import AcquisitionStorage
from queryx.app.acquisition.validation import (
    AcquisitionValidationError,
    validate_dataset_reference,
    validate_provider_file_reference,
    validate_version,
)
from queryx.app.core.config import Settings
from queryx.app.ingestion.fingerprint import file_fingerprint
from queryx.app.ingestion.models import IngestionStatus
from queryx.app.ingestion.service import IngestionService, IngestionServiceError
from queryx.app.worker.models import TaskType, WorkStatus
from queryx.app.worker.storage import WorkerStorage


class AcquisitionServiceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 400, *, transient: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.transient = transient


class AcquisitionCancelledError(RuntimeError):
    pass


class AcquisitionService:
    RECIPE_VERSION = "kaggle-acquisition-v1"

    def __init__(
        self,
        settings: Settings,
        storage: AcquisitionStorage | None = None,
        provider: AcquisitionProvider | None = None,
        ingestion: IngestionService | None = None,
        work_storage: WorkerStorage | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage or AcquisitionStorage(settings.catalog_db_path)
        self.ingestion = ingestion or IngestionService(settings)
        self.work_storage = work_storage or WorkerStorage(settings.catalog_db_path)
        self.temp_dir = settings.kaggle_temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.provider = provider

    def providers(self) -> list[dict[str, object]]:
        return [{"provider": "kaggle", "enabled": self.settings.kaggle_enabled, "formats": sorted(self.allowed_formats)}]

    @property
    def allowed_formats(self) -> set[str]:
        return {item.strip().lower() for item in self.settings.kaggle_allowed_formats.split(",") if item.strip()}

    def create_inspection(self, dataset: str, version: str | None = None, enqueue: bool = True) -> tuple[AcquisitionRun, str | None]:
        try:
            reference = validate_dataset_reference(dataset)
            requested = validate_version(version)
        except AcquisitionValidationError as exc:
            raise AcquisitionServiceError(exc.code, exc.message, 422) from exc
        active = self.storage.active_inspection(reference, requested)
        if active:
            raise AcquisitionServiceError("acquisition_in_progress", "Dataset inspection is already in progress", 409)
        run = self.storage.create_run(reference, requested)
        if not enqueue:
            return self.execute_inspection(run.id), None
        item, _ = self.work_storage.enqueue(TaskType.KAGGLE_INSPECT, run.id, self.settings.worker_max_attempts)
        return run, item.id

    def execute_inspection(self, run_id: str, checkpoint: Callable[[], None] | None = None) -> AcquisitionRun:
        run = self._run(run_id)
        if run.status == AcquisitionStatus.AWAITING_SELECTION:
            return run
        if run.status not in {AcquisitionStatus.CREATED, AcquisitionStatus.INSPECTING}:
            raise AcquisitionServiceError("acquisition_not_inspectable", "Acquisition cannot be inspected", 409)
        self._ensure_enabled()
        self.storage.transition(run.id, AcquisitionStatus.INSPECTING)
        self._checkpoint(checkpoint)
        try:
            manifest = self._provider().inspect_dataset(run.dataset_reference, run.requested_version)
        except KaggleProviderError as exc:
            raise AcquisitionServiceError(exc.code, exc.message, 503 if exc.transient else 422, transient=exc.transient) from exc
        if len(manifest.files) > self.settings.kaggle_max_files:
            raise AcquisitionServiceError("kaggle_file_limit_exceeded", "Dataset contains too many files", 422)
        if len(manifest.resolved_version) > 128:
            raise AcquisitionServiceError("kaggle_metadata_too_large", "Resolved version metadata is too large", 422)
        manifest = manifest.model_copy(
            update={
                "title": manifest.title[:500] if manifest.title else None,
                "license_name": manifest.license_name[:200] if manifest.license_name else None,
            }
        )
        total = 0
        for item in manifest.files:
            try:
                validate_provider_file_reference(item.reference)
            except AcquisitionValidationError as exc:
                raise AcquisitionServiceError(exc.code, exc.message, 422) from exc
            if len(item.name) > 255:
                raise AcquisitionServiceError("kaggle_filename_too_long", "Dataset contains an overlong filename", 422)
            if item.size_bytes is not None:
                if item.size_bytes > self.settings.kaggle_max_file_bytes:
                    raise AcquisitionServiceError("kaggle_file_too_large", "Dataset contains a file over the limit", 422)
                total += item.size_bytes
        if total > self.settings.kaggle_max_dataset_bytes:
            raise AcquisitionServiceError("kaggle_dataset_too_large", "Dataset exceeds the configured limit", 422)
        self._checkpoint(checkpoint)
        return self.storage.save_manifest(run.id, manifest, self.allowed_formats)

    def start(
        self,
        run_id: str,
        selections: list[FileSelection],
        enqueue: bool = True,
    ) -> tuple[AcquisitionRun, str | None, bool]:
        run = self._run(run_id)
        if run.status in {AcquisitionStatus.DOWNLOADING, AcquisitionStatus.AWAITING_INGESTION}:
            raise AcquisitionServiceError("acquisition_in_progress", "Acquisition is already in progress", 409)
        if run.status == AcquisitionStatus.COMPLETED:
            return run, None, True
        if run.status != AcquisitionStatus.AWAITING_SELECTION:
            raise AcquisitionServiceError("acquisition_not_selectable", "Manifest is not ready for selection", 409)
        if not selections or len(selections) > self.settings.kaggle_max_files:
            raise AcquisitionServiceError("invalid_file_selection", "Select at least one file within the configured limit", 422)
        files = {item.id: item for item in self.storage.list_files(run.id)}
        if len({item.file_id for item in selections}) != len(selections):
            raise AcquisitionServiceError("duplicate_file_selection", "A file may be selected only once", 422)
        for selected in selections:
            item = files.get(selected.file_id)
            if item is None:
                raise AcquisitionServiceError("manifest_file_not_found", "Selected file is not in the manifest", 404)
            if item.status != AcquisitionFileStatus.DISCOVERED:
                raise AcquisitionServiceError("unsupported_manifest_file", "Selected file is not supported", 422)
            if selected.target_asset_id and self.ingestion.get_asset(selected.target_asset_id) is None:
                raise AcquisitionServiceError("asset_not_found", "Target asset was not found", 404)
        fingerprint = self.request_fingerprint(run, selections, files)
        if self.storage.active_for_fingerprint(fingerprint):
            raise AcquisitionServiceError("acquisition_in_progress", "Equivalent acquisition is already in progress", 409)
        compatible = self.storage.completed_for_fingerprint(fingerprint)
        if compatible:
            self.storage.transition(run.id, AcquisitionStatus.CANCELLED)
            return compatible, None, True
        try:
            updated = self.storage.select_files(run.id, selections, fingerprint)
        except (KeyError, ValueError) as exc:
            raise AcquisitionServiceError("manifest_file_not_found", "Selected file is not available", 404) from exc
        if not enqueue:
            return self.execute_download(updated.id), None, False
        item, _ = self.work_storage.enqueue(TaskType.KAGGLE_DOWNLOAD, updated.id, self.settings.worker_max_attempts)
        return updated, item.id, False

    def execute_download(self, run_id: str, checkpoint: Callable[[], None] | None = None) -> AcquisitionRun:
        run = self._run(run_id)
        if run.status == AcquisitionStatus.AWAITING_INGESTION:
            return run
        if run.status not in {AcquisitionStatus.DOWNLOADING}:
            raise AcquisitionServiceError("acquisition_not_downloadable", "Acquisition cannot be downloaded", 409)
        self._ensure_enabled()
        selected = [item for item in self.storage.list_files(run.id) if item.selected]
        downloaded = 0
        failed = 0
        for item in selected:
            self._checkpoint(checkpoint)
            if item.status == AcquisitionFileStatus.QUEUED_FOR_INGESTION:
                downloaded += 1
                continue
            try:
                self._download_and_dispatch(run, item, checkpoint)
                downloaded += 1
            except AcquisitionCancelledError:
                raise
            except AcquisitionServiceError as exc:
                failed += 1
                self.storage.update_file(
                    item.id,
                    AcquisitionFileStatus.FAILED,
                    error={"code": exc.code, "message": exc.message},
                )
                if exc.transient:
                    raise
        if downloaded == 0:
            self.storage.transition(run.id, AcquisitionStatus.FAILED, files_downloaded=0, files_failed=failed)
            raise AcquisitionServiceError("acquisition_download_failed", "No selected file could be acquired", 422)
        return self.storage.transition(
            run.id,
            AcquisitionStatus.AWAITING_INGESTION,
            files_downloaded=downloaded,
            files_failed=failed,
        )

    def _download_and_dispatch(
        self,
        run: AcquisitionRun,
        item: AcquisitionFile,
        checkpoint: Callable[[], None] | None,
    ) -> None:
        reference = validate_provider_file_reference(item.provider_file_reference)
        run_dir = self.temp_dir / run.id
        run_dir.mkdir(mode=0o700, exist_ok=True)
        suffix = Path(item.display_name).suffix.lower()
        target = run_dir / f"{uuid4().hex}{suffix}"
        self.storage.update_file(item.id, AcquisitionFileStatus.DOWNLOADING)
        try:
            received = self._provider().download_file(
                run.dataset_reference,
                run.resolved_version or run.requested_version,
                reference,
                target,
                min(self.settings.kaggle_max_file_bytes, self.settings.ingestion_max_upload_bytes),
                checkpoint,
            )
            if target.is_symlink() or not target.is_file():
                raise AcquisitionServiceError("unsafe_download_output", "Provider output is unsafe", 422)
            fingerprint = file_fingerprint(target)
            self.storage.update_file(
                item.id,
                AcquisitionFileStatus.DOWNLOADED,
                size_bytes=received,
                content_fingerprint=fingerprint,
            )
            result = self.ingestion.accept_acquired_file(
                target,
                item.display_name,
                asset_id=item.target_asset_id,
                logical_name=item.logical_name,
                enqueue=True,
            )
            self.storage.update_file(
                item.id,
                AcquisitionFileStatus.QUEUED_FOR_INGESTION,
                ingestion_job_id=result.job_id,
                content_fingerprint=fingerprint,
            )
        except KaggleProviderError as exc:
            raise AcquisitionServiceError(exc.code, exc.message, 503 if exc.transient else 422, transient=exc.transient) from exc
        except IngestionServiceError as exc:
            raise AcquisitionServiceError(exc.code, exc.message, exc.status_code) from exc
        finally:
            target.unlink(missing_ok=True)

    def reconcile(self, now: datetime | None = None) -> AcquisitionReconciliationReport:
        report = AcquisitionReconciliationReport()
        resolved = now or datetime.now(timezone.utc)
        active = self.storage.list_runs(
            (AcquisitionStatus.CREATED, AcquisitionStatus.INSPECTING, AcquisitionStatus.DOWNLOADING, AcquisitionStatus.AWAITING_INGESTION)
        )
        for run in active:
            if run.status in {AcquisitionStatus.CREATED, AcquisitionStatus.INSPECTING, AcquisitionStatus.DOWNLOADING}:
                if run.updated_at < resolved - timedelta(seconds=self.settings.ingestion_stale_job_seconds):
                    task = TaskType.KAGGLE_INSPECT if run.status in {AcquisitionStatus.CREATED, AcquisitionStatus.INSPECTING} else TaskType.KAGGLE_DOWNLOAD
                    if self.work_storage.active_for(task, run.id) is None:
                        item, reused = self.work_storage.enqueue(task, run.id, self.settings.worker_max_attempts)
                        if not reused:
                            report.jobs_requeued.append(item.id)
                continue
            ready = failed = pending = 0
            for item in [value for value in self.storage.list_files(run.id) if value.selected]:
                if not item.ingestion_job_id:
                    if item.status == AcquisitionFileStatus.DOWNLOADED:
                        report.missing_jobs.append(item.id)
                    failed += item.status == AcquisitionFileStatus.FAILED
                    pending += item.status not in {AcquisitionFileStatus.FAILED, AcquisitionFileStatus.CANCELLED}
                    continue
                job = self.ingestion.get_job(item.ingestion_job_id)
                if job is None:
                    self.storage.update_file(
                        item.id,
                        AcquisitionFileStatus.FAILED,
                        error={"code": "child_job_missing", "message": "Child ingestion job is missing"},
                    )
                    report.missing_jobs.append(item.id)
                    failed += 1
                elif job.status == IngestionStatus.READY:
                    self.storage.update_file(
                        item.id,
                        AcquisitionFileStatus.READY,
                        asset_id=job.asset_id,
                        asset_version_id=job.asset_version_id,
                        content_fingerprint=job.source_fingerprint or item.content_fingerprint,
                    )
                    if job.asset_version_id:
                        self.ingestion.storage.add_lineage(
                            self.source_reference(run, item), job.asset_version_id, "kaggle_acquisition"
                        )
                    ready += 1
                elif job.status in {IngestionStatus.FAILED, IngestionStatus.CANCELLED}:
                    target = AcquisitionFileStatus.CANCELLED if job.status == IngestionStatus.CANCELLED else AcquisitionFileStatus.FAILED
                    self.storage.update_file(item.id, target)
                    failed += 1
                else:
                    pending += 1
                    if self.work_storage.active_for(TaskType.INGESTION, job.id) is None:
                        work, reused = self.work_storage.enqueue(TaskType.INGESTION, job.id, self.settings.worker_max_attempts)
                        if not reused:
                            report.jobs_requeued.append(work.id)
            if pending:
                continue
            status = AcquisitionStatus.COMPLETED if ready and not failed else AcquisitionStatus.PARTIAL if ready else AcquisitionStatus.FAILED
            self.storage.transition(run.id, status, files_ready=ready, files_failed=failed)
            report.runs_updated.append(run.id)
        referenced_dirs = {run.id for run in self.storage.list_runs()}
        for path in self.temp_dir.iterdir():
            if path.is_dir() and path.name not in referenced_dirs:
                shutil.rmtree(path, ignore_errors=True)
                report.orphan_temporaries.append(path.name)
        return report

    def cancel(self, run_id: str) -> AcquisitionRun:
        run = self._run(run_id)
        if run.status in {AcquisitionStatus.COMPLETED, AcquisitionStatus.PARTIAL, AcquisitionStatus.FAILED, AcquisitionStatus.CANCELLED}:
            raise AcquisitionServiceError("acquisition_not_cancellable", "Terminal acquisition cannot be cancelled", 409)
        for task in (TaskType.KAGGLE_INSPECT, TaskType.KAGGLE_DOWNLOAD):
            item = self.work_storage.active_for(task, run.id)
            if item:
                self.work_storage.request_cancel(item.id)
        ready = 0
        for item in [value for value in self.storage.list_files(run.id) if value.selected]:
            if item.status == AcquisitionFileStatus.READY:
                ready += 1
            elif item.status not in {AcquisitionFileStatus.FAILED, AcquisitionFileStatus.CANCELLED}:
                self.storage.update_file(item.id, AcquisitionFileStatus.CANCELLED)
        shutil.rmtree(self.temp_dir / run.id, ignore_errors=True)
        return self.storage.transition(run.id, AcquisitionStatus.PARTIAL if ready else AcquisitionStatus.CANCELLED)

    def fail_execution(self, run_id: str, code: str, message: str) -> None:
        run = self.storage.get_run(run_id)
        if run and run.status not in {AcquisitionStatus.COMPLETED, AcquisitionStatus.PARTIAL, AcquisitionStatus.CANCELLED}:
            self.storage.add_error(run_id, {"code": code, "message": message})
            self.storage.transition(run_id, AcquisitionStatus.FAILED)

    def request_fingerprint(
        self, run: AcquisitionRun, selections: list[FileSelection], files: dict[str, AcquisitionFile]
    ) -> str:
        payload = {
            "provider": run.provider,
            "dataset": run.dataset_reference,
            "version": run.resolved_version,
            "files": sorted(
                (
                    files[item.file_id].provider_file_reference,
                    item.logical_name or "",
                    item.target_asset_id or "",
                )
                for item in selections
            ),
            "recipe": self.RECIPE_VERSION,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def source_reference(run: AcquisitionRun, item: AcquisitionFile) -> str:
        return f"kaggle://{run.dataset_reference}@{run.resolved_version}/{item.provider_file_reference}"

    def _provider(self) -> AcquisitionProvider:
        if self.provider is not None:
            return self.provider
        credentials = Path(self.settings.kaggle_credentials_path) if self.settings.kaggle_credentials_path else Path()
        self.provider = KaggleProvider(credentials, self.settings.kaggle_download_timeout_seconds)
        return self.provider

    def _ensure_enabled(self) -> None:
        if not self.settings.kaggle_enabled:
            raise AcquisitionServiceError("kaggle_disabled", "Kaggle acquisition is disabled", 422)
        if self.provider is None and not self.settings.kaggle_credentials_path:
            raise AcquisitionServiceError("kaggle_credentials_missing", "Kaggle credentials are not configured", 422)

    def _run(self, run_id: str) -> AcquisitionRun:
        run = self.storage.get_run(run_id)
        if run is None:
            raise AcquisitionServiceError("acquisition_not_found", "Acquisition was not found", 404)
        return run

    @staticmethod
    def _checkpoint(checkpoint: Callable[[], None] | None) -> None:
        if checkpoint:
            checkpoint()
