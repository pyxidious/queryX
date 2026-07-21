from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from queryx.app.core.config import Settings
from queryx.app.core.storage_paths import StorageReferenceError, resolve_storage_reference
from queryx.app.ingestion.fingerprint import file_fingerprint, technical_schema_fingerprint
from queryx.app.ingestion.models import (
    BackendType,
    AssetVersion,
    BindingRole,
    BindingStatus,
    DataFormat,
    StorageBinding,
)
from queryx.app.ingestion.storage import IngestionStorage
from queryx.app.processing.models import (
    ProcessingReconciliationReport,
    ProcessingRun,
    ProcessingStatus,
)
from queryx.app.processing.normalizers.parquet import CanonicalParquetNormalizer, NormalizationError
from queryx.app.processing.recipe import CanonicalParquetRecipe, canonical_parquet_recipe
from queryx.app.processing.serving.duckdb import DuckDBLockTimeout, DuckDBServingAdapter
from queryx.app.processing.storage import ProcessingInProgressError, ProcessingStorage
from queryx.app.processing.validation import (
    ProcessingValidationError,
    schemas_compatible,
    validate_normalized_file,
)
from queryx.app.worker.coordination import ExecutionInterruptedError


class ProcessingServiceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 400, run_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.run_id = run_id


class ProcessingCancelledError(RuntimeError):
    pass


class ProcessingService:
    def __init__(
        self,
        settings: Settings,
        storage: ProcessingStorage | None = None,
        ingestion_storage: IngestionStorage | None = None,
        normalizer: CanonicalParquetNormalizer | None = None,
        serving: DuckDBServingAdapter | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage or ProcessingStorage(settings.catalog_db_path)
        self.ingestion_storage = ingestion_storage or IngestionStorage(settings.catalog_db_path)
        self.normalizer = normalizer or CanonicalParquetNormalizer()
        lock_path = settings.duckdb_lock_path
        if (
            lock_path == Path("data/queryx.duckdb.lock")
            and settings.duckdb_path != Path("data/queryx.duckdb")
        ):
            lock_path = settings.duckdb_path.parent / "queryx.duckdb.lock"
        self.serving = serving or DuckDBServingAdapter(
            settings.duckdb_path,
            settings.duckdb_schema,
            lock_path,
            settings.duckdb_lock_timeout_seconds,
        )
        self.raw_dir = settings.data_raw_dir
        self.normalized_dir = settings.data_normalized_dir
        if settings.duckdb_path.resolve().parent != self.normalized_dir.resolve().parent:
            raise ValueError("DuckDB and normalized storage must share the managed data volume")
        if lock_path.resolve().parent != self.normalized_dir.resolve().parent:
            raise ValueError("DuckDB lock and normalized storage must share the managed data volume")
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.normalized_dir.mkdir(parents=True, exist_ok=True)

    def prepare(
        self,
        asset_id: str,
        version_id: str,
        recipe: CanonicalParquetRecipe | None = None,
    ) -> ProcessingRun:
        run, _, resolved_recipe = self.create_processing_run(asset_id, version_id, recipe)
        if run.status == ProcessingStatus.COMPLETED:
            return run
        return self.execute_processing_run(run.id, resolved_recipe)

    def create_processing_run(
        self,
        asset_id: str,
        version_id: str,
        recipe: CanonicalParquetRecipe | None = None,
    ) -> tuple[ProcessingRun, str, CanonicalParquetRecipe]:
        version = self.ingestion_storage.get_version(asset_id, version_id)
        if version is None:
            if self.ingestion_storage.get_asset(asset_id) is None:
                raise ProcessingServiceError("asset_not_found", f"asset '{asset_id}' not found", 404)
            raise ProcessingServiceError("asset_version_not_found", f"asset_version '{version_id}' not found", 404)
        if version.status == "failed":
            version = self._recover_legacy_processing_failure(version)
        if version.status != "ready":
            raise ProcessingServiceError("asset_version_not_ready", "Asset version is not ready", 409)
        inspection = self.ingestion_storage.get_version_inspection(version_id)
        if inspection is None:
            raise ProcessingServiceError("observed_schema_missing", "Observed raw schema is unavailable", 409)
        raw_bindings = self.storage.list_bindings(version_id, BindingRole.RAW, BindingStatus.READY)
        raw_binding = next((binding for binding in raw_bindings if binding.backend_type == "file"), None)
        if raw_binding is None:
            raise ProcessingServiceError("raw_binding_missing", "Ready raw binding is unavailable", 409)
        raw_path = self._path_from_reference(raw_binding.physical_location, self.raw_dir, "raw")
        if not raw_path.is_file():
            raise ProcessingServiceError("raw_file_missing", "Raw file is unavailable", 409)

        resolved_recipe = recipe or canonical_parquet_recipe(
            inspection,
            self.settings.parquet_compression,
            self.settings.parquet_batch_rows,
        )
        try:
            run, mode = self.storage.create_or_reuse_run(
                version_id,
                raw_binding.id,
                resolved_recipe.name,
                resolved_recipe.version,
                resolved_recipe.fingerprint,
                [field.model_dump(mode="json") for field in inspection.fields],
                resolved_recipe.model_dump(mode="json"),
            )
        except ProcessingInProgressError as exc:
            raise ProcessingServiceError("processing_in_progress", str(exc), 409) from exc
        if mode == "completed":
            self._assert_completed_outputs(run)
        return run, mode, resolved_recipe

    def execute_processing_run(
        self,
        run_id: str,
        recipe: CanonicalParquetRecipe | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> ProcessingRun:
        run = self.storage.get_run(run_id)
        if run is None:
            raise ProcessingServiceError("processing_run_not_found", "Processing run not found", 404, run_id)
        if run.status == ProcessingStatus.COMPLETED:
            self._assert_completed_outputs(run)
            return run
        version = self.ingestion_storage.get_version_by_id(run.asset_version_id)
        if version is None:
            raise ProcessingServiceError("asset_version_not_found", "Asset version not found", 404, run.id)
        inspection = self.ingestion_storage.get_version_inspection(version.id)
        if inspection is None:
            raise ProcessingServiceError("observed_schema_missing", "Observed raw schema is unavailable", 409, run.id)
        resolved_recipe = recipe
        if resolved_recipe is None and run.recipe:
            resolved_recipe = CanonicalParquetRecipe.model_validate(run.recipe)
        if resolved_recipe is None:
            resolved_recipe = canonical_parquet_recipe(
                inspection,
                self.settings.parquet_compression,
                self.settings.parquet_batch_rows,
            )
        if resolved_recipe.fingerprint != run.recipe_fingerprint:
            raise ProcessingServiceError("recipe_mismatch", "Processing recipe does not match the run", 409, run.id)
        if run.status == ProcessingStatus.PARTIAL:
            self._checkpoint(checkpoint)
            return self._resume_partial(run, version, resolved_recipe, checkpoint)
        if run.status != ProcessingStatus.CREATED:
            raise ProcessingServiceError("processing_in_progress", "Processing run is already active", 409, run.id)
        raw_binding = self.storage.get_binding(run.input_binding_id)
        if raw_binding is None or raw_binding.status != BindingStatus.READY:
            raise ProcessingServiceError("raw_binding_missing", "Ready raw binding is unavailable", 409, run.id)
        raw_path = self._path_from_reference(raw_binding.physical_location, self.raw_dir, "raw")
        if not raw_path.is_file():
            raise ProcessingServiceError("raw_file_missing", "Raw file is unavailable", 409, run.id)

        self._checkpoint(checkpoint)
        run = self.storage.transition_run(run.id, ProcessingStatus.NORMALIZING)
        temp_path = self.normalized_dir / f".tmp-{uuid4().hex}.parquet"
        normalized_name = f"{uuid4().hex}.parquet"
        normalized_reference = f"normalized/{normalized_name}"
        normalized_path = self._path_from_reference(normalized_reference, self.normalized_dir, "normalized")
        normalized_binding, already_ready = self.storage.prepare_binding(
            version.id,
            BindingRole.NORMALIZED,
            BackendType.FILE,
            normalized_reference,
            resolved_recipe.fingerprint,
            DataFormat.PARQUET,
            {
                "recipe_name": resolved_recipe.name,
                "recipe_version": resolved_recipe.version,
                "recipe": resolved_recipe.model_dump(mode="json"),
            },
        )
        normalized_path = self._path_from_reference(
            normalized_binding.physical_location,
            self.normalized_dir,
            "normalized",
        )
        promoted = False
        try:
            if already_ready:
                canonical_schema = normalized_binding.metadata.get("canonical_schema", [])
                result_records = int(normalized_binding.metadata.get("records", 0))
                content_fingerprint = normalized_binding.content_fingerprint or ""
                schema_fingerprint = normalized_binding.schema_fingerprint or ""
                bytes_written = normalized_path.stat().st_size
            else:
                result = self.normalizer.normalize(
                    raw_path,
                    temp_path,
                    inspection,
                    resolved_recipe,
                    resolved_recipe.batch_rows,
                    checkpoint,
                )
                validate_normalized_file(temp_path, result.content_fingerprint, result.canonical_schema)
                self._promote(temp_path, normalized_path)
                promoted = True
                canonical_schema = result.canonical_schema
                result_records = result.records_written
                content_fingerprint = result.content_fingerprint
                schema_fingerprint = result.schema_fingerprint
                bytes_written = result.bytes_written
                normalized_binding = self.storage.ready_binding(
                    normalized_binding.id,
                    content_fingerprint,
                    schema_fingerprint,
                    {
                        "recipe_name": resolved_recipe.name,
                        "recipe_version": resolved_recipe.version,
                        "recipe": resolved_recipe.model_dump(mode="json"),
                        "canonical_schema": canonical_schema,
                        "records": result_records,
                    },
                )
            run = self.storage.transition_run(
                run.id,
                ProcessingStatus.REGISTERING,
                normalized_binding_id=normalized_binding.id,
                records_read=result_records,
                records_written=result_records,
                records_rejected=0,
                bytes_written=bytes_written,
                canonical_schema_json=json.dumps(canonical_schema, sort_keys=True),
            )
            self._checkpoint(checkpoint)
        except ExecutionInterruptedError:
            current = self.storage.get_run(run.id) or run
            current_binding = self.storage.get_binding(normalized_binding.id)
            if current.status == ProcessingStatus.REGISTERING and current_binding and current_binding.status == "ready":
                self._cleanup(temp_path)
                self.storage.transition_run(current.id, ProcessingStatus.PARTIAL)
            else:
                self._cleanup(temp_path, normalized_path if promoted else None)
                if promoted:
                    self.storage.force_fail_ready_binding(normalized_binding.id)
                elif not already_ready:
                    self.storage.fail_binding(normalized_binding.id)
                self.storage.reset_interrupted_run(run.id)
            raise
        except ProcessingCancelledError:
            self._cleanup(temp_path, normalized_path if promoted else None)
            if promoted:
                self.storage.force_fail_ready_binding(normalized_binding.id)
            elif not already_ready:
                self.storage.fail_binding(normalized_binding.id)
            current = self.storage.get_run(run.id) or run
            if current.status in {ProcessingStatus.NORMALIZING, ProcessingStatus.REGISTERING}:
                self.storage.transition_run(current.id, ProcessingStatus.CANCELLED)
            raise
        except (NormalizationError, ProcessingValidationError) as exc:
            self._cleanup(temp_path, normalized_path if promoted else None)
            if promoted:
                self.storage.force_fail_ready_binding(normalized_binding.id)
            elif not already_ready:
                self.storage.fail_binding(normalized_binding.id)
            return self._fail_run(
                run,
                getattr(exc, "code", "normalization_failed"),
                str(exc),
                getattr(exc, "details", None),
            )
        except Exception:
            self._cleanup(temp_path, normalized_path if promoted else None)
            if promoted:
                self.storage.force_fail_ready_binding(normalized_binding.id)
            elif not already_ready:
                self.storage.fail_binding(normalized_binding.id)
            return self._fail_run(run, "normalization_failed", "Canonical normalization failed")
        finally:
            self._cleanup(temp_path)
        return self._register(
            run,
            version.asset_id,
            version.version_number,
            resolved_recipe,
            normalized_binding,
            checkpoint,
        )

    def _resume_partial(
        self,
        run: ProcessingRun,
        version: AssetVersion,
        recipe: CanonicalParquetRecipe,
        checkpoint: Callable[[], None] | None = None,
    ) -> ProcessingRun:
        if run.normalized_binding_id is None:
            raise ProcessingServiceError("partial_run_not_resumable", "Partial run has no normalized binding", 409, run.id)
        binding = self.storage.get_binding(run.normalized_binding_id)
        if binding is None or binding.status != "ready":
            raise ProcessingServiceError("partial_run_not_resumable", "Normalized binding is not ready", 409, run.id)
        run = self.storage.transition_run(run.id, ProcessingStatus.REGISTERING, finished_at=None)
        return self._register(
            run,
            version.asset_id,
            version.version_number,
            recipe,
            binding,
            checkpoint,
        )

    def _register(
        self,
        run: ProcessingRun,
        asset_id: str,
        version_number: int,
        recipe: CanonicalParquetRecipe,
        normalized_binding: StorageBinding,
        checkpoint: Callable[[], None] | None = None,
    ) -> ProcessingRun:
        normalized_path = self._path_from_reference(
            normalized_binding.physical_location, self.normalized_dir, "normalized"
        )
        canonical_schema = run.canonical_schema or normalized_binding.metadata.get("canonical_schema", [])
        relation = self._relation_name(asset_id, version_number, recipe.fingerprint)
        serving_location = (
            f"duckdb/{self.settings.duckdb_path.name}#"
            f"{self.settings.duckdb_schema}.{relation}"
        )
        serving_binding: StorageBinding | None = None
        view_created = False
        try:
            self._checkpoint(checkpoint)
            serving_binding, _ = self.storage.prepare_binding(
                run.asset_version_id,
                BindingRole.SERVING,
                BackendType.DUCKDB,
                serving_location,
                recipe.fingerprint,
                None,
                {
                    "database": self.settings.duckdb_path.name,
                    "schema": self.settings.duckdb_schema,
                    "relation": relation,
                },
            )
            validate_normalized_file(
                normalized_path,
                normalized_binding.content_fingerprint or "",
                canonical_schema,
            )
            serving_schema = self.serving.register_view(relation, normalized_path)
            self._checkpoint(checkpoint)
            view_created = True
            serving_binding = self.storage.ready_binding(
                serving_binding.id,
                None,
                technical_schema_fingerprint(serving_schema),
                {
                    "database": self.settings.duckdb_path.name,
                    "schema": self.settings.duckdb_schema,
                    "relation": relation,
                    "serving_schema": serving_schema,
                },
            )
            run = self.storage.transition_run(
                run.id,
                ProcessingStatus.VALIDATING,
                serving_binding_id=serving_binding.id,
                serving_schema_json=json.dumps(serving_schema, sort_keys=True),
            )
            if not self.serving.view_exists(relation):
                raise ProcessingValidationError("duckdb_view_missing", "DuckDB view was not created")
            self.serving.preview(relation, 1)
            self._checkpoint(checkpoint)
            if not schemas_compatible(canonical_schema, serving_schema):
                raise ProcessingValidationError("serving_schema_mismatch", "Serving schema is incompatible")
            return self.storage.transition_run(run.id, ProcessingStatus.COMPLETED)
        except ExecutionInterruptedError:
            current_binding = self.storage.get_binding(serving_binding.id) if serving_binding else None
            if view_created and (current_binding is None or current_binding.status != BindingStatus.READY):
                self.serving.drop_view(relation)
                if serving_binding is not None:
                    self.storage.force_fail_ready_binding(serving_binding.id)
            current = self.storage.get_run(run.id) or run
            if current.status in {ProcessingStatus.REGISTERING, ProcessingStatus.VALIDATING}:
                self.storage.transition_run(current.id, ProcessingStatus.PARTIAL)
            raise
        except ProcessingCancelledError:
            current_binding = self.storage.get_binding(serving_binding.id) if serving_binding else None
            if view_created and (current_binding is None or current_binding.status != BindingStatus.READY):
                self.serving.drop_view(relation)
                if serving_binding is not None:
                    self.storage.force_fail_ready_binding(serving_binding.id)
            current = self.storage.get_run(run.id) or run
            if current.status in {
                ProcessingStatus.REGISTERING,
                ProcessingStatus.VALIDATING,
                ProcessingStatus.PARTIAL,
            }:
                self.storage.transition_run(current.id, ProcessingStatus.CANCELLED)
            raise
        except Exception as exc:
            if view_created:
                self.serving.drop_view(relation)
            if serving_binding is not None:
                self.storage.force_fail_ready_binding(serving_binding.id)
            current = self.storage.get_run(run.id) or run
            if current.status in {ProcessingStatus.REGISTERING, ProcessingStatus.VALIDATING}:
                code = getattr(exc, "code", "duckdb_registration_failed")
                return self.storage.transition_run(
                    run.id,
                    ProcessingStatus.PARTIAL,
                    errors_json=json.dumps([*current.errors, {"code": code, "message": "DuckDB registration failed"}], sort_keys=True),
                )
            raise

    def get_run(self, run_id: str) -> ProcessingRun | None:
        return self.storage.get_run(run_id)

    def list_runs(self, limit: int = 20) -> list[ProcessingRun]:
        return self.storage.list_runs()[-limit:][::-1]

    def list_runs_for_version(self, version_id: str) -> list[ProcessingRun]:
        return self.storage.list_runs_for_version(version_id)

    def cancel(self, run_id: str) -> ProcessingRun:
        run = self.storage.get_run(run_id)
        if run is None:
            raise ProcessingServiceError("processing_run_not_found", "Processing run not found", 404, run_id)
        if run.status in {
            ProcessingStatus.COMPLETED,
            ProcessingStatus.FAILED,
            ProcessingStatus.CANCELLED,
        }:
            raise ProcessingServiceError("processing_not_cancellable", "Processing run is terminal", 409, run_id)
        return self.storage.transition_run(run.id, ProcessingStatus.CANCELLED)

    def fail_execution(self, run_id: str, code: str, message: str) -> ProcessingRun | None:
        run = self.storage.get_run(run_id)
        if run is None:
            return None
        if run.status not in {
            ProcessingStatus.COMPLETED,
            ProcessingStatus.FAILED,
            ProcessingStatus.CANCELLED,
        }:
            self.storage.force_run_status(
                run.id,
                ProcessingStatus.FAILED,
                {"code": code, "message": message},
            )
        return self.storage.get_run(run_id)

    def list_bindings(self, asset_id: str, version_id: str) -> list[StorageBinding] | None:
        if self.ingestion_storage.get_version(asset_id, version_id) is None:
            return None
        return self.storage.list_bindings(version_id)

    def data_preview(
        self,
        asset_id: str,
        version_id: str,
        limit: int,
    ) -> dict[str, object]:
        if limit < 1 or limit > self.settings.processing_preview_rows:
            raise ProcessingServiceError(
                "preview_limit_exceeded",
                f"Preview limit must be between 1 and {self.settings.processing_preview_rows}",
                400,
            )
        if self.ingestion_storage.get_version(asset_id, version_id) is None:
            raise ProcessingServiceError("asset_version_not_found", "Asset version not found", 404)
        bindings = self.storage.list_bindings(version_id, BindingRole.SERVING, BindingStatus.READY)
        binding = next((item for item in reversed(bindings) if item.backend_type == "duckdb"), None)
        if binding is None:
            raise ProcessingServiceError("serving_binding_missing", "Ready serving binding is unavailable", 409)
        relation = self._binding_relation(binding)
        if not self.serving.view_exists(relation):
            raise ProcessingServiceError("duckdb_view_missing", "DuckDB serving view is unavailable", 409)
        try:
            schema, rows = self.serving.preview(relation, limit)
        except DuckDBLockTimeout as exc:
            raise ProcessingServiceError(
                "duckdb_lock_timeout",
                "DuckDB is temporarily busy",
                503,
            ) from exc
        return {
            "asset_id": asset_id,
            "asset_version_id": version_id,
            "binding_id": binding.id,
            "schema": schema,
            "rows": rows,
            "limit": limit,
        }

    def reconcile(self, now: datetime | None = None) -> ProcessingReconciliationReport:
        report = ProcessingReconciliationReport()
        resolved_now = now or datetime.now(timezone.utc)
        recoverable_versions = {
            run.asset_version_id
            for run in self.storage.list_runs((ProcessingStatus.FAILED, ProcessingStatus.PARTIAL))
        }
        for version_id in recoverable_versions:
            version = self.ingestion_storage.get_version_by_id(version_id)
            if version is not None and version.status == "failed":
                self._recover_legacy_processing_failure(version)
        cutoff = resolved_now - timedelta(seconds=self.settings.processing_stale_run_seconds)
        active = self.storage.list_runs(
            (
                ProcessingStatus.CREATED,
                ProcessingStatus.NORMALIZING,
                ProcessingStatus.REGISTERING,
                ProcessingStatus.VALIDATING,
            )
        )
        for run in active:
            if run.updated_at > cutoff:
                continue
            report.stale_runs.append(run.id)
            binding = self.storage.get_binding(run.normalized_binding_id) if run.normalized_binding_id else None
            if binding is not None and binding.status == "ready" and self._normalized_valid(binding):
                self.storage.force_run_status(
                    run.id,
                    ProcessingStatus.PARTIAL,
                    {"code": "stale_run", "message": "Stale run can retry DuckDB registration"},
                )
                report.resumable_partial_runs.append(run.id)
            else:
                self.storage.force_run_status(
                    run.id,
                    ProcessingStatus.FAILED,
                    {"code": "stale_run", "message": "Stale run has no usable normalized output"},
                )
                report.failed_runs.append(run.id)

        for run in self.storage.list_runs((ProcessingStatus.PARTIAL,)):
            binding = self.storage.get_binding(run.normalized_binding_id) if run.normalized_binding_id else None
            if binding is not None and binding.status == "ready" and self._normalized_valid(binding):
                if run.id not in report.resumable_partial_runs:
                    report.resumable_partial_runs.append(run.id)

        normalized = self.storage.list_bindings(role=BindingRole.NORMALIZED, status=BindingStatus.READY)
        for binding in normalized:
            if not self._normalized_valid(binding):
                report.missing_normalized_bindings.append(binding.id)
                self.storage.force_fail_ready_binding(binding.id)
                for run in self.storage.list_runs():
                    if run.normalized_binding_id == binding.id:
                        self.storage.force_run_status(
                            run.id,
                            ProcessingStatus.FAILED,
                            {"code": "normalized_file_missing", "message": "Normalized output is unavailable"},
                        )
                        if run.id not in report.failed_runs:
                            report.failed_runs.append(run.id)

        serving_bindings = self.storage.list_bindings(role=BindingRole.SERVING, status=BindingStatus.READY)
        bound_relations: set[str] = set()
        for binding in serving_bindings:
            relation = self._binding_relation(binding)
            bound_relations.add(relation)
            if not self.serving.view_exists(relation):
                report.missing_serving_bindings.append(binding.id)
                self.storage.force_fail_ready_binding(binding.id)
                for run in self.storage.list_runs():
                    if run.serving_binding_id == binding.id and run.normalized_binding_id:
                        self.storage.force_run_status(
                            run.id,
                            ProcessingStatus.PARTIAL,
                            {"code": "duckdb_view_missing", "message": "Serving view is unavailable"},
                        )
                        report.resumable_partial_runs.append(run.id)

        referenced_normalized: set[Path] = set()
        for binding in self.storage.list_bindings(role=BindingRole.NORMALIZED):
            try:
                referenced_normalized.add(
                    self._path_from_reference(
                        binding.physical_location, self.normalized_dir, "normalized"
                    )
                )
            except ProcessingServiceError:
                pass
        for path in self.normalized_dir.iterdir():
            reference = f"normalized/{path.name}"
            if path.is_file() and path.resolve() not in referenced_normalized:
                report.orphan_normalized_files.append(reference)
                if path.name.startswith(".tmp-"):
                    path.unlink()
        report.orphan_duckdb_views = [
            relation for relation in self.serving.list_views() if relation not in bound_relations
        ]
        return report

    def _recover_legacy_processing_failure(self, version: AssetVersion) -> AssetVersion:
        runs = self.storage.list_runs_for_version(version.id)
        if not any(run.status in {ProcessingStatus.FAILED, ProcessingStatus.PARTIAL} for run in runs):
            return version
        raw_binding = self.ingestion_storage.get_binding(version.id)
        if raw_binding is None:
            return version
        try:
            raw_path = self._path_from_reference(raw_binding.physical_location, self.raw_dir, "raw")
            if not raw_path.is_file() or file_fingerprint(raw_path) != version.source_fingerprint:
                return version
        except (OSError, ProcessingServiceError):
            return version
        if self.ingestion_storage.restore_failed_version_with_ready_raw(version.id):
            recovered = self.ingestion_storage.get_version_by_id(version.id)
            return recovered or version
        return version

    def _assert_completed_outputs(self, run: ProcessingRun) -> None:
        if not run.normalized_binding_id or not run.serving_binding_id:
            raise ProcessingServiceError("completed_run_inconsistent", "Completed run lacks output bindings", 409, run.id)
        normalized = self.storage.get_binding(run.normalized_binding_id)
        serving = self.storage.get_binding(run.serving_binding_id)
        if normalized is None or serving is None or normalized.status != "ready" or serving.status != "ready":
            raise ProcessingServiceError("completed_run_inconsistent", "Completed run outputs are not ready", 409, run.id)
        relation = self._binding_relation(serving)
        if not self._normalized_valid(normalized) or not self.serving.view_exists(relation):
            raise ProcessingServiceError("completed_run_inconsistent", "Completed run outputs are unavailable", 409, run.id)
        serving_schema, _ = self.serving.preview(relation, 1)
        if not schemas_compatible(run.canonical_schema, serving_schema):
            raise ProcessingServiceError("completed_run_inconsistent", "Completed run schemas are incompatible", 409, run.id)

    def _normalized_valid(self, binding: StorageBinding) -> bool:
        try:
            path = self._path_from_reference(binding.physical_location, self.normalized_dir, "normalized")
            validate_normalized_file(
                path,
                binding.content_fingerprint or "",
                binding.metadata.get("canonical_schema", []),
            )
            return True
        except Exception:
            return False

    def _fail_run(
        self,
        run: ProcessingRun,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> ProcessingRun:
        current = self.storage.get_run(run.id) or run
        rejected = max(current.records_rejected, 1) if code == "strict_conversion_failed" else current.records_rejected
        error = {"code": code, "message": message, **(details or {})}
        return self.storage.transition_run(
            run.id,
            ProcessingStatus.FAILED,
            records_rejected=rejected,
            errors_json=json.dumps([*current.errors, error], sort_keys=True),
        )

    @staticmethod
    def _relation_name(asset_id: str, version_number: int, recipe_fingerprint: str) -> str:
        compact = asset_id.replace("-", "")
        if not compact.isalnum():
            raise ProcessingServiceError("unsafe_asset_identifier", "Asset identifier is invalid", 500)
        return f"asset_{compact}_v{version_number}_{recipe_fingerprint[:12]}"

    @staticmethod
    def _binding_relation(binding: StorageBinding) -> str:
        relation = binding.metadata.get("relation")
        if not isinstance(relation, str):
            raise ProcessingServiceError("invalid_serving_binding", "Serving relation identifier is missing", 500)
        return relation

    @staticmethod
    def _path_from_reference(reference: str, root: Path, prefix: str) -> Path:
        try:
            return resolve_storage_reference(reference, root, prefix)
        except StorageReferenceError as exc:
            raise ProcessingServiceError(
                "unsafe_storage_path", "Stored file reference is invalid", 500
            ) from exc

    @staticmethod
    def _promote(source: Path, destination: Path) -> None:
        if destination.exists():
            raise ProcessingServiceError("normalized_output_exists", "Normalized output already exists", 500)
        source.rename(destination)

    @staticmethod
    def _cleanup(*paths: Path | None) -> None:
        for path in paths:
            if path is not None and path.is_file():
                path.unlink()

    @staticmethod
    def _checkpoint(checkpoint: Callable[[], None] | None) -> None:
        if checkpoint is not None:
            checkpoint()
