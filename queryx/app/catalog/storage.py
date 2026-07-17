from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from queryx.app.catalog.models import (
    CatalogSnapshot,
    CurrentCatalog,
    CurrentCatalogSource,
    DataSource,
    ScanRun,
    SourceMetadata,
    SourceScanResult,
)


class CatalogStorage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS catalog_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS catalog_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    database_type TEXT NOT NULL,
                    declared_json TEXT NOT NULL,
                    inferred_json TEXT NOT NULL,
                    FOREIGN KEY(snapshot_id) REFERENCES catalog_snapshots(id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    database_type TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    database_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    sources_succeeded INTEGER NOT NULL,
                    sources_failed INTEGER NOT NULL,
                    warnings_json TEXT NOT NULL,
                    errors_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS source_scan_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_run_id INTEGER NOT NULL,
                    source_id TEXT NOT NULL,
                    database_type TEXT NOT NULL,
                    scan_status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    fingerprint TEXT,
                    declared_json TEXT NOT NULL,
                    inferred_json TEXT NOT NULL,
                    profiling_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    error_json TEXT,
                    FOREIGN KEY(scan_run_id) REFERENCES scan_runs(id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_source_scan_results_source ON source_scan_results(source_id, scan_run_id)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (2, datetime.now(timezone.utc).isoformat()),
            )

    def upsert_sources(self, sources: list[DataSource]) -> None:
        with self._connect() as connection:
            for source in sources:
                connection.execute(
                    """
                    INSERT INTO sources (id, name, database_type, host, port, database_name, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        database_type = excluded.database_type,
                        host = excluded.host,
                        port = excluded.port,
                        database_name = excluded.database_name,
                        enabled = excluded.enabled
                    """,
                    (
                        source.id,
                        source.name,
                        source.database_type,
                        source.host,
                        source.port,
                        source.database,
                        int(source.enabled),
                    ),
                )

    def save_scan_run(self, run: ScanRun) -> ScanRun:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO scan_runs (
                    started_at, finished_at, duration_ms, status, sources_succeeded,
                    sources_failed, warnings_json, errors_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.started_at.isoformat(),
                    run.finished_at.isoformat(),
                    run.duration_ms,
                    run.status,
                    run.sources_succeeded,
                    run.sources_failed,
                    self._dumps(run.warnings),
                    self._dumps(run.errors),
                ),
            )
            scan_run_id = int(cursor.lastrowid)
            saved_results: list[SourceScanResult] = []
            for result in run.results:
                result_cursor = connection.execute(
                    """
                    INSERT INTO source_scan_results (
                        scan_run_id, source_id, database_type, scan_status, started_at,
                        finished_at, duration_ms, fingerprint, declared_json, inferred_json,
                        profiling_json, warnings_json, error_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scan_run_id,
                        result.source_id,
                        result.database_type,
                        result.scan_status,
                        result.started_at.isoformat(),
                        result.finished_at.isoformat(),
                        result.duration_ms,
                        result.fingerprint,
                        self._dumps(result.declared_metadata),
                        self._dumps(result.inferred_metadata),
                        self._dumps(result.profiling_metrics),
                        self._dumps(result.warnings),
                        self._dumps(result.error) if result.error is not None else None,
                    ),
                )
                saved_results.append(result.model_copy(update={"id": int(result_cursor.lastrowid), "scan_run_id": scan_run_id}))

            successful_sources = [
                SourceMetadata(
                    source=result.source_id,
                    database_type=result.database_type,
                    declared=result.declared_metadata,
                    inferred=result.inferred_metadata,
                    profiling_metrics=result.profiling_metrics,
                )
                for result in saved_results
                if result.scan_status == "completed"
            ]
            self._save_legacy_snapshot(connection, run.finished_at, successful_sources)

        return run.model_copy(update={"id": scan_run_id, "results": saved_results})

    def save_snapshot(self, sources: list[SourceMetadata]) -> CatalogSnapshot:
        created_at = datetime.now(timezone.utc)
        with self._connect() as connection:
            snapshot_id = self._save_legacy_snapshot(connection, created_at, sources)
        return CatalogSnapshot(id=snapshot_id, created_at=created_at, sources=sources)

    def get_latest_snapshot(self) -> CatalogSnapshot | None:
        latest_run = self.get_latest_scan_run()
        if latest_run is not None:
            sources = [
                SourceMetadata(
                    source=result.source_id,
                    database_type=result.database_type,
                    declared=result.declared_metadata,
                    inferred=result.inferred_metadata,
                    profiling_metrics=result.profiling_metrics,
                )
                for result in latest_run.results
                if result.scan_status == "completed"
            ]
            return CatalogSnapshot(id=latest_run.id, created_at=latest_run.finished_at, sources=sources)

        with self._connect() as connection:
            snapshot_row = connection.execute(
                "SELECT id, created_at FROM catalog_snapshots ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if snapshot_row is None:
                return None
            source_rows = connection.execute(
                """
                SELECT source, database_type, declared_json, inferred_json
                FROM catalog_sources
                WHERE snapshot_id = ?
                ORDER BY id ASC
                """,
                (snapshot_row["id"],),
            ).fetchall()

        sources = [
            SourceMetadata(
                source=row["source"],
                database_type=row["database_type"],
                declared=self._loads_dict(row["declared_json"]),
                inferred=self._loads_dict(row["inferred_json"]),
            )
            for row in source_rows
        ]
        return CatalogSnapshot(
            id=int(snapshot_row["id"]),
            created_at=datetime.fromisoformat(snapshot_row["created_at"]),
            sources=sources,
        )

    def get_latest_scan_run(self) -> ScanRun | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                return None
            results = self._load_results(connection, "WHERE scan_run_id = ?", (row["id"],))
        return self._row_to_run(row, results)

    def get_source_history(self, source_id: str) -> list[SourceScanResult]:
        with self._connect() as connection:
            return self._load_results(
                connection,
                "WHERE source_id = ? ORDER BY scan_run_id DESC, id DESC",
                (source_id,),
            )

    def get_latest_successful_source_result(self, source_id: str) -> SourceScanResult | None:
        with self._connect() as connection:
            results = self._load_results(
                connection,
                "WHERE source_id = ? AND scan_status = 'completed' ORDER BY scan_run_id DESC LIMIT 1",
                (source_id,),
            )
        return results[0] if results else None

    def get_latest_source_result(self, source_id: str) -> SourceScanResult | None:
        with self._connect() as connection:
            results = self._load_results(
                connection,
                "WHERE source_id = ? ORDER BY scan_run_id DESC LIMIT 1",
                (source_id,),
            )
        return results[0] if results else None

    def get_two_latest_successful_source_results(self, source_id: str) -> list[SourceScanResult]:
        with self._connect() as connection:
            return self._load_results(
                connection,
                "WHERE source_id = ? AND scan_status = 'completed' ORDER BY scan_run_id DESC LIMIT 2",
                (source_id,),
            )

    def get_current_catalog(self, sources: list[DataSource]) -> CurrentCatalog:
        current_sources: list[CurrentCatalogSource] = []
        for source in sources:
            latest_success = self.get_latest_successful_source_result(source.id)
            if latest_success is None or latest_success.scan_run_id is None:
                continue
            latest_result = self.get_latest_source_result(source.id)
            latest_failed = latest_result is not None and latest_result.scan_status == "failed"
            stale = latest_result is not None and latest_result.scan_run_id != latest_success.scan_run_id
            freshness = "stale" if latest_failed or stale else "current"
            warning = None
            if freshness == "stale":
                warning = "The latest scan failed; using last successful metadata"
            current_sources.append(
                CurrentCatalogSource(
                    source_id=source.id,
                    snapshot_id=latest_success.scan_run_id,
                    freshness_status=freshness,
                    latest_scan_failed=latest_failed,
                    last_successful_scan_id=latest_success.scan_run_id,
                    warning=warning,
                    fingerprint=latest_success.fingerprint,
                    metadata={
                        "declared": latest_success.declared_metadata,
                        "inferred": latest_success.inferred_metadata,
                        "profiling": latest_success.profiling_metrics,
                    },
                )
            )
        return CurrentCatalog(generated_at=datetime.now(timezone.utc), sources=current_sources)

    def _load_results(
        self,
        connection: sqlite3.Connection,
        where_clause: str,
        params: tuple[Any, ...],
    ) -> list[SourceScanResult]:
        rows = connection.execute(f"SELECT * FROM source_scan_results {where_clause}", params).fetchall()
        return [self._row_to_result(row) for row in rows]

    def _row_to_run(self, row: sqlite3.Row, results: list[SourceScanResult]) -> ScanRun:
        return ScanRun(
            id=int(row["id"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]),
            duration_ms=int(row["duration_ms"]),
            status=row["status"],
            sources_succeeded=int(row["sources_succeeded"]),
            sources_failed=int(row["sources_failed"]),
            warnings=self._loads_list(row["warnings_json"]),
            errors=self._loads_list(row["errors_json"]),
            results=results,
        )

    def _row_to_result(self, row: sqlite3.Row) -> SourceScanResult:
        return SourceScanResult(
            id=int(row["id"]),
            scan_run_id=int(row["scan_run_id"]),
            source_id=row["source_id"],
            database_type=row["database_type"],
            scan_status=row["scan_status"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]),
            duration_ms=int(row["duration_ms"]),
            fingerprint=row["fingerprint"],
            declared_metadata=self._loads_dict(row["declared_json"]),
            inferred_metadata=self._loads_dict(row["inferred_json"]),
            profiling_metrics=self._loads_dict(row["profiling_json"]),
            warnings=self._loads_list(row["warnings_json"]),
            error=self._loads_nullable(row["error_json"]),
        )

    def _save_legacy_snapshot(
        self,
        connection: sqlite3.Connection,
        created_at: datetime,
        sources: list[SourceMetadata],
    ) -> int:
        cursor = connection.execute(
            "INSERT INTO catalog_snapshots (created_at) VALUES (?)",
            (created_at.isoformat(),),
        )
        snapshot_id = int(cursor.lastrowid)
        for source in sources:
            connection.execute(
                """
                INSERT INTO catalog_sources (
                    snapshot_id, source, database_type, declared_json, inferred_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    source.source,
                    source.database_type,
                    self._dumps(source.declared),
                    self._dumps(source.inferred),
                ),
            )
        return snapshot_id

    @staticmethod
    def _dumps(value: Any) -> str:
        return json.dumps(value, sort_keys=True, default=str)

    @staticmethod
    def _loads_dict(value: str) -> dict[str, Any]:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _loads_list(value: str) -> list[Any]:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, list) else []

    @staticmethod
    def _loads_nullable(value: str | None) -> dict[str, Any] | None:
        if value is None:
            return None
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else None
