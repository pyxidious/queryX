from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from queryx.app.catalog.models import CatalogSnapshot, SourceMetadata


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

    def save_snapshot(self, sources: list[SourceMetadata]) -> CatalogSnapshot:
        created_at = datetime.now(timezone.utc)
        with self._connect() as connection:
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
                        json.dumps(source.declared, sort_keys=True),
                        json.dumps(source.inferred, sort_keys=True),
                    ),
                )
        return CatalogSnapshot(id=snapshot_id, created_at=created_at, sources=sources)

    def get_latest_snapshot(self) -> CatalogSnapshot | None:
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
                declared=self._loads(row["declared_json"]),
                inferred=self._loads(row["inferred_json"]),
            )
            for row in source_rows
        ]
        return CatalogSnapshot(
            id=int(snapshot_row["id"]),
            created_at=datetime.fromisoformat(snapshot_row["created_at"]),
            sources=sources,
        )

    @staticmethod
    def _loads(value: str) -> dict[str, Any]:
        loaded = json.loads(value)
        if not isinstance(loaded, dict):
            return {}
        return loaded
