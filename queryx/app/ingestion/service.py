from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from fastapi import UploadFile

from queryx.app.core.config import Settings
from queryx.app.ingestion.catalog_adapter import inspection_to_technical_metadata
from queryx.app.ingestion.fingerprint import (
    configuration_fingerprint,
    file_fingerprint,
    technical_schema_fingerprint,
)
from queryx.app.ingestion.models import (
    AssetSchemaDiff,
    AssetVersion,
    DataAsset,
    DataFormat,
    IngestionJob,
    IngestionStatus,
    ReconciliationReport,
    UploadResult,
)
from queryx.app.ingestion.readers import CSVReader, DatasetReader, ParquetReader
from queryx.app.ingestion.storage import (
    AssetNotFoundError,
    IngestionInProgressError,
    IngestionStorage,
    PreparedVersion,
)
from queryx.app.ingestion.validation import IngestionValidationError, validate_filename, validate_size
from queryx.app.worker.coordination import ExecutionInterruptedError


class IngestionServiceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 400, job_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.job_id = job_id


class IngestionCancelledError(RuntimeError):
    pass


class IngestionService:
    def __init__(self, settings: Settings, storage: IngestionStorage | None = None) -> None:
        self.settings = settings
        self.storage = storage or IngestionStorage(settings.catalog_db_path)
        self.raw_dir = settings.data_raw_dir
        self.staging_dir = settings.data_staging_dir
        self.normalized_dir = settings.data_normalized_dir
        for directory in (self.raw_dir, self.staging_dir, self.normalized_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.readers: dict[DataFormat, DatasetReader] = {
            DataFormat.CSV: CSVReader(settings.ingestion_csv_count_rows),
            DataFormat.PARQUET: ParquetReader(),
        }
        self._finalization_lock = threading.RLock()

    async def ingest_upload(self, upload: UploadFile, asset_id: str | None = None) -> UploadResult:
        accepted = await self.accept_upload(upload, asset_id=asset_id, enqueue=False)
        return self.execute_ingestion_job(accepted.job_id)

    async def accept_upload(
        self,
        upload: UploadFile,
        asset_id: str | None = None,
        enqueue: bool = True,
    ) -> UploadResult:
        original_filename = upload.filename or ""
        job = self.storage.create_job(original_filename=original_filename, asset_id=asset_id)
        staged_path: Path | None = None
        try:
            safe_name, data_format = validate_filename(original_filename)
            if asset_id is not None and self.storage.get_asset(asset_id) is None:
                raise IngestionServiceError("asset_not_found", f"asset '{asset_id}' not found", 404, job.id)

            suffix = Path(safe_name).suffix.lower()
            internal_name = f"{uuid4().hex}{suffix}"
            staged_path = self._controlled_path(self.staging_dir, internal_name)
            staging_reference = f"staging/{internal_name}"
            self.storage.transition_job(
                job.id,
                IngestionStatus.ACQUIRING,
                source_reference=staging_reference,
            )
            bytes_received = await self._save_bounded(upload, staged_path)
            self.storage.update_job(job.id, bytes_received=bytes_received)
            work_item_id: str | None = None
            if enqueue:
                try:
                    from queryx.app.worker.models import TaskType
                    from queryx.app.worker.storage import WorkerStorage

                    item, _ = WorkerStorage(self.settings.catalog_db_path).enqueue(
                        TaskType.INGESTION,
                        job.id,
                        self.settings.worker_max_attempts,
                    )
                    work_item_id = item.id
                except Exception as exc:
                    self._cleanup(staged_path)
                    self._fail_job(job.id, "work_item_creation_failed", "Ingestion could not be queued")
                    raise IngestionServiceError(
                        "work_item_creation_failed",
                        "Ingestion could not be queued",
                        500,
                        job.id,
                    ) from exc
            return UploadResult(
                job_id=job.id,
                work_item_id=work_item_id,
                status=IngestionStatus.ACQUIRING,
                asset_id=asset_id,
            )
        except IngestionValidationError as exc:
            self._cleanup(staged_path)
            self._fail_job(job.id, exc.code, exc.message)
            status_code = 413 if exc.code == "upload_too_large" else 415 if exc.code == "unsupported_format" else 400
            raise IngestionServiceError(exc.code, exc.message, status_code, job.id) from exc
        except IngestionServiceError:
            self._cleanup(staged_path)
            current = self.storage.get_job(job.id)
            if current is not None and current.status not in {IngestionStatus.FAILED, IngestionStatus.CANCELLED}:
                self._fail_job(job.id, "upload_acceptance_failed", "Upload could not be accepted")
            raise
        except Exception as exc:
            self._cleanup(staged_path)
            self._fail_job(job.id, "upload_acceptance_failed", "Upload could not be accepted")
            raise IngestionServiceError("upload_acceptance_failed", "Upload could not be accepted", 500, job.id) from exc
        finally:
            await upload.close()

    def execute_ingestion_job(
        self,
        job_id: str,
        checkpoint: Callable[[], None] | None = None,
        allow_retry: bool = False,
    ) -> UploadResult:
        job = self.storage.get_job(job_id)
        if job is None:
            raise IngestionServiceError("ingestion_job_not_found", "Ingestion job not found", 404, job_id)
        if job.status == IngestionStatus.READY:
            return UploadResult(
                job_id=job.id,
                status=job.status,
                asset_id=job.asset_id,
                asset_version_id=job.asset_version_id,
                reused=any(item.get("code") == "idempotent_retry" for item in job.warnings),
            )
        if job.status == IngestionStatus.CANCELLED:
            raise IngestionCancelledError("Ingestion was cancelled")
        if job.status not in {IngestionStatus.ACQUIRING, IngestionStatus.INSPECTING}:
            raise IngestionServiceError("ingestion_not_executable", "Ingestion job is not executable", 409, job_id)

        safe_name, data_format = validate_filename(job.original_filename)
        if not job.source_reference:
            raise IngestionServiceError("staged_file_missing", "Staged upload is unavailable", 409, job_id)
        staged_path = self._path_from_reference(job.source_reference, self.staging_dir, "staging")
        internal_name = staged_path.name
        raw_reference = f"raw/{internal_name}"
        raw_path = self._controlled_path(self.raw_dir, internal_name)
        prepared: PreparedVersion | None = None
        promoted = False
        try:
            self._checkpoint(checkpoint)
            if job.status == IngestionStatus.INSPECTING and job.asset_version_id and not staged_path.is_file():
                if self._recover_job(job):
                    recovered = self.storage.get_job(job.id)
                    assert recovered is not None
                    return UploadResult(
                        job_id=job.id,
                        status=recovered.status,
                        asset_id=recovered.asset_id,
                        asset_version_id=recovered.asset_version_id,
                    )
            if not staged_path.is_file():
                raise IngestionServiceError("staged_file_missing", "Staged upload is unavailable", 409, job.id)
            if job.status == IngestionStatus.ACQUIRING:
                job = self.storage.transition_job(job.id, IngestionStatus.INSPECTING)
            source_fingerprint = file_fingerprint(staged_path)
            self._checkpoint(checkpoint)
            inspection = self.readers[data_format].inspect(
                staged_path,
                preview_limit=self.settings.ingestion_preview_rows,
                sample_limit=self.settings.ingestion_inspection_rows,
            )
            self._checkpoint(checkpoint)
            technical_metadata = inspection_to_technical_metadata(inspection)
            schema_fingerprint = technical_schema_fingerprint(technical_metadata["fields"])
            recipe_fingerprint = self._recipe_fingerprint(data_format)

            with self._finalization_lock:
                try:
                    prepared = self.storage.prepare_version(
                        job_id=job.id,
                        name=Path(safe_name).stem,
                        requested_asset_id=job.requested_asset_id or job.asset_id,
                        raw_reference=raw_reference,
                        data_format=data_format,
                        source_fingerprint=source_fingerprint,
                        schema_fingerprint=schema_fingerprint,
                        recipe_fingerprint=recipe_fingerprint,
                        inspection=inspection,
                        technical_metadata=technical_metadata,
                    )
                except AssetNotFoundError as exc:
                    raise IngestionServiceError(
                        "asset_not_found",
                        f"asset '{job.requested_asset_id}' not found",
                        404,
                        job.id,
                    ) from exc
                except IngestionInProgressError as exc:
                    raise IngestionServiceError("ingestion_in_progress", str(exc), 409, job.id) from exc
                except sqlite3.IntegrityError as exc:
                    raise IngestionServiceError(
                        "ingestion_in_progress",
                        "A compatible ingestion was created concurrently; retry the request",
                        409,
                        job.id,
                    ) from exc

                if prepared.reused:
                    existing_raw = self._path_from_reference(prepared.raw_reference, self.raw_dir, "raw")
                    if (
                        not existing_raw.is_file()
                        or file_fingerprint(existing_raw) != prepared.version.source_fingerprint
                    ):
                        self.storage.fail_jobs_for_version(
                            prepared.version.id,
                            {"code": "raw_file_missing", "message": "Reusable version has no valid raw file"},
                        )
                        raise IngestionServiceError(
                            "raw_file_missing",
                            "Existing compatible version has no valid raw file",
                            409,
                            job.id,
                        )
                    self.storage.finalize_reused_job(
                        job.id,
                        prepared.version.id,
                        prepared.raw_reference,
                    )
                    self._cleanup(staged_path)
                    return UploadResult(
                        job_id=job.id,
                        status=IngestionStatus.READY,
                        asset_id=prepared.asset.id,
                        asset_version_id=prepared.version.id,
                        reused=True,
                    )

                self._checkpoint(checkpoint)
                self._promote(staged_path, raw_path)
                promoted = True
                self.storage.finalize_version(job.id, prepared.version.id, raw_reference, data_format)

            return UploadResult(
                job_id=job.id,
                status=IngestionStatus.READY,
                asset_id=prepared.asset.id,
                asset_version_id=prepared.version.id,
            )
        except ExecutionInterruptedError:
            raise
        except IngestionCancelledError:
            self._cleanup(staged_path, raw_path if promoted else None)
            self._fail_prepared(prepared)
            current = self.storage.get_job(job.id)
            if current and current.status in {IngestionStatus.ACQUIRING, IngestionStatus.INSPECTING}:
                self.storage.transition_job(job.id, IngestionStatus.CANCELLED)
            raise
        except IngestionValidationError as exc:
            self._cleanup(staged_path, raw_path if promoted else None)
            self._fail_prepared(prepared)
            self._fail_job(job.id, exc.code, exc.message)
            status_code = 413 if exc.code == "upload_too_large" else 415 if exc.code == "unsupported_format" else 400
            raise IngestionServiceError(exc.code, exc.message, status_code, job.id) from exc
        except (sqlite3.OperationalError, OSError) as exc:
            if allow_retry:
                self._fail_prepared(prepared if not promoted else None)
                raise
            self._cleanup(staged_path, raw_path if promoted else None)
            self._fail_prepared(prepared)
            self._fail_job(job.id, "ingestion_failed", "Ingestion failed")
            raise IngestionServiceError("ingestion_failed", "Ingestion failed", 500, job.id) from exc
        except IngestionServiceError as exc:
            self._cleanup(staged_path, raw_path if promoted else None)
            self._fail_prepared(prepared)
            self._fail_job(job.id, exc.code, exc.message)
            raise
        except Exception as exc:
            self._cleanup(staged_path, raw_path if promoted else None)
            self._fail_prepared(prepared)
            self._fail_job(job.id, "ingestion_failed", "Ingestion failed")
            raise IngestionServiceError("ingestion_failed", "Ingestion failed", 500, job.id) from exc

    def get_job(self, job_id: str) -> IngestionJob | None:
        return self.storage.get_job(job_id)

    def cancel(self, job_id: str) -> IngestionJob:
        job = self.storage.get_job(job_id)
        if job is None:
            raise IngestionServiceError("ingestion_job_not_found", "Ingestion job not found", 404, job_id)
        if job.status in {
            IngestionStatus.READY,
            IngestionStatus.COMPLETED,
            IngestionStatus.PARTIAL,
            IngestionStatus.FAILED,
            IngestionStatus.CANCELLED,
        }:
            raise IngestionServiceError("ingestion_not_cancellable", "Ingestion job is terminal", 409, job_id)
        cancelled = self.storage.transition_job(job.id, IngestionStatus.CANCELLED)
        if job.source_reference and job.source_reference.startswith("staging/"):
            try:
                self._cleanup(self._path_from_reference(job.source_reference, self.staging_dir, "staging"))
            except IngestionServiceError:
                pass
        return cancelled

    def fail_execution(self, job_id: str, code: str, message: str) -> IngestionJob | None:
        self._fail_job(job_id, code, message)
        return self.storage.get_job(job_id)

    def get_preview(self, job_id: str) -> dict[str, object] | None:
        job = self.storage.get_job(job_id)
        if job is None:
            return None
        if job.inspection is None:
            raise IngestionServiceError("preview_not_ready", "Preview is not available for this job", 409, job_id)
        if job.asset_version_id is not None:
            binding = self.storage.get_binding(job.asset_version_id)
            if binding is not None:
                raw_path = self._path_from_reference(binding.physical_location, self.raw_dir, "raw")
                if not raw_path.is_file():
                    if job.inspection.preview:
                        return self._preview_payload(
                            job,
                            job.inspection.preview[: self.settings.ingestion_preview_rows],
                        )
                    raise IngestionServiceError("raw_file_missing", "The raw file is not available", 409, job_id)
                rows = self.readers[binding.format].preview(raw_path, self.settings.ingestion_preview_rows)
                return self._preview_payload(job, rows)
        if job.inspection.preview:
            return self._preview_payload(
                job,
                job.inspection.preview[: self.settings.ingestion_preview_rows],
            )
        raise IngestionServiceError("preview_not_ready", "Preview is not available for this job", 409, job_id)

    def _preview_payload(self, job: IngestionJob, rows: list[dict[str, object]]) -> dict[str, object]:
        assert job.inspection is not None
        return {
            "job_id": job.id,
            "status": job.status,
            "schema": [field.model_dump(mode="json") for field in job.inspection.fields],
            "metadata": job.inspection.metadata,
            "records_detected": job.inspection.records_detected,
            "records_estimated": job.inspection.records_estimated,
            "rows": rows,
            "preview_limit": self.settings.ingestion_preview_rows,
        }

    def list_assets(self) -> list[DataAsset]:
        return self.storage.list_assets()

    def get_asset(self, asset_id: str) -> DataAsset | None:
        return self.storage.get_asset(asset_id)

    def list_versions(self, asset_id: str) -> list[AssetVersion] | None:
        return self.storage.list_versions(asset_id)

    def get_version(self, asset_id: str, version_id: str) -> AssetVersion | None:
        return self.storage.get_version(asset_id, version_id)

    def get_latest_diff(self, asset_id: str) -> AssetSchemaDiff | None:
        return self.storage.get_latest_diff(asset_id)

    def get_version_diff(self, asset_id: str, version_id: str) -> AssetSchemaDiff | None:
        return self.storage.get_version_diff(asset_id, version_id)

    def reconcile(self, now: datetime | None = None) -> ReconciliationReport:
        report = ReconciliationReport()
        resolved_now = now or datetime.now(timezone.utc)
        cutoff = resolved_now - timedelta(seconds=self.settings.ingestion_stale_job_seconds)
        active_jobs = self.storage.list_jobs_in_statuses(
            (IngestionStatus.ACQUIRING, IngestionStatus.INSPECTING)
        )
        for job in active_jobs:
            if job.updated_at > cutoff:
                continue
            report.interrupted_jobs.append(job.id)
            if self._recover_job(job):
                report.recovered_jobs.append(job.id)
            else:
                report.failed_jobs.append(job.id)

        for binding in self.storage.list_bindings():
            if binding.backend_type != "file":
                continue
            try:
                path = self._path_from_reference(binding.physical_location, self.raw_dir, "raw")
            except IngestionServiceError:
                path = self.raw_dir / "__invalid__"
            if not path.is_file():
                report.missing_bindings.append(binding.id)
                report.failed_jobs.extend(
                    job_id
                    for job_id in self.storage.fail_jobs_for_version(
                        binding.asset_version_id,
                        {"code": "raw_file_missing", "message": "Storage binding points to a missing raw file"},
                    )
                    if job_id not in report.failed_jobs
                )

        active_references = {
            job.source_reference
            for job in self.storage.list_jobs_in_statuses(
                (IngestionStatus.ACQUIRING, IngestionStatus.INSPECTING)
            )
            if job.source_reference is not None
        }
        for path in self.staging_dir.iterdir():
            reference = f"staging/{path.name}"
            if path.is_file() and reference not in active_references:
                report.orphan_staging_files.append(reference)
                path.unlink()

        bound_raw = {
            binding.physical_location
            for binding in self.storage.list_bindings()
            if binding.backend_type == "file"
        }
        planned_raw = self.storage.list_planned_locations()
        for path in self.raw_dir.iterdir():
            reference = f"raw/{path.name}"
            if path.is_file() and reference not in bound_raw and reference not in planned_raw:
                report.orphan_raw_files.append(reference)
        return report

    def _recover_job(self, job: IngestionJob) -> bool:
        staged_path: Path | None = None
        if job.source_reference and job.source_reference.startswith("staging/"):
            try:
                staged_path = self._path_from_reference(job.source_reference, self.staging_dir, "staging")
            except IngestionServiceError:
                staged_path = None
        if job.status != IngestionStatus.INSPECTING or job.asset_version_id is None:
            self._cleanup(staged_path)
            self._fail_job(job.id, "interrupted_job", "Interrupted ingestion cannot be recovered")
            return False
        details = self.storage.get_prepared_details(job.asset_version_id)
        if details is None or details["status"] != "preparing" or not details["planned_location"]:
            self._cleanup(staged_path)
            self._fail_job(job.id, "interrupted_job", "Prepared version is not recoverable")
            return False
        raw_path: Path | None = None
        promoted = False
        try:
            data_format = DataFormat(details["format"])
            raw_path = self._path_from_reference(details["planned_location"], self.raw_dir, "raw")
            if raw_path.is_file():
                if file_fingerprint(raw_path) != details["source_fingerprint"]:
                    raise IngestionServiceError("fingerprint_mismatch", "Raw file fingerprint mismatch")
                self._cleanup(staged_path)
            elif staged_path is not None and staged_path.is_file():
                if file_fingerprint(staged_path) != details["source_fingerprint"]:
                    raise IngestionServiceError("fingerprint_mismatch", "Staging file fingerprint mismatch")
                self._promote(staged_path, raw_path)
                promoted = True
            else:
                raise IngestionServiceError("staged_file_missing", "No recoverable file is available")
            self.storage.finalize_version(job.id, job.asset_version_id, details["planned_location"], data_format)
            self.storage.append_job_warning(
                job.id,
                {"code": "recovered_job", "message": "Interrupted ingestion was finalized by reconciliation"},
            )
            return True
        except Exception:
            self._cleanup(staged_path, raw_path if promoted else None)
            self.storage.fail_prepared_version(job.asset_version_id)
            self._fail_job(job.id, "recovery_failed", "Interrupted ingestion could not be recovered")
            return False

    def _recipe_fingerprint(self, data_format: DataFormat) -> str:
        return configuration_fingerprint(
            {
                "version": 1,
                "format": data_format.value,
                "inspection_rows": self.settings.ingestion_inspection_rows,
                "csv_count_rows": self.settings.ingestion_csv_count_rows,
            }
        )

    async def _save_bounded(self, upload: UploadFile, destination: Path) -> int:
        received = 0
        try:
            with destination.open("xb") as output:
                while chunk := await upload.read(1024 * 1024):
                    received += len(chunk)
                    validate_size(received, self.settings.ingestion_max_upload_bytes)
                    output.write(chunk)
        except FileExistsError as exc:
            raise IngestionServiceError("storage_conflict", "Internal storage conflict", 500) from exc
        if received == 0:
            raise IngestionValidationError("empty_file", "The uploaded file is empty")
        return received

    @staticmethod
    def _promote(staged_path: Path, raw_path: Path) -> None:
        if raw_path.exists():
            raise IngestionServiceError("storage_conflict", "Internal raw path already exists", 500)
        staged_path.rename(raw_path)

    @staticmethod
    def _controlled_path(root: Path, internal_name: str) -> Path:
        resolved_root = root.resolve()
        candidate = (resolved_root / internal_name).resolve()
        if candidate.parent != resolved_root:
            raise IngestionServiceError("unsafe_storage_path", "Internal storage path is invalid", 500)
        return candidate

    @classmethod
    def _path_from_reference(cls, reference: str, root: Path, prefix: str) -> Path:
        expected = f"{prefix}/"
        if not reference.startswith(expected):
            raise IngestionServiceError("unsafe_storage_path", "Stored file reference is invalid", 500)
        internal_name = reference[len(expected) :]
        return cls._controlled_path(root, internal_name)

    def _fail_prepared(self, prepared: PreparedVersion | None) -> None:
        if prepared is not None and not prepared.reused:
            self.storage.fail_prepared_version(prepared.version.id)

    def _fail_job(self, job_id: str, code: str, message: str) -> None:
        job = self.storage.get_job(job_id)
        if job is None or job.status in {
            IngestionStatus.FAILED,
            IngestionStatus.CANCELLED,
            IngestionStatus.COMPLETED,
            IngestionStatus.PARTIAL,
            IngestionStatus.READY,
        }:
            return
        self.storage.transition_job(
            job_id,
            IngestionStatus.FAILED,
            error_json=json.dumps({"code": code, "message": message}, sort_keys=True),
        )

    @staticmethod
    def _cleanup(*paths: Path | None) -> None:
        for path in paths:
            if path is not None and path.is_file():
                path.unlink()

    @staticmethod
    def _checkpoint(checkpoint: Callable[[], None] | None) -> None:
        if checkpoint is not None:
            checkpoint()
