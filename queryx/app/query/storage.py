from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from queryx.app.ingestion.storage import IngestionStorage
from queryx.app.query.models import (
    AssetRelationship,
    AssetRelationshipCreate,
    QueryRun,
    QueryRunStatus,
    RelationshipStatus,
)


class DuplicateRelationshipError(RuntimeError):
    pass


class QueryStorage:
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
                CREATE TABLE IF NOT EXISTS asset_relationships (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    left_asset_id TEXT NOT NULL,
                    left_field TEXT NOT NULL,
                    right_asset_id TEXT NOT NULL,
                    right_field TEXT NOT NULL,
                    relationship_type TEXT NOT NULL,
                    join_type_default TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(left_asset_id) REFERENCES data_assets(id),
                    FOREIGN KEY(right_asset_id) REFERENCES data_assets(id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_relationship_active
                    ON asset_relationships(left_asset_id, left_field, right_asset_id, right_field)
                    WHERE status = 'active';
                CREATE INDEX IF NOT EXISTS idx_asset_relationship_assets
                    ON asset_relationships(left_asset_id, right_asset_id, status);
                CREATE TABLE IF NOT EXISTS query_runs (
                    id TEXT PRIMARY KEY,
                    plan_fingerprint TEXT NOT NULL,
                    normalized_plan_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_asset_versions_json TEXT NOT NULL,
                    rows_returned INTEGER NOT NULL DEFAULT 0,
                    truncated INTEGER NOT NULL DEFAULT 0,
                    execution_time_ms REAL,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_query_runs_created ON query_runs(created_at);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (12, _now().isoformat()),
            )

    def create_relationship(self, payload: AssetRelationshipCreate) -> AssetRelationship:
        now = _now()
        relationship = AssetRelationship(
            id=str(uuid4()),
            **payload.model_dump(),
            status=RelationshipStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO asset_relationships (
                        id, name, left_asset_id, left_field, right_asset_id, right_field,
                        relationship_type, join_type_default, source, confidence, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        relationship.id, relationship.name, relationship.left_asset_id,
                        relationship.left_field, relationship.right_asset_id,
                        relationship.right_field, relationship.relationship_type,
                        relationship.join_type_default, relationship.source,
                        relationship.confidence, relationship.status,
                        now.isoformat(), now.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateRelationshipError("Equivalent active relationship already exists") from exc
            raise
        return relationship

    def list_relationships(self, include_disabled: bool = True) -> list[AssetRelationship]:
        where = "" if include_disabled else "WHERE status = 'active'"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM asset_relationships {where} ORDER BY created_at DESC, id"
            ).fetchall()
        return [AssetRelationship.model_validate(dict(row)) for row in rows]

    def get_relationship(self, relationship_id: str) -> AssetRelationship | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM asset_relationships WHERE id = ?", (relationship_id,)
            ).fetchone()
        return AssetRelationship.model_validate(dict(row)) if row else None

    def disable_relationship(self, relationship_id: str) -> AssetRelationship | None:
        now = _now().isoformat()
        with self._connect() as connection:
            connection.execute(
                "UPDATE asset_relationships SET status = 'disabled', updated_at = ? WHERE id = ?",
                (now, relationship_id),
            )
        return self.get_relationship(relationship_id)

    def create_query_run(
        self, plan_fingerprint: str, normalized_plan: dict[str, Any], source_versions: list[str]
    ) -> QueryRun:
        now = _now()
        run = QueryRun(
            id=str(uuid4()), plan_fingerprint=plan_fingerprint,
            normalized_plan=normalized_plan, status=QueryRunStatus.RUNNING,
            source_asset_versions=source_versions, created_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO query_runs (
                    id, plan_fingerprint, normalized_plan_json, status,
                    source_asset_versions_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (run.id, plan_fingerprint, _dumps(normalized_plan), run.status,
                 _dumps(source_versions), now.isoformat()),
            )
        return run

    def finish_query_run(
        self, run_id: str, *, status: QueryRunStatus, rows_returned: int = 0,
        truncated: bool = False, execution_time_ms: float | None = None,
        error: dict[str, Any] | None = None,
    ) -> QueryRun:
        finished = _now()
        with self._connect() as connection:
            connection.execute(
                """UPDATE query_runs SET status = ?, rows_returned = ?, truncated = ?,
                   execution_time_ms = ?, error_json = ?, finished_at = ? WHERE id = ?""",
                (status, rows_returned, int(truncated), execution_time_ms,
                 _dumps(error) if error else None, finished.isoformat(), run_id),
            )
        run = self.get_query_run(run_id)
        assert run is not None
        return run

    def get_query_run(self, run_id: str) -> QueryRun | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM query_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        values = dict(row)
        values["normalized_plan"] = json.loads(values.pop("normalized_plan_json"))
        values["source_asset_versions"] = json.loads(values.pop("source_asset_versions_json"))
        error_json = values.pop("error_json")
        values["error"] = json.loads(error_json) if error_json else None
        values["truncated"] = bool(values["truncated"])
        return QueryRun.model_validate(values)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
