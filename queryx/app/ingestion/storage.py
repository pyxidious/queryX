from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from queryx.app.ingestion.models import (
    AssetVersion,
    DataAsset,
    DataFormat,
    IngestionJob,
    IngestionStatus,
    InspectionResult,
    LineageEdge,
    StorageBinding,
)


_TRANSITIONS: dict[IngestionStatus, set[IngestionStatus]] = {
    IngestionStatus.CREATED: {IngestionStatus.ACQUIRING, IngestionStatus.CANCELLED, IngestionStatus.FAILED},
    IngestionStatus.ACQUIRING: {IngestionStatus.INSPECTING, IngestionStatus.CANCELLED, IngestionStatus.FAILED},
    IngestionStatus.INSPECTING: {IngestionStatus.READY, IngestionStatus.CANCELLED, IngestionStatus.FAILED},
    IngestionStatus.READY: {IngestionStatus.COMPLETED, IngestionStatus.PARTIAL, IngestionStatus.FAILED, IngestionStatus.CANCELLED},
    IngestionStatus.COMPLETED: set(),
    IngestionStatus.PARTIAL: set(),
    IngestionStatus.FAILED: set(),
    IngestionStatus.CANCELLED: set(),
}


class InvalidJobTransition(ValueError):
    pass


class IngestionStorage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS data_assets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, asset_kind TEXT NOT NULL,
                    description TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS asset_versions (
                    id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, version_number INTEGER NOT NULL,
                    source_fingerprint TEXT NOT NULL, schema_fingerprint TEXT,
                    recipe_fingerprint TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(asset_id, version_number),
                    FOREIGN KEY(asset_id) REFERENCES data_assets(id)
                );
                CREATE TABLE IF NOT EXISTS storage_bindings (
                    id TEXT PRIMARY KEY, asset_version_id TEXT NOT NULL, backend_type TEXT NOT NULL,
                    physical_location TEXT NOT NULL, format TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(backend_type, physical_location),
                    FOREIGN KEY(asset_version_id) REFERENCES asset_versions(id)
                );
                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, source_type TEXT NOT NULL,
                    original_filename TEXT NOT NULL, source_reference TEXT, target_backend TEXT NOT NULL,
                    bytes_received INTEGER NOT NULL DEFAULT 0, records_detected INTEGER,
                    records_loaded INTEGER, records_rejected INTEGER, warnings_json TEXT NOT NULL,
                    error_json TEXT, source_fingerprint TEXT, asset_id TEXT, asset_version_id TEXT,
                    inspection_json TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                    FOREIGN KEY(asset_id) REFERENCES data_assets(id),
                    FOREIGN KEY(asset_version_id) REFERENCES asset_versions(id)
                );
                CREATE TABLE IF NOT EXISTS lineage_edges (
                    id TEXT PRIMARY KEY, source_reference TEXT NOT NULL,
                    target_asset_version_id TEXT NOT NULL, operation TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(target_asset_version_id) REFERENCES asset_versions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_asset_versions_asset ON asset_versions(asset_id, version_number);
                """
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (4, _now().isoformat()),
            )

    def create_job(self, original_filename: str, source_type: str = "upload") -> IngestionJob:
        now = _now()
        job = IngestionJob(
            id=str(uuid4()), status=IngestionStatus.CREATED, source_type=source_type,
            original_filename=original_filename, target_backend="file", created_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ingestion_jobs (
                    id, status, source_type, original_filename, target_backend, bytes_received,
                    warnings_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (job.id, job.status, job.source_type, job.original_filename, job.target_backend, 0, "[]", now.isoformat()),
            )
        return job

    def transition_job(self, job_id: str, status: IngestionStatus, **updates: Any) -> IngestionJob:
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM ingestion_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = IngestionStatus(row["status"])
            if status not in _TRANSITIONS[current]:
                raise InvalidJobTransition(f"Cannot transition ingestion job from {current} to {status}")
            values: dict[str, Any] = {"status": status.value, **updates}
            if status == IngestionStatus.ACQUIRING:
                values.setdefault("started_at", _now().isoformat())
            if status in {IngestionStatus.READY, IngestionStatus.COMPLETED, IngestionStatus.PARTIAL, IngestionStatus.FAILED, IngestionStatus.CANCELLED}:
                values.setdefault("finished_at", _now().isoformat())
            allowed = {
                "status", "source_reference", "bytes_received", "records_detected", "records_loaded",
                "records_rejected", "warnings_json", "error_json", "source_fingerprint", "asset_id",
                "asset_version_id", "inspection_json", "started_at", "finished_at",
            }
            if not values.keys() <= allowed:
                raise ValueError("Unsupported ingestion job update")
            assignments = ", ".join(f"{key} = ?" for key in values)
            connection.execute(
                f"UPDATE ingestion_jobs SET {assignments} WHERE id = ?",
                (*values.values(), job_id),
            )
        job = self.get_job(job_id)
        assert job is not None
        return job

    def create_asset_for_job(
        self,
        job_id: str,
        name: str,
        source_reference: str,
        data_format: DataFormat,
        source_fingerprint: str,
        schema_fingerprint: str,
        recipe_fingerprint: str,
        inspection: InspectionResult,
    ) -> tuple[DataAsset, AssetVersion]:
        now = _now()
        asset_id, version_id, binding_id, lineage_id = (str(uuid4()) for _ in range(4))
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO data_assets VALUES (?, ?, ?, ?, ?, ?)",
                (asset_id, name, "file", None, now.isoformat(), now.isoformat()),
            )
            connection.execute(
                "INSERT INTO asset_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (version_id, asset_id, 1, source_fingerprint, schema_fingerprint, recipe_fingerprint, "ready", now.isoformat()),
            )
            connection.execute(
                "INSERT INTO storage_bindings VALUES (?, ?, ?, ?, ?, ?)",
                (binding_id, version_id, "file", source_reference, data_format.value, now.isoformat()),
            )
            connection.execute(
                "INSERT INTO lineage_edges VALUES (?, ?, ?, ?, ?)",
                (lineage_id, source_reference, version_id, "upload", now.isoformat()),
            )
            cursor = connection.execute(
                """UPDATE ingestion_jobs SET status = 'ready', source_reference = ?, records_detected = ?,
                    source_fingerprint = ?, asset_id = ?, asset_version_id = ?, inspection_json = ?, finished_at = ?
                    WHERE id = ? AND status = 'inspecting'""",
                (
                    source_reference, inspection.records_detected, source_fingerprint, asset_id, version_id,
                    _dumps(inspection.model_dump(mode="json")), now.isoformat(), job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise InvalidJobTransition("Job must be inspecting before asset creation")
        asset = self.get_asset(asset_id)
        assert asset is not None and asset.versions
        return asset, asset.versions[0]

    def get_job(self, job_id: str) -> IngestionJob | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row is not None else None

    def list_assets(self) -> list[DataAsset]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM data_assets ORDER BY created_at DESC, id").fetchall()
            return [self._row_to_asset(connection, row) for row in rows]

    def get_asset(self, asset_id: str) -> DataAsset | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM data_assets WHERE id = ?", (asset_id,)).fetchone()
            return self._row_to_asset(connection, row) if row is not None else None

    def get_lineage(self, asset_version_id: str) -> list[LineageEdge]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM lineage_edges WHERE target_asset_version_id = ? ORDER BY created_at", (asset_version_id,)
            ).fetchall()
        return [LineageEdge(**dict(row)) for row in rows]

    def _row_to_asset(self, connection: sqlite3.Connection, row: sqlite3.Row) -> DataAsset:
        version_rows = connection.execute(
            "SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY version_number DESC", (row["id"],)
        ).fetchall()
        versions: list[AssetVersion] = []
        for version_row in version_rows:
            binding_rows = connection.execute(
                "SELECT * FROM storage_bindings WHERE asset_version_id = ? ORDER BY created_at", (version_row["id"],)
            ).fetchall()
            versions.append(AssetVersion(**dict(version_row), storage_bindings=[StorageBinding(**dict(item)) for item in binding_rows]))
        return DataAsset(**dict(row), versions=versions)

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> IngestionJob:
        values = dict(row)
        values["warnings"] = _loads(values.pop("warnings_json"), [])
        values["error"] = _loads(values.pop("error_json"), None)
        inspection = _loads(values.pop("inspection_json"), None)
        values["inspection"] = InspectionResult.model_validate(inspection) if inspection else None
        return IngestionJob.model_validate(values)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value is not None else default
