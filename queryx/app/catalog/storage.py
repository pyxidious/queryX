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
    EnrichmentResult,
    EnrichmentRun,
    EntitySemanticAnnotation,
    FieldSemanticAnnotation,
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
                CREATE TABLE IF NOT EXISTS source_scan_locks (
                    source_id TEXT PRIMARY KEY,
                    token TEXT NOT NULL,
                    acquired_at TEXT NOT NULL
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
                """
                CREATE TABLE IF NOT EXISTS enrichment_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    source_snapshot_id INTEGER NOT NULL,
                    technical_fingerprint TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    output_schema_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    entities_processed INTEGER NOT NULL,
                    fields_processed INTEGER NOT NULL,
                    failures INTEGER NOT NULL,
                    token_metrics_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    errors_json TEXT NOT NULL,
                    request_count INTEGER NOT NULL,
                    retry_count INTEGER NOT NULL,
                    invalid_responses INTEGER NOT NULL,
                    reused_result INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS entity_annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    enrichment_run_id INTEGER NOT NULL,
                    source_id TEXT NOT NULL,
                    entity_name TEXT NOT NULL,
                    annotation_json TEXT NOT NULL,
                    FOREIGN KEY(enrichment_run_id) REFERENCES enrichment_runs(id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS field_annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    enrichment_run_id INTEGER NOT NULL,
                    source_id TEXT NOT NULL,
                    entity_name TEXT NOT NULL,
                    field_path TEXT NOT NULL,
                    annotation_json TEXT NOT NULL,
                    FOREIGN KEY(enrichment_run_id) REFERENCES enrichment_runs(id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_enrichment_runs_source ON enrichment_runs(source_id, id)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (3, datetime.now(timezone.utc).isoformat()),
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

    def get_source(self, source_id: str) -> DataSource | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        if row is None:
            return None
        return DataSource(
            id=row["id"],
            name=row["name"],
            database_type=row["database_type"],
            host=row["host"],
            port=int(row["port"]),
            database=row["database_name"],
            enabled=bool(row["enabled"]),
        )

    def acquire_source_scan(self, source_id: str, token: str) -> bool:
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO source_scan_locks VALUES (?, ?, ?)",
                    (source_id, token, datetime.now(timezone.utc).isoformat()),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def release_source_scan(self, source_id: str, token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM source_scan_locks WHERE source_id = ? AND token = ?",
                (source_id, token),
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

    def find_reusable_enrichment_run(
        self,
        source_id: str,
        source_snapshot_id: int,
        technical_fingerprint: str,
        model_name: str,
        prompt_version: str,
        output_schema_version: str,
    ) -> EnrichmentRun | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM enrichment_runs
                WHERE source_id = ?
                    AND source_snapshot_id = ?
                    AND technical_fingerprint = ?
                    AND model_name = ?
                    AND prompt_version = ?
                    AND output_schema_version = ?
                    AND status = 'completed'
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    source_id,
                    source_snapshot_id,
                    technical_fingerprint,
                    model_name,
                    prompt_version,
                    output_schema_version,
                ),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_enrichment_run(connection, row, include_results=True).model_copy(
                update={"reused_result": True}
            )

    def save_enrichment_run(self, run: EnrichmentRun) -> EnrichmentRun:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO enrichment_runs (
                    source_id, source_snapshot_id, technical_fingerprint, model_name,
                    prompt_version, output_schema_version, created_at, started_at, finished_at,
                    duration_ms, status, entities_processed, fields_processed, failures,
                    token_metrics_json, warnings_json, errors_json, request_count, retry_count,
                    invalid_responses, reused_result
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.source_id,
                    run.source_snapshot_id,
                    run.technical_fingerprint,
                    run.model_name,
                    run.prompt_version,
                    run.output_schema_version,
                    run.created_at.isoformat(),
                    run.started_at.isoformat(),
                    run.finished_at.isoformat(),
                    run.duration_ms,
                    run.status,
                    run.entities_processed,
                    run.fields_processed,
                    run.failures,
                    self._dumps(run.token_metrics),
                    self._dumps(run.warnings),
                    self._dumps(run.errors),
                    run.request_count,
                    run.retry_count,
                    run.invalid_responses,
                    int(run.reused_result),
                ),
            )
            run_id = int(cursor.lastrowid)
            for result in run.results:
                connection.execute(
                    """
                    INSERT INTO entity_annotations (
                        enrichment_run_id, source_id, entity_name, annotation_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        result.entity.source_id,
                        result.entity.entity_name,
                        self._dumps(result.entity.model_dump(mode="json")),
                    ),
                )
                for field in result.fields:
                    connection.execute(
                        """
                        INSERT INTO field_annotations (
                            enrichment_run_id, source_id, entity_name, field_path, annotation_json
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            field.source_id,
                            field.entity_name,
                            field.field_path,
                            self._dumps(field.model_dump(mode="json")),
                        ),
                    )
        return run.model_copy(update={"id": run_id})

    def get_enrichment_run(self, run_id: int, include_results: bool = True) -> EnrichmentRun | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM enrichment_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_enrichment_run(connection, row, include_results)

    def get_latest_enrichment_run(self, source_id: str, include_results: bool = True) -> EnrichmentRun | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM enrichment_runs
                WHERE source_id = ? AND status IN ('completed', 'partial')
                ORDER BY id DESC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_enrichment_run(connection, row, include_results)

    def get_enrichment_history(self, source_id: str) -> list[EnrichmentRun]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM enrichment_runs WHERE source_id = ? ORDER BY id DESC",
                (source_id,),
            ).fetchall()
            return [self._row_to_enrichment_run(connection, row, include_results=False) for row in rows]

    def get_compatible_enrichment_run(
        self,
        source_id: str,
        source_snapshot_id: int,
        technical_fingerprint: str,
    ) -> EnrichmentRun | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM enrichment_runs
                WHERE source_id = ?
                    AND source_snapshot_id = ?
                    AND technical_fingerprint = ?
                    AND status IN ('completed', 'partial')
                ORDER BY id DESC
                LIMIT 1
                """,
                (source_id, source_snapshot_id, technical_fingerprint),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_enrichment_run(connection, row, include_results=True)

    def get_semantic_current(self, sources: list[DataSource]) -> dict[str, Any]:
        technical_current = self.get_current_catalog(sources)
        semantic_sources: list[dict[str, Any]] = []
        for source in technical_current.sources:
            compatible = None
            if source.fingerprint is not None:
                compatible = self.get_compatible_enrichment_run(
                    source.source_id,
                    source.snapshot_id,
                    source.fingerprint,
                )
            latest = self.get_latest_enrichment_run(source.source_id, include_results=False)
            semantic_status = "missing"
            run_payload = None
            if compatible is not None:
                semantic_status = "current"
                run_payload = compatible.model_dump(mode="json")
            elif latest is not None:
                semantic_status = "stale"
                run_payload = latest.model_dump(mode="json")
            semantic_sources.append(
                {
                    "source_id": source.source_id,
                    "technical_snapshot_id": source.snapshot_id,
                    "technical_fingerprint": source.fingerprint,
                    "semantic_status": semantic_status,
                    "enrichment": run_payload,
                }
            )
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "sources": semantic_sources}

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

    def _row_to_enrichment_run(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        include_results: bool,
    ) -> EnrichmentRun:
        results: list[EnrichmentResult] = []
        if include_results:
            entity_rows = connection.execute(
                """
                SELECT annotation_json FROM entity_annotations
                WHERE enrichment_run_id = ?
                ORDER BY id ASC
                """,
                (row["id"],),
            ).fetchall()
            fields_by_entity: dict[str, list[FieldSemanticAnnotation]] = {}
            field_rows = connection.execute(
                """
                SELECT annotation_json FROM field_annotations
                WHERE enrichment_run_id = ?
                ORDER BY id ASC
                """,
                (row["id"],),
            ).fetchall()
            for field_row in field_rows:
                field = FieldSemanticAnnotation.model_validate(self._loads_dict(field_row["annotation_json"]))
                fields_by_entity.setdefault(field.entity_name, []).append(field)
            for entity_row in entity_rows:
                entity = EntitySemanticAnnotation.model_validate(self._loads_dict(entity_row["annotation_json"]))
                results.append(
                    EnrichmentResult(
                        entity=entity,
                        fields=fields_by_entity.get(entity.entity_name, []),
                        output_schema_version=row["output_schema_version"],
                    )
                )
        return EnrichmentRun(
            id=int(row["id"]),
            source_id=row["source_id"],
            source_snapshot_id=int(row["source_snapshot_id"]),
            technical_fingerprint=row["technical_fingerprint"],
            model_name=row["model_name"],
            prompt_version=row["prompt_version"],
            output_schema_version=row["output_schema_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]),
            duration_ms=int(row["duration_ms"]),
            status=row["status"],
            entities_processed=int(row["entities_processed"]),
            fields_processed=int(row["fields_processed"]),
            failures=int(row["failures"]),
            token_metrics=self._loads_dict(row["token_metrics_json"]),
            warnings=self._loads_list(row["warnings_json"]),
            errors=self._loads_list(row["errors_json"]),
            request_count=int(row["request_count"]),
            retry_count=int(row["retry_count"]),
            invalid_responses=int(row["invalid_responses"]),
            reused_result=bool(row["reused_result"]),
            results=results,
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
