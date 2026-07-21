from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from queryx.app.processing.storage import ProcessingStorage
from queryx.app.worker.models import TaskType, WorkerRuntime, WorkItem, WorkStatus


class LeaseLostError(RuntimeError):
    pass


class WorkItemConflictError(RuntimeError):
    pass


class WorkerStorage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        ProcessingStorage(db_path)
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
                CREATE TABLE IF NOT EXISTS work_items (
                    id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL CHECK(task_type IN (
                        'ingestion', 'processing'
                    )),
                    aggregate_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'queued', 'leased', 'retry_wait', 'completed', 'failed', 'cancelled'
                    )),
                    priority INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    claimed_by TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
                    max_attempts INTEGER NOT NULL CHECK(max_attempts > 0),
                    cancellation_requested INTEGER NOT NULL DEFAULT 0,
                    last_error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_work_items_claim
                    ON work_items(status, available_at, priority DESC, created_at);
                CREATE INDEX IF NOT EXISTS idx_work_items_lease
                    ON work_items(status, lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_work_items_aggregate
                    ON work_items(task_type, aggregate_id, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_work_item_active_aggregate
                    ON work_items(task_type, aggregate_id)
                    WHERE status IN ('queued', 'leased', 'retry_wait');

                CREATE TABLE IF NOT EXISTS worker_runtime (
                    worker_id TEXT PRIMARY KEY,
                    heartbeat_at TEXT NOT NULL,
                    reconciliation_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worker_reconciliation_runs (
                    id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (8, _now().isoformat()),
            )

    def enqueue(
        self,
        task_type: TaskType,
        aggregate_id: str,
        max_attempts: int,
        priority: int = 0,
        available_at: datetime | None = None,
    ) -> tuple[WorkItem, bool]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT * FROM work_items WHERE task_type = ? AND aggregate_id = ?
                   AND status IN ('queued', 'leased', 'retry_wait')
                   ORDER BY created_at DESC LIMIT 1""",
                (task_type.value, aggregate_id),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._row(existing), True
            now = _now()
            item_id = str(uuid4())
            ready_at = available_at or now
            connection.execute(
                """INSERT INTO work_items (
                    id, task_type, aggregate_id, status, priority, available_at,
                    attempt_count, max_attempts, cancellation_requested, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', ?, ?, 0, ?, 0, ?, ?)""",
                (
                    item_id,
                    task_type.value,
                    aggregate_id,
                    priority,
                    ready_at.isoformat(),
                    max_attempts,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            row = connection.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
            connection.commit()
            assert row is not None
            return self._row(row), False
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise WorkItemConflictError("An equivalent work item is already active") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim(self, worker_id: str, lease_seconds: int, now: datetime | None = None) -> WorkItem | None:
        resolved = now or _now()
        expires = resolved + timedelta(seconds=lease_seconds)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE work_items SET status = 'failed',
                       last_error_json = ?, claimed_by = NULL, lease_expires_at = NULL,
                       updated_at = ?, finished_at = ?
                   WHERE status = 'leased' AND lease_expires_at <= ?
                     AND attempt_count >= max_attempts
                     AND task_type IN ('ingestion', 'processing')""",
                (
                    _dumps({"code": "max_attempts_exceeded", "message": "Expired lease exhausted retries"}),
                    resolved.isoformat(),
                    resolved.isoformat(),
                    resolved.isoformat(),
                ),
            )
            row = connection.execute(
                """SELECT * FROM work_items
                   WHERE task_type IN ('ingestion', 'processing')
                     AND cancellation_requested = 0 AND attempt_count < max_attempts AND (
                       (status IN ('queued', 'retry_wait') AND available_at <= ?)
                       OR (status = 'leased' AND lease_expires_at <= ?)
                   )
                   ORDER BY priority DESC, available_at ASC, created_at ASC, id ASC
                   LIMIT 1""",
                (resolved.isoformat(), resolved.isoformat()),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            cursor = connection.execute(
                """UPDATE work_items SET status = 'leased', claimed_by = ?,
                       lease_expires_at = ?, heartbeat_at = ?, attempt_count = attempt_count + 1,
                       updated_at = ?, finished_at = NULL
                   WHERE id = ? AND status = ?""",
                (
                    worker_id,
                    expires.isoformat(),
                    resolved.isoformat(),
                    resolved.isoformat(),
                    row["id"],
                    row["status"],
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            claimed = connection.execute("SELECT * FROM work_items WHERE id = ?", (row["id"],)).fetchone()
            connection.commit()
            assert claimed is not None
            return self._row(claimed)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def heartbeat(
        self,
        item_id: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> WorkItem:
        resolved = now or _now()
        expires = resolved + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE work_items SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                   WHERE id = ? AND status = 'leased' AND claimed_by = ?
                     AND lease_expires_at > ? AND cancellation_requested = 0""",
                (
                    resolved.isoformat(),
                    expires.isoformat(),
                    resolved.isoformat(),
                    item_id,
                    worker_id,
                    resolved.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise LeaseLostError("Work item lease is no longer owned")
        item = self.get(item_id)
        assert item is not None
        return item

    def complete(self, item_id: str, worker_id: str, now: datetime | None = None) -> WorkItem:
        return self._finish_owned(item_id, worker_id, WorkStatus.COMPLETED, None, now)

    def fail(
        self,
        item_id: str,
        worker_id: str,
        error: dict[str, Any],
        now: datetime | None = None,
    ) -> WorkItem:
        return self._finish_owned(item_id, worker_id, WorkStatus.FAILED, error, now)

    def retry(
        self,
        item_id: str,
        worker_id: str,
        error: dict[str, Any],
        retry_base_seconds: int,
        now: datetime | None = None,
    ) -> WorkItem:
        resolved = now or _now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
            self._assert_owned(row, worker_id, resolved)
            assert row is not None
            if row["attempt_count"] >= row["max_attempts"]:
                connection.execute(
                    """UPDATE work_items SET status = 'failed', last_error_json = ?, claimed_by = NULL,
                       lease_expires_at = NULL, updated_at = ?, finished_at = ? WHERE id = ?""",
                    (_dumps(error), resolved.isoformat(), resolved.isoformat(), item_id),
                )
            else:
                delay = min(retry_base_seconds * (2 ** max(int(row["attempt_count"]) - 1, 0)), 3600)
                available = resolved + timedelta(seconds=delay)
                connection.execute(
                    """UPDATE work_items SET status = 'retry_wait', available_at = ?,
                       claimed_by = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                       last_error_json = ?, updated_at = ?, finished_at = NULL WHERE id = ?""",
                    (available.isoformat(), _dumps(error), resolved.isoformat(), item_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        item = self.get(item_id)
        assert item is not None
        return item

    def request_cancel(self, item_id: str) -> WorkItem:
        now = _now().isoformat()
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM work_items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise KeyError(item_id)
            status = WorkStatus(row["status"])
            if status in {WorkStatus.COMPLETED, WorkStatus.FAILED, WorkStatus.CANCELLED}:
                raise WorkItemConflictError("Terminal work item cannot be cancelled")
            if status in {WorkStatus.QUEUED, WorkStatus.RETRY_WAIT}:
                connection.execute(
                    """UPDATE work_items SET status = 'cancelled', cancellation_requested = 1,
                       updated_at = ?, finished_at = ? WHERE id = ?""",
                    (now, now, item_id),
                )
            else:
                connection.execute(
                    "UPDATE work_items SET cancellation_requested = 1, updated_at = ? WHERE id = ?",
                    (now, item_id),
                )
        item = self.get(item_id)
        assert item is not None
        return item

    def cancel_owned(self, item_id: str, worker_id: str) -> WorkItem:
        return self._finish_owned(item_id, worker_id, WorkStatus.CANCELLED, None, _now(), allow_cancel=True)

    def get(self, item_id: str) -> WorkItem | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
        return self._row(row) if row is not None else None

    def active_for(self, task_type: TaskType, aggregate_id: str) -> WorkItem | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM work_items WHERE task_type = ? AND aggregate_id = ?
                   AND status IN ('queued', 'leased', 'retry_wait') ORDER BY created_at DESC LIMIT 1""",
                (task_type.value, aggregate_id),
            ).fetchone()
        return self._row(row) if row is not None else None

    def latest_for(self, task_type: TaskType, aggregate_id: str) -> WorkItem | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM work_items WHERE task_type = ? AND aggregate_id = ?
                   ORDER BY created_at DESC, id DESC LIMIT 1""",
                (task_type.value, aggregate_id),
            ).fetchone()
        return self._row(row) if row is not None else None

    def list_items(self, statuses: tuple[WorkStatus, ...] | None = None) -> list[WorkItem]:
        with self._connect() as connection:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = connection.execute(
                    f"""SELECT * FROM work_items WHERE task_type IN ('ingestion', 'processing')
                        AND status IN ({placeholders}) ORDER BY created_at""",
                    tuple(status.value for status in statuses),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM work_items WHERE task_type IN ('ingestion', 'processing')
                       ORDER BY created_at"""
                ).fetchall()
        return [self._row(row) for row in rows]

    def counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in WorkStatus}
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT status, COUNT(*) AS total FROM work_items
                   WHERE task_type IN ('ingestion', 'processing') GROUP BY status"""
            ).fetchall()
        counts.update({row["status"]: int(row["total"]) for row in rows})
        return counts

    def touch_worker(self, worker_id: str, reconciled: bool = False, now: datetime | None = None) -> None:
        resolved = now or _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO worker_runtime (worker_id, heartbeat_at, reconciliation_at, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(worker_id) DO UPDATE SET heartbeat_at = excluded.heartbeat_at,
                     reconciliation_at = CASE WHEN excluded.reconciliation_at IS NULL
                       THEN worker_runtime.reconciliation_at ELSE excluded.reconciliation_at END,
                     updated_at = excluded.updated_at""",
                (
                    worker_id,
                    resolved.isoformat(),
                    resolved.isoformat() if reconciled else None,
                    resolved.isoformat(),
                ),
            )

    def latest_worker(self) -> WorkerRuntime:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM worker_runtime ORDER BY heartbeat_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return WorkerRuntime()
        return WorkerRuntime(
            worker_id=row["worker_id"],
            heartbeat_at=row["heartbeat_at"],
            reconciliation_at=row["reconciliation_at"],
        )

    def record_reconciliation(self, worker_id: str, metrics: dict[str, Any]) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO worker_reconciliation_runs VALUES (?, ?, ?, ?)",
                (str(uuid4()), worker_id, _dumps(metrics), now.isoformat()),
            )
        self.touch_worker(worker_id, reconciled=True, now=now)

    def force_status(
        self,
        item_id: str,
        status: WorkStatus,
        error: dict[str, Any] | None = None,
        available_at: datetime | None = None,
    ) -> None:
        now = _now()
        terminal = status in {WorkStatus.COMPLETED, WorkStatus.FAILED, WorkStatus.CANCELLED}
        with self._connect() as connection:
            connection.execute(
                """UPDATE work_items SET status = ?, available_at = COALESCE(?, available_at),
                   claimed_by = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                   last_error_json = COALESCE(?, last_error_json), updated_at = ?, finished_at = ?
                   WHERE id = ?""",
                (
                    status.value,
                    available_at.isoformat() if available_at else None,
                    _dumps(error) if error else None,
                    now.isoformat(),
                    now.isoformat() if terminal else None,
                    item_id,
                ),
            )

    def _finish_owned(
        self,
        item_id: str,
        worker_id: str,
        status: WorkStatus,
        error: dict[str, Any] | None,
        now: datetime | None,
        allow_cancel: bool = False,
    ) -> WorkItem:
        resolved = now or _now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
            self._assert_owned(row, worker_id, resolved, allow_cancel=allow_cancel)
            connection.execute(
                """UPDATE work_items SET status = ?, claimed_by = NULL, lease_expires_at = NULL,
                   heartbeat_at = NULL, last_error_json = ?, updated_at = ?, finished_at = ?
                   WHERE id = ?""",
                (status.value, _dumps(error) if error else None, resolved.isoformat(), resolved.isoformat(), item_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        item = self.get(item_id)
        assert item is not None
        return item

    @staticmethod
    def _assert_owned(
        row: sqlite3.Row | None,
        worker_id: str,
        now: datetime,
        allow_cancel: bool = False,
    ) -> None:
        if row is None or row["status"] != WorkStatus.LEASED.value or row["claimed_by"] != worker_id:
            raise LeaseLostError("Work item lease is no longer owned")
        if row["lease_expires_at"] is None or datetime.fromisoformat(row["lease_expires_at"]) <= now:
            raise LeaseLostError("Work item lease expired")
        if row["cancellation_requested"] and not allow_cancel:
            raise LeaseLostError("Work item cancellation was requested")

    @staticmethod
    def _row(row: sqlite3.Row) -> WorkItem:
        values = dict(row)
        values["cancellation_requested"] = bool(values["cancellation_requested"])
        values["last_error"] = _loads(values.pop("last_error_json"), None)
        return WorkItem.model_validate(values)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
