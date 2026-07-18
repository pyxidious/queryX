from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from queryx.app.core.config import Settings
from queryx.app.ingestion.catalog_adapter import inspection_to_technical_metadata
from queryx.app.ingestion.fingerprint import (
    configuration_fingerprint,
    file_fingerprint,
    technical_schema_fingerprint,
)
from queryx.app.ingestion.models import DataAsset, DataFormat, IngestionJob, IngestionStatus, UploadResult
from queryx.app.ingestion.readers import CSVReader, DatasetReader, ParquetReader
from queryx.app.ingestion.storage import IngestionStorage
from queryx.app.ingestion.validation import IngestionValidationError, validate_filename, validate_size


class IngestionServiceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 400, job_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.job_id = job_id


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

    async def ingest_upload(self, upload: UploadFile) -> UploadResult:
        original_filename = upload.filename or ""
        job = self.storage.create_job(original_filename=original_filename)
        staged_path: Path | None = None
        raw_path: Path | None = None
        try:
            safe_name, data_format = validate_filename(original_filename)
            suffix = Path(safe_name).suffix.lower()
            internal_name = f"{uuid4().hex}{suffix}"
            staged_path = self._controlled_path(self.staging_dir, internal_name)
            self.storage.transition_job(job.id, IngestionStatus.ACQUIRING)
            bytes_received = await self._save_bounded(upload, staged_path)
            self.storage.transition_job(
                job.id,
                IngestionStatus.INSPECTING,
                bytes_received=bytes_received,
                source_reference=f"staging/{internal_name}",
            )
            source_fingerprint = file_fingerprint(staged_path)
            inspection = self.readers[data_format].inspect(
                staged_path,
                preview_limit=self.settings.ingestion_preview_rows,
                sample_limit=self.settings.ingestion_inspection_rows,
            )
            technical_metadata = inspection_to_technical_metadata(inspection)
            schema_fingerprint = technical_schema_fingerprint(technical_metadata["fields"])
            recipe_fingerprint = configuration_fingerprint(
                {
                    "version": 1,
                    "format": data_format.value,
                    "inspection_rows": self.settings.ingestion_inspection_rows,
                    "csv_count_rows": self.settings.ingestion_csv_count_rows,
                }
            )
            raw_path = self._controlled_path(self.raw_dir, internal_name)
            if raw_path.exists():
                raise IngestionServiceError("storage_conflict", "Internal storage conflict", 500, job.id)
            staged_path.rename(raw_path)
            asset, version = self.storage.create_asset_for_job(
                job_id=job.id,
                name=Path(safe_name).stem,
                source_reference=f"raw/{internal_name}",
                data_format=data_format,
                source_fingerprint=source_fingerprint,
                schema_fingerprint=schema_fingerprint,
                recipe_fingerprint=recipe_fingerprint,
                inspection=inspection,
            )
            return UploadResult(
                job_id=job.id,
                status=IngestionStatus.READY,
                asset_id=asset.id,
                asset_version_id=version.id,
            )
        except IngestionValidationError as exc:
            self._cleanup(staged_path, raw_path)
            self._fail_job(job.id, exc.code, exc.message)
            status_code = 413 if exc.code == "upload_too_large" else 415 if exc.code == "unsupported_format" else 400
            raise IngestionServiceError(exc.code, exc.message, status_code, job.id) from exc
        except IngestionServiceError:
            self._cleanup(staged_path, raw_path)
            self._fail_job(job.id, "ingestion_failed", "Ingestion failed")
            raise
        except Exception as exc:
            self._cleanup(staged_path, raw_path)
            self._fail_job(job.id, "ingestion_failed", "Ingestion failed")
            raise IngestionServiceError("ingestion_failed", "Ingestion failed", 500, job.id) from exc
        finally:
            await upload.close()

    def get_job(self, job_id: str) -> IngestionJob | None:
        return self.storage.get_job(job_id)

    def get_preview(self, job_id: str) -> dict[str, object] | None:
        job = self.storage.get_job(job_id)
        if job is None:
            return None
        if job.inspection is None:
            raise IngestionServiceError("preview_not_ready", "Preview is not available for this job", 409, job_id)
        return {
            "job_id": job.id,
            "status": job.status,
            "schema": [field.model_dump(mode="json") for field in job.inspection.fields],
            "metadata": job.inspection.metadata,
            "records_detected": job.inspection.records_detected,
            "records_estimated": job.inspection.records_estimated,
            "rows": job.inspection.preview[: self.settings.ingestion_preview_rows],
            "preview_limit": self.settings.ingestion_preview_rows,
        }

    def list_assets(self) -> list[DataAsset]:
        return self.storage.list_assets()

    def get_asset(self, asset_id: str) -> DataAsset | None:
        return self.storage.get_asset(asset_id)

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
    def _controlled_path(root: Path, internal_name: str) -> Path:
        resolved_root = root.resolve()
        candidate = (resolved_root / internal_name).resolve()
        if candidate.parent != resolved_root:
            raise IngestionServiceError("unsafe_storage_path", "Internal storage path is invalid", 500)
        return candidate

    def _fail_job(self, job_id: str, code: str, message: str) -> None:
        job = self.storage.get_job(job_id)
        if job is None or job.status in {IngestionStatus.FAILED, IngestionStatus.CANCELLED, IngestionStatus.COMPLETED, IngestionStatus.PARTIAL}:
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
