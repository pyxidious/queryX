from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from queryx.app.ingestion.models import (
    BackendType,
    BindingRole,
    BindingStatus,
    DataFormat,
    StorageBinding,
)
from queryx.app.ingestion.storage import IngestionStorage
from queryx.app.processing.models import (
    ProcessingOperation,
    ProcessingRun,
    ProcessingStatus,
)


_TRANSITIONS: dict[ProcessingStatus, set[ProcessingStatus]] = {
    ProcessingStatus.CREATED: {ProcessingStatus.NORMALIZING, ProcessingStatus.FAILED, ProcessingStatus.CANCELLED},
    ProcessingStatus.NORMALIZING: {ProcessingStatus.REGISTERING, ProcessingStatus.FAILED, ProcessingStatus.CANCELLED},
    ProcessingStatus.REGISTERING: {
        ProcessingStatus.VALIDATING,
        ProcessingStatus.PARTIAL,
        ProcessingStatus.FAILED,
        ProcessingStatus.CANCELLED,
    },
    ProcessingStatus.VALIDATING: {
        ProcessingStatus.COMPLETED,
        ProcessingStatus.PARTIAL,
        ProcessingStatus.FAILED,
    },
    ProcessingStatus.PARTIAL: {ProcessingStatus.REGISTERING, ProcessingStatus.CANCELLED},
    ProcessingStatus.COMPLETED: set(),
    ProcessingStatus.FAILED: set(),
    ProcessingStatus.CANCELLED: set(),
}


class InvalidProcessingTransition(ValueError):
    pass


class ProcessingInProgressError(RuntimeError):
    pass


class ProcessingStorage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        IngestionStorage(db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS processing_runs (
                    id TEXT PRIMARY KEY,
                    asset_version_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_binding_id TEXT NOT NULL,
                    recipe_name TEXT NOT NULL,
                    recipe_version TEXT NOT NULL,
                    recipe_fingerprint TEXT NOT NULL,
                    normalized_binding_id TEXT,
                    serving_binding_id TEXT,
                    records_read INTEGER NOT NULL DEFAULT 0,
                    records_written INTEGER NOT NULL DEFAULT 0,
                    records_rejected INTEGER NOT NULL DEFAULT 0,
                    bytes_written INTEGER NOT NULL DEFAULT 0,
                    observed_schema_json TEXT NOT NULL,
                    canonical_schema_json TEXT NOT NULL,
                    serving_schema_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    errors_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(asset_version_id) REFERENCES asset_versions(id),
                    FOREIGN KEY(input_binding_id) REFERENCES storage_bindings(id),
                    FOREIGN KEY(normalized_binding_id) REFERENCES storage_bindings(id),
                    FOREIGN KEY(serving_binding_id) REFERENCES storage_bindings(id)
                )
                """
            )
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_processing_runs_version
                    ON processing_runs(asset_version_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_processing_runs_status_updated
                    ON processing_runs(status, updated_at);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_processing_run_equivalent
                    ON processing_runs(asset_version_id, operation, recipe_fingerprint)
                    WHERE status IN ('created', 'normalizing', 'registering', 'validating', 'partial', 'completed');
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (7, _now().isoformat()),
            )

    def create_or_reuse_run(
        self,
        asset_version_id: str,
        input_binding_id: str,
        recipe_name: str,
        recipe_version: str,
        recipe_fingerprint: str,
        observed_schema: list[dict[str, Any]],
    ) -> tuple[ProcessingRun, str]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT * FROM processing_runs
                   WHERE asset_version_id = ? AND operation = ? AND recipe_fingerprint = ?
                     AND status IN ('created', 'normalizing', 'registering', 'validating', 'partial', 'completed')
                   ORDER BY created_at DESC LIMIT 1""",
                (
                    asset_version_id,
                    ProcessingOperation.NORMALIZE_AND_REGISTER.value,
                    recipe_fingerprint,
                ),
            ).fetchone()
            if existing is not None:
                status = ProcessingStatus(existing["status"])
                if status == ProcessingStatus.COMPLETED:
                    connection.commit()
                    return self._row_to_run(existing).model_copy(update={"reused": True}), "completed"
                if status == ProcessingStatus.PARTIAL:
                    connection.commit()
                    return self._row_to_run(existing), "partial"
                raise ProcessingInProgressError("An equivalent processing run is active")

            now = _now()
            run = ProcessingRun(
                id=str(uuid4()),
                asset_version_id=asset_version_id,
                operation=ProcessingOperation.NORMALIZE_AND_REGISTER,
                status=ProcessingStatus.CREATED,
                input_binding_id=input_binding_id,
                recipe_name=recipe_name,
                recipe_version=recipe_version,
                recipe_fingerprint=recipe_fingerprint,
                observed_schema=observed_schema,
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                """INSERT INTO processing_runs (
                    id, asset_version_id, operation, status, input_binding_id,
                    recipe_name, recipe_version, recipe_fingerprint,
                    records_read, records_written, records_rejected, bytes_written,
                    observed_schema_json, canonical_schema_json, serving_schema_json,
                    warnings_json, errors_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, '[]', '[]', '[]', '[]', ?, ?)""",
                (
                    run.id,
                    asset_version_id,
                    run.operation.value,
                    run.status.value,
                    input_binding_id,
                    recipe_name,
                    recipe_version,
                    recipe_fingerprint,
                    _dumps(observed_schema),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.commit()
            return run, "new"
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def transition_run(self, run_id: str, status: ProcessingStatus, **updates: Any) -> ProcessingRun:
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM processing_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            current = ProcessingStatus(row["status"])
            if status not in _TRANSITIONS[current]:
                raise InvalidProcessingTransition(f"Cannot transition processing run from {current} to {status}")
            now = _now().isoformat()
            values: dict[str, Any] = {"status": status.value, "updated_at": now, **updates}
            if status == ProcessingStatus.NORMALIZING:
                values.setdefault("started_at", now)
            if status in {
                ProcessingStatus.COMPLETED,
                ProcessingStatus.PARTIAL,
                ProcessingStatus.FAILED,
                ProcessingStatus.CANCELLED,
            }:
                values.setdefault("finished_at", now)
            allowed = {
                "status",
                "normalized_binding_id",
                "serving_binding_id",
                "records_read",
                "records_written",
                "records_rejected",
                "bytes_written",
                "canonical_schema_json",
                "serving_schema_json",
                "warnings_json",
                "errors_json",
                "started_at",
                "updated_at",
                "finished_at",
            }
            if not values.keys() <= allowed:
                raise ValueError("Unsupported processing run update")
            assignments = ", ".join(f"{key} = ?" for key in values)
            cursor = connection.execute(
                f"UPDATE processing_runs SET {assignments} WHERE id = ? AND status = ?",
                (*values.values(), run_id, current.value),
            )
            if cursor.rowcount != 1:
                raise InvalidProcessingTransition("Processing run changed concurrently")
        run = self.get_run(run_id)
        assert run is not None
        return run

    def get_run(self, run_id: str) -> ProcessingRun | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM processing_runs WHERE id = ?", (run_id,)).fetchone()
        return self._row_to_run(row) if row is not None else None

    def list_runs(self, statuses: tuple[ProcessingStatus, ...] | None = None) -> list[ProcessingRun]:
        with self._connect() as connection:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = connection.execute(
                    f"SELECT * FROM processing_runs WHERE status IN ({placeholders}) ORDER BY created_at",
                    tuple(status.value for status in statuses),
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM processing_runs ORDER BY created_at").fetchall()
        return [self._row_to_run(row) for row in rows]

    def prepare_binding(
        self,
        asset_version_id: str,
        role: BindingRole,
        backend: BackendType,
        physical_location: str,
        recipe_fingerprint: str,
        data_format: DataFormat | None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[StorageBinding, bool]:
        now = _now().isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM storage_bindings
                   WHERE asset_version_id = ? AND binding_role = ? AND backend_type = ?
                     AND recipe_fingerprint = ? AND status IN ('preparing', 'ready')
                   ORDER BY created_at DESC LIMIT 1""",
                (asset_version_id, role.value, backend.value, recipe_fingerprint),
            ).fetchone()
            if row is not None:
                connection.commit()
                return self._row_to_binding(row), row["status"] == BindingStatus.READY.value
            failed = connection.execute(
                """SELECT * FROM storage_bindings
                   WHERE asset_version_id = ? AND binding_role = ? AND backend_type = ?
                     AND recipe_fingerprint = ? AND physical_location = ? AND status = 'failed'
                   ORDER BY created_at DESC LIMIT 1""",
                (asset_version_id, role.value, backend.value, recipe_fingerprint, physical_location),
            ).fetchone()
            if failed is not None:
                connection.execute(
                    """UPDATE storage_bindings SET status = 'preparing', content_fingerprint = NULL,
                       schema_fingerprint = NULL, metadata_json = ?, updated_at = ? WHERE id = ?""",
                    (_dumps(metadata or {}), now, failed["id"]),
                )
                connection.commit()
                binding = self.get_binding(failed["id"])
                assert binding is not None
                return binding, False
            binding_id = str(uuid4())
            connection.execute(
                """INSERT INTO storage_bindings (
                    id, asset_version_id, backend_type, binding_role, status, physical_location,
                    format, recipe_fingerprint, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'preparing', ?, ?, ?, ?, ?, ?)""",
                (
                    binding_id,
                    asset_version_id,
                    backend.value,
                    role.value,
                    physical_location,
                    data_format.value if data_format else None,
                    recipe_fingerprint,
                    _dumps(metadata or {}),
                    now,
                    now,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        binding = self.get_binding(binding_id)
        assert binding is not None
        return binding, False

    def ready_binding(
        self,
        binding_id: str,
        content_fingerprint: str | None,
        schema_fingerprint: str,
        metadata: dict[str, Any],
    ) -> StorageBinding:
        now = _now().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE storage_bindings SET status = 'ready', content_fingerprint = ?,
                   schema_fingerprint = ?, metadata_json = ?, updated_at = ?
                   WHERE id = ? AND status = 'preparing'""",
                (content_fingerprint, schema_fingerprint, _dumps(metadata), now, binding_id),
            )
            if cursor.rowcount != 1:
                existing = connection.execute("SELECT status FROM storage_bindings WHERE id = ?", (binding_id,)).fetchone()
                if existing is None or existing["status"] != "ready":
                    raise InvalidProcessingTransition("Binding is not preparing")
        binding = self.get_binding(binding_id)
        assert binding is not None
        return binding

    def fail_binding(self, binding_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE storage_bindings SET status = 'failed', updated_at = ? WHERE id = ? AND status <> 'ready'",
                (_now().isoformat(), binding_id),
            )

    def force_fail_ready_binding(self, binding_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE storage_bindings SET status = 'failed', updated_at = ? WHERE id = ?",
                (_now().isoformat(), binding_id),
            )

    def get_binding(self, binding_id: str) -> StorageBinding | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM storage_bindings WHERE id = ?", (binding_id,)).fetchone()
        return self._row_to_binding(row) if row is not None else None

    def list_bindings(
        self,
        asset_version_id: str | None = None,
        role: BindingRole | None = None,
        status: BindingStatus | None = None,
    ) -> list[StorageBinding]:
        clauses: list[str] = []
        params: list[str] = []
        if asset_version_id is not None:
            clauses.append("asset_version_id = ?")
            params.append(asset_version_id)
        if role is not None:
            clauses.append("binding_role = ?")
            params.append(role.value)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM storage_bindings {where} ORDER BY created_at", tuple(params)
            ).fetchall()
        return [self._row_to_binding(row) for row in rows]

    def force_run_status(
        self,
        run_id: str,
        status: ProcessingStatus,
        error: dict[str, Any] | None = None,
    ) -> None:
        now = _now().isoformat()
        with self._connect() as connection:
            row = connection.execute("SELECT errors_json FROM processing_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                return
            errors = _loads(row["errors_json"], [])
            if error:
                errors.append(error)
            connection.execute(
                """UPDATE processing_runs SET status = ?, errors_json = ?, updated_at = ?, finished_at = ?
                   WHERE id = ?""",
                (status.value, _dumps(errors), now, now, run_id),
            )

    @staticmethod
    def _row_to_binding(row: sqlite3.Row) -> StorageBinding:
        values = dict(row)
        values["metadata"] = _loads(values.pop("metadata_json", None), {})
        return StorageBinding.model_validate(values)

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> ProcessingRun:
        values = dict(row)
        values["observed_schema"] = _loads(values.pop("observed_schema_json"), [])
        values["canonical_schema"] = _loads(values.pop("canonical_schema_json"), [])
        values["serving_schema"] = _loads(values.pop("serving_schema_json"), [])
        values["warnings"] = _loads(values.pop("warnings_json"), [])
        values["errors"] = _loads(values.pop("errors_json"), [])
        return ProcessingRun.model_validate(values)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value is not None else default
