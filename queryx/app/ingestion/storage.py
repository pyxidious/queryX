from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from queryx.app.ingestion.catalog_adapter import compare_technical_metadata
from queryx.app.ingestion.models import (
    AssetSchemaDiff,
    AssetVersion,
    DataAsset,
    DataFormat,
    DatasetProvenance,
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
    IngestionStatus.READY: {
        IngestionStatus.COMPLETED,
        IngestionStatus.PARTIAL,
        IngestionStatus.FAILED,
        IngestionStatus.CANCELLED,
    },
    IngestionStatus.COMPLETED: set(),
    IngestionStatus.PARTIAL: set(),
    IngestionStatus.FAILED: set(),
    IngestionStatus.CANCELLED: set(),
}


class InvalidJobTransition(ValueError):
    pass


class AssetNotFoundError(KeyError):
    pass


class IngestionInProgressError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedVersion:
    asset: DataAsset
    version: AssetVersion
    raw_reference: str
    data_format: DataFormat
    reused: bool = False
    created_asset: bool = False


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
                    technical_metadata_json TEXT NOT NULL DEFAULT '{}',
                    inspection_json TEXT, drift_json TEXT, planned_location TEXT, format TEXT,
                    UNIQUE(asset_id, version_number),
                    FOREIGN KEY(asset_id) REFERENCES data_assets(id)
                );
                CREATE TABLE IF NOT EXISTS storage_bindings (
                    id TEXT PRIMARY KEY, asset_version_id TEXT NOT NULL, backend_type TEXT NOT NULL,
                    binding_role TEXT NOT NULL DEFAULT 'raw', status TEXT NOT NULL DEFAULT 'ready',
                    physical_location TEXT NOT NULL, format TEXT, recipe_fingerprint TEXT,
                    content_fingerprint TEXT, schema_fingerprint TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT,
                    UNIQUE(backend_type, physical_location),
                    FOREIGN KEY(asset_version_id) REFERENCES asset_versions(id)
                );
                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, source_type TEXT NOT NULL,
                    original_filename TEXT NOT NULL, logical_name TEXT, source_reference TEXT, target_backend TEXT NOT NULL,
                    bytes_received INTEGER NOT NULL DEFAULT 0, records_detected INTEGER,
                    records_loaded INTEGER, records_rejected INTEGER, warnings_json TEXT NOT NULL,
                    error_json TEXT, source_fingerprint TEXT, asset_id TEXT, asset_version_id TEXT,
                    requested_asset_id TEXT, inspection_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    heartbeat_at TEXT, started_at TEXT, finished_at TEXT,
                    provenance_json TEXT NOT NULL DEFAULT '{"source_provider":"manual"}',
                    FOREIGN KEY(asset_id) REFERENCES data_assets(id),
                    FOREIGN KEY(asset_version_id) REFERENCES asset_versions(id)
                );
                CREATE TABLE IF NOT EXISTS lineage_edges (
                    id TEXT PRIMARY KEY, source_reference TEXT NOT NULL,
                    target_asset_version_id TEXT NOT NULL, operation TEXT NOT NULL, created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(target_asset_version_id) REFERENCES asset_versions(id)
                );
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
                );
                """
            )
            for column, definition in (
                ("technical_metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("inspection_json", "TEXT"),
                ("drift_json", "TEXT"),
                ("planned_location", "TEXT"),
                ("format", "TEXT"),
            ):
                self._ensure_column(connection, "asset_versions", column, definition)
            self._ensure_column(connection, "ingestion_jobs", "updated_at", "TEXT")
            self._ensure_column(connection, "ingestion_jobs", "heartbeat_at", "TEXT")
            self._ensure_column(connection, "ingestion_jobs", "requested_asset_id", "TEXT")
            self._ensure_column(connection, "ingestion_jobs", "logical_name", "TEXT")
            self._ensure_column(
                connection,
                "ingestion_jobs",
                "provenance_json",
                "TEXT NOT NULL DEFAULT '{\"source_provider\":\"manual\"}'",
            )
            self._ensure_column(connection, "lineage_edges", "metadata_json", "TEXT NOT NULL DEFAULT '{}'")
            for column, definition in (
                ("binding_role", "TEXT NOT NULL DEFAULT 'raw'"),
                ("status", "TEXT NOT NULL DEFAULT 'ready'"),
                ("recipe_fingerprint", "TEXT"),
                ("content_fingerprint", "TEXT"),
                ("schema_fingerprint", "TEXT"),
                ("metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("updated_at", "TEXT"),
            ):
                self._ensure_column(connection, "storage_bindings", column, definition)
            connection.execute("UPDATE ingestion_jobs SET updated_at = COALESCE(updated_at, created_at)")
            connection.execute(
                """UPDATE ingestion_jobs
                   SET provenance_json = COALESCE(provenance_json, '{"source_provider":"manual"}')"""
            )
            connection.execute(
                """UPDATE storage_bindings SET binding_role = COALESCE(binding_role, 'raw'),
                   status = COALESCE(status, 'ready'), updated_at = COALESCE(updated_at, created_at),
                   metadata_json = COALESCE(metadata_json, '{}')"""
            )
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status_updated
                    ON ingestion_jobs(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_asset_versions_asset
                    ON asset_versions(asset_id, version_number);
                CREATE INDEX IF NOT EXISTS idx_asset_versions_fingerprint
                    ON asset_versions(source_fingerprint, status);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_version_idempotency
                    ON asset_versions(asset_id, source_fingerprint, recipe_fingerprint)
                    WHERE status IN ('preparing', 'ready');
                CREATE INDEX IF NOT EXISTS idx_storage_bindings_version_role
                    ON storage_bindings(asset_version_id, binding_role, status);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_storage_binding_equivalent
                    ON storage_bindings(
                        asset_version_id, binding_role, backend_type, COALESCE(recipe_fingerprint, '')
                    ) WHERE status IN ('preparing', 'ready');
                CREATE INDEX IF NOT EXISTS idx_lineage_edges_target
                    ON lineage_edges(target_asset_version_id, operation);
                """
            )
            applied_at = _now().isoformat()
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (4, applied_at),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (5, applied_at),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (6, applied_at),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (10, applied_at),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (11, applied_at),
            )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def create_job(
        self,
        original_filename: str,
        source_type: str = "upload",
        asset_id: str | None = None,
        logical_name: str | None = None,
        provenance: DatasetProvenance | None = None,
    ) -> IngestionJob:
        now = _now()
        resolved_provenance = provenance or DatasetProvenance()
        job = IngestionJob(
            id=str(uuid4()),
            status=IngestionStatus.CREATED,
            source_type=source_type,
            original_filename=original_filename,
            logical_name=logical_name,
            target_backend="file",
            asset_id=None,
            requested_asset_id=asset_id,
            created_at=now,
            updated_at=now,
            provenance=resolved_provenance,
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ingestion_jobs (
                    id, status, source_type, original_filename, logical_name, target_backend, bytes_received,
                    warnings_json, requested_asset_id, created_at, updated_at, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job.id,
                    job.status,
                    job.source_type,
                    job.original_filename,
                    logical_name,
                    job.target_backend,
                    0,
                    "[]",
                    asset_id,
                    now.isoformat(),
                    now.isoformat(),
                    _dumps(resolved_provenance.model_dump(mode="json", exclude_none=True)),
                ),
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
            values: dict[str, Any] = {"status": status.value, **updates, "updated_at": _now().isoformat()}
            if status == IngestionStatus.ACQUIRING:
                values.setdefault("started_at", _now().isoformat())
            if status in {
                IngestionStatus.READY,
                IngestionStatus.COMPLETED,
                IngestionStatus.PARTIAL,
                IngestionStatus.FAILED,
                IngestionStatus.CANCELLED,
            }:
                values.setdefault("finished_at", _now().isoformat())
            allowed = {
                "status",
                "source_reference",
                "bytes_received",
                "records_detected",
                "records_loaded",
                "records_rejected",
                "warnings_json",
                "error_json",
                "source_fingerprint",
                "asset_id",
                "asset_version_id",
                "inspection_json",
                "updated_at",
                "heartbeat_at",
                "started_at",
                "finished_at",
            }
            if not values.keys() <= allowed:
                raise ValueError("Unsupported ingestion job update")
            assignments = ", ".join(f"{key} = ?" for key in values)
            cursor = connection.execute(
                f"UPDATE ingestion_jobs SET {assignments} WHERE id = ? AND status = ?",
                (*values.values(), job_id, current.value),
            )
            if cursor.rowcount != 1:
                raise InvalidJobTransition("Ingestion job changed concurrently")
        job = self.get_job(job_id)
        assert job is not None
        return job

    def update_job(self, job_id: str, **updates: Any) -> IngestionJob:
        allowed = {"bytes_received", "heartbeat_at"}
        if not updates.keys() <= allowed:
            raise ValueError("Unsupported ingestion job update")
        values = {**updates, "updated_at": _now().isoformat()}
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE ingestion_jobs SET {assignments} WHERE id = ?",
                (*values.values(), job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)
        job = self.get_job(job_id)
        assert job is not None
        return job

    def prepare_version(
        self,
        job_id: str,
        name: str,
        requested_asset_id: str | None,
        raw_reference: str,
        data_format: DataFormat,
        source_fingerprint: str,
        schema_fingerprint: str,
        recipe_fingerprint: str,
        inspection: InspectionResult,
        technical_metadata: dict[str, Any],
    ) -> PreparedVersion:
        connection = self._connect()
        created_asset = requested_asset_id is None
        try:
            connection.execute("BEGIN IMMEDIATE")
            job_row = connection.execute("SELECT status FROM ingestion_jobs WHERE id = ?", (job_id,)).fetchone()
            if job_row is None or job_row["status"] != IngestionStatus.INSPECTING.value:
                raise InvalidJobTransition("Job must be inspecting before version preparation")

            asset_id = requested_asset_id or str(uuid4())
            asset_row = connection.execute("SELECT * FROM data_assets WHERE id = ?", (asset_id,)).fetchone()
            if requested_asset_id is not None and asset_row is None:
                raise AssetNotFoundError(asset_id)

            if requested_asset_id is not None:
                reusable = connection.execute(
                    """SELECT * FROM asset_versions
                       WHERE asset_id = ? AND source_fingerprint = ? AND recipe_fingerprint = ?
                         AND status IN ('preparing', 'ready')
                       ORDER BY version_number DESC LIMIT 1""",
                    (asset_id, source_fingerprint, recipe_fingerprint),
                ).fetchone()
                if reusable is not None:
                    if reusable["status"] == "preparing":
                        raise IngestionInProgressError("An equivalent ingestion is still preparing")
                    binding = connection.execute(
                        """SELECT * FROM storage_bindings WHERE asset_version_id = ?
                           AND backend_type = 'file' AND binding_role = 'raw' AND status = 'ready'""",
                        (reusable["id"],),
                    ).fetchone()
                    if binding is None:
                        raise IngestionInProgressError("Equivalent version is not backed by a ready file")
                    warning = {
                        "code": "idempotent_retry",
                        "message": "Existing compatible asset version reused",
                        "asset_id": asset_id,
                        "asset_version_id": reusable["id"],
                    }
                    now = _now().isoformat()
                    connection.execute(
                        """UPDATE ingestion_jobs SET source_reference = ?,
                           source_fingerprint = ?, asset_id = ?, asset_version_id = ?, inspection_json = ?,
                           records_detected = ?, warnings_json = ?, updated_at = ?
                           WHERE id = ?""",
                        (
                            binding["physical_location"],
                            source_fingerprint,
                            asset_id,
                            reusable["id"],
                            reusable["inspection_json"],
                            inspection.records_detected,
                            _dumps([warning]),
                            now,
                            job_id,
                        ),
                    )
                    connection.commit()
                    asset = self.get_asset(asset_id)
                    version = self.get_version(asset_id, reusable["id"])
                    assert asset is not None and version is not None
                    return PreparedVersion(
                        asset=asset,
                        version=version,
                        raw_reference=binding["physical_location"],
                        data_format=DataFormat(binding["format"]),
                        reused=True,
                    )

            now = _now()
            if created_asset:
                connection.execute(
                    "INSERT INTO data_assets VALUES (?, ?, 'file', NULL, ?, ?)",
                    (asset_id, name, now.isoformat(), now.isoformat()),
                )

            previous = connection.execute(
                """SELECT * FROM asset_versions
                   WHERE asset_id = ? AND status = 'ready'
                   ORDER BY version_number DESC LIMIT 1""",
                (asset_id,),
            ).fetchone()
            version_number = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version_number), 0) + 1 FROM asset_versions WHERE asset_id = ?",
                    (asset_id,),
                ).fetchone()[0]
            )
            version_id = str(uuid4())
            previous_metadata = _loads(previous["technical_metadata_json"], {}) if previous else None
            diff = compare_technical_metadata(
                previous_metadata,
                technical_metadata,
                previous["id"] if previous else None,
                version_id,
            )
            duplicates = connection.execute(
                """SELECT asset_id, id AS asset_version_id, version_number FROM asset_versions
                   WHERE source_fingerprint = ? AND asset_id <> ? AND status = 'ready'
                   ORDER BY asset_id, version_number""",
                (source_fingerprint, asset_id),
            ).fetchall()
            warnings: list[dict[str, Any]] = []
            if duplicates:
                warnings.append(
                    {
                        "code": "duplicate_content",
                        "message": "Content fingerprint is already used by other assets",
                        "matches": [dict(row) for row in duplicates],
                    }
                )
            persisted_inspection = inspection.without_preview()
            connection.execute(
                """INSERT INTO asset_versions (
                    id, asset_id, version_number, source_fingerprint, schema_fingerprint,
                    recipe_fingerprint, status, created_at, technical_metadata_json,
                    inspection_json, drift_json, planned_location, format
                ) VALUES (?, ?, ?, ?, ?, ?, 'preparing', ?, ?, ?, ?, ?, ?)""",
                (
                    version_id,
                    asset_id,
                    version_number,
                    source_fingerprint,
                    schema_fingerprint,
                    recipe_fingerprint,
                    now.isoformat(),
                    _dumps(technical_metadata),
                    _dumps(persisted_inspection.model_dump(mode="json")),
                    _dumps(diff.model_dump(mode="json")),
                    raw_reference,
                    data_format.value,
                ),
            )
            connection.execute(
                """UPDATE ingestion_jobs SET source_fingerprint = ?, asset_id = ?, asset_version_id = ?,
                   inspection_json = ?, records_detected = ?, warnings_json = ?, updated_at = ?
                   WHERE id = ? AND status = 'inspecting'""",
                (
                    source_fingerprint,
                    asset_id,
                    version_id,
                    _dumps(persisted_inspection.model_dump(mode="json")),
                    inspection.records_detected,
                    _dumps(warnings),
                    now.isoformat(),
                    job_id,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        asset = self.get_asset(asset_id)
        version = self.get_version(asset_id, version_id)
        assert asset is not None and version is not None
        return PreparedVersion(
            asset=asset,
            version=version,
            raw_reference=raw_reference,
            data_format=data_format,
            created_asset=created_asset,
        )

    def finalize_reused_job(self, job_id: str, version_id: str, raw_reference: str) -> None:
        now = _now().isoformat()
        with self._connect() as connection:
            job = connection.execute(
                "SELECT provenance_json FROM ingestion_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if job is None:
                raise KeyError(job_id)
            self._insert_provenance_lineage(
                connection,
                raw_reference,
                version_id,
                DatasetProvenance.model_validate(_loads(job["provenance_json"], {})),
                now,
            )
            cursor = connection.execute(
                """UPDATE ingestion_jobs SET status = 'ready', source_reference = ?, updated_at = ?, finished_at = ?
                   WHERE id = ? AND status = 'inspecting' AND asset_version_id = ?""",
                (raw_reference, now, now, job_id, version_id),
            )
            if cursor.rowcount != 1:
                raise InvalidJobTransition("Idempotent retry job changed before finalization")

    def finalize_version(self, job_id: str, version_id: str, raw_reference: str, data_format: DataFormat) -> None:
        now = _now().isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute(
                """SELECT asset_id, status, source_fingerprint, schema_fingerprint
                   FROM asset_versions WHERE id = ?""",
                (version_id,),
            ).fetchone()
            job = connection.execute(
                "SELECT status, provenance_json FROM ingestion_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if version is None or version["status"] != "preparing":
                raise InvalidJobTransition("Version is not preparing")
            if job is None or job["status"] != "inspecting":
                raise InvalidJobTransition("Job is not inspecting")
            connection.execute(
                """INSERT INTO storage_bindings (
                    id, asset_version_id, backend_type, binding_role, status, physical_location,
                    format, content_fingerprint, schema_fingerprint, metadata_json, created_at, updated_at
                ) VALUES (?, ?, 'file', 'raw', 'ready', ?, ?, ?, ?, '{}', ?, ?)""",
                (
                    str(uuid4()),
                    version_id,
                    raw_reference,
                    data_format.value,
                    version["source_fingerprint"],
                    version["schema_fingerprint"],
                    now,
                    now,
                ),
            )
            self._insert_provenance_lineage(
                connection,
                raw_reference,
                version_id,
                DatasetProvenance.model_validate(_loads(job["provenance_json"], {})),
                now,
            )
            connection.execute("UPDATE asset_versions SET status = 'ready' WHERE id = ?", (version_id,))
            connection.execute(
                "UPDATE data_assets SET updated_at = ? WHERE id = ?", (now, version["asset_id"])
            )
            cursor = connection.execute(
                """UPDATE ingestion_jobs SET status = 'ready', source_reference = ?, updated_at = ?, finished_at = ?
                   WHERE id = ? AND status = 'inspecting' AND asset_version_id = ?""",
                (raw_reference, now, now, job_id, version_id),
            )
            if cursor.rowcount != 1:
                raise InvalidJobTransition("Job changed during version finalization")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fail_prepared_version(self, version_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE asset_versions SET status = 'failed' WHERE id = ? AND status = 'preparing'",
                (version_id,),
            )

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
        from queryx.app.ingestion.catalog_adapter import inspection_to_technical_metadata

        prepared = self.prepare_version(
            job_id,
            name,
            None,
            source_reference,
            data_format,
            source_fingerprint,
            schema_fingerprint,
            recipe_fingerprint,
            inspection,
            inspection_to_technical_metadata(inspection),
        )
        self.finalize_version(job_id, prepared.version.id, source_reference, data_format)
        asset = self.get_asset(prepared.asset.id)
        version = self.get_version(prepared.asset.id, prepared.version.id)
        assert asset is not None and version is not None
        return asset, version

    def get_job(self, job_id: str) -> IngestionJob | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row is not None else None

    def list_jobs_in_statuses(self, statuses: tuple[IngestionStatus, ...]) -> list[IngestionJob]:
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM ingestion_jobs WHERE status IN ({placeholders}) ORDER BY created_at",
                tuple(status.value for status in statuses),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def list_jobs(self, limit: int = 20) -> list[IngestionJob]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ingestion_jobs ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def list_assets(self) -> list[DataAsset]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM data_assets ORDER BY created_at DESC, id").fetchall()
            return [self._row_to_asset(connection, row) for row in rows]

    def get_asset(self, asset_id: str) -> DataAsset | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM data_assets WHERE id = ?", (asset_id,)).fetchone()
            return self._row_to_asset(connection, row) if row is not None else None

    def list_versions(self, asset_id: str) -> list[AssetVersion] | None:
        with self._connect() as connection:
            exists = connection.execute("SELECT 1 FROM data_assets WHERE id = ?", (asset_id,)).fetchone()
            if exists is None:
                return None
            rows = connection.execute(
                "SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY version_number DESC",
                (asset_id,),
            ).fetchall()
            return [self._row_to_version(connection, row) for row in rows]

    def get_version(self, asset_id: str, version_id: str) -> AssetVersion | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM asset_versions WHERE id = ? AND asset_id = ?", (version_id, asset_id)
            ).fetchone()
            return self._row_to_version(connection, row) if row is not None else None

    def get_version_by_id(self, version_id: str) -> AssetVersion | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM asset_versions WHERE id = ?", (version_id,)).fetchone()
            return self._row_to_version(connection, row) if row is not None else None

    def get_version_inspection(self, version_id: str) -> InspectionResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT inspection_json FROM asset_versions WHERE id = ?", (version_id,)
            ).fetchone()
        if row is None:
            return None
        payload = _loads(row["inspection_json"], None)
        return InspectionResult.model_validate(payload) if payload else None

    def get_version_diff(self, asset_id: str, version_id: str) -> AssetSchemaDiff | None:
        version = self.get_version(asset_id, version_id)
        return version.schema_diff if version is not None else None

    def get_latest_diff(self, asset_id: str) -> AssetSchemaDiff | None:
        versions = self.list_versions(asset_id)
        if versions is None or not versions:
            return None
        ready = next((version for version in versions if version.status == "ready"), versions[0])
        return ready.schema_diff

    def get_binding(self, version_id: str) -> StorageBinding | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM storage_bindings WHERE asset_version_id = ?
                   AND backend_type = 'file' AND binding_role = 'raw' AND status = 'ready'""",
                (version_id,),
            ).fetchone()
        return self._row_to_binding(row) if row is not None else None

    def get_prepared_details(self, version_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id, asset_id, source_fingerprint, planned_location, format, status
                   FROM asset_versions WHERE id = ?""",
                (version_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_planned_locations(self) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT planned_location FROM asset_versions WHERE status = 'preparing' AND planned_location IS NOT NULL"
            ).fetchall()
        return {row["planned_location"] for row in rows}

    def append_job_warning(self, job_id: str, warning: dict[str, Any]) -> None:
        now = _now().isoformat()
        with self._connect() as connection:
            row = connection.execute("SELECT warnings_json FROM ingestion_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return
            warnings = _loads(row["warnings_json"], [])
            warnings.append(warning)
            connection.execute(
                "UPDATE ingestion_jobs SET warnings_json = ?, updated_at = ? WHERE id = ?",
                (_dumps(warnings), now, job_id),
            )

    def list_bindings(self) -> list[StorageBinding]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM storage_bindings ORDER BY created_at").fetchall()
        return [self._row_to_binding(row) for row in rows]

    def get_lineage(self, asset_version_id: str) -> list[LineageEdge]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM lineage_edges WHERE target_asset_version_id = ? ORDER BY created_at",
                (asset_version_id,),
            ).fetchall()
        return [self._row_to_lineage(row) for row in rows]

    @staticmethod
    def _insert_provenance_lineage(
        connection: sqlite3.Connection,
        source_reference: str,
        asset_version_id: str,
        provenance: DatasetProvenance,
        created_at: str,
        operation: str = "upload",
    ) -> None:
        metadata = {"provenance": provenance.model_dump(mode="json", exclude_none=True)}
        metadata_json = _dumps(metadata)
        existing = connection.execute(
            """SELECT id FROM lineage_edges WHERE target_asset_version_id = ?
               AND operation = ? AND metadata_json = ? LIMIT 1""",
            (asset_version_id, operation, metadata_json),
        ).fetchone()
        if existing is None:
            connection.execute(
                """INSERT INTO lineage_edges (
                    id, source_reference, target_asset_version_id, operation, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (str(uuid4()), source_reference, asset_version_id, operation, created_at, metadata_json),
            )

    def fail_jobs_for_version(self, version_id: str, error: dict[str, Any]) -> list[str]:
        now = _now().isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, status FROM ingestion_jobs WHERE asset_version_id = ?", (version_id,)
            ).fetchall()
            failed: list[str] = []
            for row in rows:
                if row["status"] in {"ready", "inspecting", "acquiring"}:
                    connection.execute(
                        """UPDATE ingestion_jobs SET status = 'failed', error_json = ?, updated_at = ?, finished_at = ?
                           WHERE id = ?""",
                        (_dumps(error), now, now, row["id"]),
                    )
                    failed.append(row["id"])
            connection.execute(
                "UPDATE asset_versions SET status = 'failed' WHERE id = ? AND status IN ('ready', 'preparing')",
                (version_id,),
            )
        return failed

    def _row_to_asset(self, connection: sqlite3.Connection, row: sqlite3.Row) -> DataAsset:
        version_rows = connection.execute(
            "SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY version_number DESC", (row["id"],)
        ).fetchall()
        versions = [self._row_to_version(connection, version_row) for version_row in version_rows]
        latest = next((version for version in versions if version.status == "ready"), versions[0] if versions else None)
        return DataAsset(
            **dict(row),
            latest_version_id=latest.id if latest else None,
            latest_version_number=latest.version_number if latest else None,
            versions=versions,
        )

    @staticmethod
    def _row_to_version(connection: sqlite3.Connection, row: sqlite3.Row) -> AssetVersion:
        binding_rows = connection.execute(
            "SELECT * FROM storage_bindings WHERE asset_version_id = ? ORDER BY created_at", (row["id"],)
        ).fetchall()
        values = dict(row)
        technical_metadata = _loads(values.pop("technical_metadata_json", None), {})
        diff_payload = _loads(values.pop("drift_json", None), None)
        values.pop("inspection_json", None)
        values.pop("planned_location", None)
        values.pop("format", None)
        lineage_rows = connection.execute(
            "SELECT * FROM lineage_edges WHERE target_asset_version_id = ? ORDER BY created_at",
            (row["id"],),
        ).fetchall()
        provenance: list[DatasetProvenance] = []
        for lineage_row in lineage_rows:
            edge = IngestionStorage._row_to_lineage(lineage_row)
            if edge.provenance is not None and edge.provenance not in provenance:
                provenance.append(edge.provenance)
        return AssetVersion(
            **values,
            technical_metadata=technical_metadata,
            schema_diff=AssetSchemaDiff.model_validate(diff_payload) if diff_payload else None,
            storage_bindings=[IngestionStorage._row_to_binding(item) for item in binding_rows],
            provenance=provenance,
        )

    @staticmethod
    def _row_to_binding(row: sqlite3.Row) -> StorageBinding:
        values = dict(row)
        values["metadata"] = _loads(values.pop("metadata_json", None), {})
        return StorageBinding.model_validate(values)

    @staticmethod
    def _row_to_lineage(row: sqlite3.Row) -> LineageEdge:
        values = dict(row)
        metadata = _loads(values.pop("metadata_json", None), {})
        payload = metadata.get("provenance") if isinstance(metadata, dict) else None
        provenance = DatasetProvenance.model_validate(payload) if payload else (
            DatasetProvenance() if values.get("operation") == "upload" else None
        )
        return LineageEdge(**values, metadata=metadata, provenance=provenance)

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> IngestionJob:
        values = dict(row)
        values["warnings"] = _loads(values.pop("warnings_json"), [])
        values["error"] = _loads(values.pop("error_json"), None)
        inspection = _loads(values.pop("inspection_json"), None)
        values["inspection"] = InspectionResult.model_validate(inspection) if inspection else None
        values["provenance"] = DatasetProvenance.model_validate(
            _loads(values.pop("provenance_json", None), {})
        )
        return IngestionJob.model_validate(values)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value is not None else default
