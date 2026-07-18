from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from queryx.app.acquisition.models import (
    AcquisitionFile,
    AcquisitionFileStatus,
    AcquisitionRun,
    AcquisitionStatus,
    DatasetManifest,
    FileSelection,
)
from queryx.app.ingestion.storage import IngestionStorage


class AcquisitionStorage:
    _TRANSITIONS = {
        AcquisitionStatus.CREATED: {AcquisitionStatus.INSPECTING, AcquisitionStatus.FAILED, AcquisitionStatus.CANCELLED},
        AcquisitionStatus.INSPECTING: {
            AcquisitionStatus.AWAITING_SELECTION, AcquisitionStatus.FAILED, AcquisitionStatus.CANCELLED
        },
        AcquisitionStatus.AWAITING_SELECTION: {
            AcquisitionStatus.DOWNLOADING, AcquisitionStatus.FAILED, AcquisitionStatus.CANCELLED
        },
        AcquisitionStatus.DOWNLOADING: {
            AcquisitionStatus.AWAITING_INGESTION, AcquisitionStatus.FAILED, AcquisitionStatus.CANCELLED
        },
        AcquisitionStatus.AWAITING_INGESTION: {
            AcquisitionStatus.COMPLETED, AcquisitionStatus.PARTIAL,
            AcquisitionStatus.FAILED, AcquisitionStatus.CANCELLED
        },
        AcquisitionStatus.COMPLETED: set(),
        AcquisitionStatus.PARTIAL: set(),
        AcquisitionStatus.FAILED: set(),
        AcquisitionStatus.CANCELLED: set(),
    }
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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS acquisition_runs (
                    id TEXT PRIMARY KEY, provider TEXT NOT NULL, dataset_reference TEXT NOT NULL,
                    requested_version TEXT NOT NULL, resolved_version TEXT, title TEXT, license_name TEXT,
                    request_fingerprint TEXT, status TEXT NOT NULL, files_total INTEGER NOT NULL DEFAULT 0,
                    files_selected INTEGER NOT NULL DEFAULT 0, files_downloaded INTEGER NOT NULL DEFAULT 0,
                    files_ready INTEGER NOT NULL DEFAULT 0, files_failed INTEGER NOT NULL DEFAULT 0,
                    warnings_json TEXT NOT NULL DEFAULT '[]', errors_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL, started_at TEXT, updated_at TEXT NOT NULL, finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_acquisition_runs_reference
                    ON acquisition_runs(provider, dataset_reference, resolved_version, created_at);
                CREATE INDEX IF NOT EXISTS idx_acquisition_runs_status
                    ON acquisition_runs(status, updated_at);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_acquisition_request_completed
                    ON acquisition_runs(request_fingerprint)
                    WHERE request_fingerprint IS NOT NULL AND status = 'completed';

                CREATE TABLE IF NOT EXISTS acquisition_files (
                    id TEXT PRIMARY KEY, acquisition_run_id TEXT NOT NULL,
                    provider_file_reference TEXT NOT NULL, display_name TEXT NOT NULL,
                    size_bytes INTEGER, format TEXT NOT NULL, selected INTEGER NOT NULL DEFAULT 0,
                    logical_name TEXT, target_asset_id TEXT, status TEXT NOT NULL,
                    ingestion_job_id TEXT, asset_id TEXT, asset_version_id TEXT,
                    content_fingerprint TEXT, warning_json TEXT, error_json TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(acquisition_run_id) REFERENCES acquisition_runs(id),
                    UNIQUE(acquisition_run_id, provider_file_reference)
                );
                CREATE INDEX IF NOT EXISTS idx_acquisition_files_run
                    ON acquisition_files(acquisition_run_id, selected, status);
                CREATE INDEX IF NOT EXISTS idx_acquisition_files_job ON acquisition_files(ingestion_job_id);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (11, _now().isoformat()),
            )

    def create_run(self, dataset: str, version: str) -> AcquisitionRun:
        now = _now()
        run_id = str(uuid4())
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO acquisition_runs (
                    id, provider, dataset_reference, requested_version, status,
                    created_at, updated_at
                ) VALUES (?, 'kaggle', ?, ?, 'created', ?, ?)""",
                (run_id, dataset, version, now.isoformat(), now.isoformat()),
            )
        run = self.get_run(run_id)
        assert run is not None
        return run

    def get_run(self, run_id: str) -> AcquisitionRun | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM acquisition_runs WHERE id = ?", (run_id,)).fetchone()
        return self._run(row) if row else None

    def list_runs(self, statuses: tuple[AcquisitionStatus, ...] | None = None) -> list[AcquisitionRun]:
        with self._connect() as connection:
            if statuses:
                marks = ",".join("?" for _ in statuses)
                rows = connection.execute(
                    f"SELECT * FROM acquisition_runs WHERE status IN ({marks}) ORDER BY created_at",
                    tuple(item.value for item in statuses),
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM acquisition_runs ORDER BY created_at DESC").fetchall()
        return [self._run(row) for row in rows]

    def active_inspection(self, dataset: str, version: str) -> AcquisitionRun | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM acquisition_runs WHERE provider = 'kaggle'
                   AND dataset_reference = ? AND requested_version = ?
                   AND status IN ('created', 'inspecting') ORDER BY created_at DESC LIMIT 1""",
                (dataset, version),
            ).fetchone()
        return self._run(row) if row else None

    def transition(self, run_id: str, status: AcquisitionStatus, **fields: Any) -> AcquisitionRun:
        current = self.get_run(run_id)
        if current is None:
            raise KeyError(run_id)
        if status != current.status and status not in self._TRANSITIONS[current.status]:
            raise ValueError(f"Invalid acquisition transition {current.status} -> {status}")
        now = _now()
        assignments = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status.value, now.isoformat()]
        allowed = {
            "resolved_version", "title", "license_name", "request_fingerprint", "files_total",
            "files_selected", "files_downloaded", "files_ready", "files_failed",
        }
        for key, value in fields.items():
            if key not in allowed:
                continue
            assignments.append(f"{key} = ?")
            values.append(value)
        if status in {AcquisitionStatus.INSPECTING, AcquisitionStatus.DOWNLOADING}:
            assignments.append("started_at = COALESCE(started_at, ?)")
            values.append(now.isoformat())
        if status in {AcquisitionStatus.COMPLETED, AcquisitionStatus.PARTIAL, AcquisitionStatus.FAILED, AcquisitionStatus.CANCELLED}:
            assignments.append("finished_at = ?")
            values.append(now.isoformat())
        values.append(run_id)
        with self._connect() as connection:
            connection.execute(f"UPDATE acquisition_runs SET {', '.join(assignments)} WHERE id = ?", values)
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def add_error(self, run_id: str, error: dict[str, Any]) -> None:
        run = self.get_run(run_id)
        if run is None:
            return
        errors = [*run.errors, error][:100]
        with self._connect() as connection:
            connection.execute(
                "UPDATE acquisition_runs SET errors_json = ?, updated_at = ? WHERE id = ?",
                (_dump(errors), _now().isoformat(), run_id),
            )

    def save_manifest(self, run_id: str, manifest: DatasetManifest, allowed: set[str]) -> AcquisitionRun:
        now = _now().isoformat()
        from queryx.app.acquisition.validation import file_format

        with self._connect() as connection:
            for item in manifest.files:
                fmt = file_format(item.name, allowed)
                connection.execute(
                    """INSERT OR IGNORE INTO acquisition_files (
                        id, acquisition_run_id, provider_file_reference, display_name, size_bytes,
                        format, selected, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                    (
                        str(uuid4()), run_id, item.reference, item.name, item.size_bytes,
                        fmt or "unsupported", "discovered" if fmt else "unsupported", now, now,
                    ),
                )
        return self.transition(
            run_id,
            AcquisitionStatus.AWAITING_SELECTION,
            resolved_version=manifest.resolved_version,
            title=manifest.title,
            license_name=manifest.license_name,
            files_total=len(manifest.files),
        )

    def list_files(self, run_id: str) -> list[AcquisitionFile]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM acquisition_files WHERE acquisition_run_id = ? ORDER BY display_name, id",
                (run_id,),
            ).fetchall()
        return [self._file(row) for row in rows]

    def get_file(self, file_id: str) -> AcquisitionFile | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM acquisition_files WHERE id = ?", (file_id,)).fetchone()
        return self._file(row) if row else None

    def select_files(self, run_id: str, selections: list[FileSelection], fingerprint: str) -> AcquisitionRun:
        now = _now().isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for selection in selections:
                row = connection.execute(
                    "SELECT * FROM acquisition_files WHERE id = ? AND acquisition_run_id = ?",
                    (selection.file_id, run_id),
                ).fetchone()
                if row is None:
                    raise KeyError(selection.file_id)
                if row["status"] != "discovered":
                    raise ValueError(selection.file_id)
                connection.execute(
                    """UPDATE acquisition_files SET selected = 1, status = 'selected', logical_name = ?,
                       target_asset_id = ?, updated_at = ? WHERE id = ?""",
                    (selection.logical_name, selection.target_asset_id, now, selection.file_id),
                )
            connection.execute(
                """UPDATE acquisition_runs SET status = 'downloading', request_fingerprint = ?,
                   files_selected = ?, started_at = COALESCE(started_at, ?), updated_at = ? WHERE id = ?""",
                (fingerprint, len(selections), now, now, run_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        run = self.get_run(run_id)
        assert run is not None
        return run

    def update_file(self, file_id: str, status: AcquisitionFileStatus, **fields: Any) -> AcquisitionFile:
        assignments = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status.value, _now().isoformat()]
        json_fields = {"warning": "warning_json", "error": "error_json"}
        allowed = {"ingestion_job_id", "asset_id", "asset_version_id", "content_fingerprint", "size_bytes"}
        for key, value in fields.items():
            if key in json_fields:
                assignments.append(f"{json_fields[key]} = ?")
                values.append(_dump(value) if value is not None else None)
            elif key in allowed:
                assignments.append(f"{key} = ?")
                values.append(value)
        values.append(file_id)
        with self._connect() as connection:
            connection.execute(f"UPDATE acquisition_files SET {', '.join(assignments)} WHERE id = ?", values)
        item = self.get_file(file_id)
        if item is None:
            raise KeyError(file_id)
        return item

    def completed_for_fingerprint(self, fingerprint: str) -> AcquisitionRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM acquisition_runs WHERE request_fingerprint = ? AND status = 'completed' LIMIT 1",
                (fingerprint,),
            ).fetchone()
        return self._run(row) if row else None

    def active_for_fingerprint(self, fingerprint: str) -> AcquisitionRun | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM acquisition_runs WHERE request_fingerprint = ?
                   AND status IN ('downloading', 'awaiting_ingestion') LIMIT 1""",
                (fingerprint,),
            ).fetchone()
        return self._run(row) if row else None

    @staticmethod
    def _run(row: sqlite3.Row) -> AcquisitionRun:
        data = dict(row)
        data["warnings"] = json.loads(data.pop("warnings_json") or "[]")
        data["errors"] = json.loads(data.pop("errors_json") or "[]")
        return AcquisitionRun(**data)

    @staticmethod
    def _file(row: sqlite3.Row) -> AcquisitionFile:
        data = dict(row)
        data["selected"] = bool(data["selected"])
        data["warning"] = json.loads(data.pop("warning_json")) if data.get("warning_json") else None
        data["error"] = json.loads(data.pop("error_json")) if data.get("error_json") else None
        return AcquisitionFile(**data)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
