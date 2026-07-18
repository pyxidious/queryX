from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from queryx.app.worker.models import TaskType, WorkStatus
from queryx.app.worker.storage import LeaseLostError, WorkerStorage


def test_work_item_creation_is_idempotent_and_schema_initialization_is_repeatable(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite3"
    first = WorkerStorage(database)
    item, reused = first.enqueue(TaskType.INGESTION, "job-1", max_attempts=3)
    second = WorkerStorage(database)
    same, was_reused = second.enqueue(TaskType.INGESTION, "job-1", max_attempts=3)

    assert reused is False
    assert was_reused is True
    assert same.id == item.id
    assert same.status == WorkStatus.QUEUED


def test_atomic_claim_prevents_two_claimants_and_respects_priority(tmp_path: Path) -> None:
    storage = WorkerStorage(tmp_path / "catalog.sqlite3")
    storage.enqueue(TaskType.INGESTION, "low", 3, priority=0)
    high, _ = storage.enqueue(TaskType.PROCESSING, "high", 3, priority=10)

    first = storage.claim("worker-a", 30)
    second = storage.claim("worker-b", 30)

    assert first is not None and first.id == high.id
    assert second is not None and second.id != first.id
    assert storage.get(first.id).claimed_by == "worker-a"  # type: ignore[union-attr]


def test_lease_heartbeat_expiry_reclaim_and_lost_owner(tmp_path: Path) -> None:
    storage = WorkerStorage(tmp_path / "catalog.sqlite3")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    item, _ = storage.enqueue(TaskType.INGESTION, "job", 3, available_at=start)
    first = storage.claim("worker-a", 10, start)
    assert first is not None and first.attempt_count == 1

    renewed = storage.heartbeat(item.id, "worker-a", 10, start + timedelta(seconds=5))
    assert renewed.heartbeat_at == start + timedelta(seconds=5)
    assert storage.claim("worker-b", 10, start + timedelta(seconds=11)) is None

    reclaimed = storage.claim("worker-b", 10, start + timedelta(seconds=16))
    assert reclaimed is not None and reclaimed.id == item.id
    assert reclaimed.attempt_count == 2
    with pytest.raises(LeaseLostError):
        storage.complete(item.id, "worker-a", start + timedelta(seconds=17))


def test_retry_backoff_and_max_attempts(tmp_path: Path) -> None:
    storage = WorkerStorage(tmp_path / "catalog.sqlite3")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    item, _ = storage.enqueue(TaskType.PROCESSING, "run", 2, available_at=start)
    storage.claim("worker", 30, start)
    waiting = storage.retry(
        item.id,
        "worker",
        {"code": "temporary", "message": "retry"},
        retry_base_seconds=4,
        now=start + timedelta(seconds=1),
    )
    assert waiting.status == WorkStatus.RETRY_WAIT
    assert waiting.available_at == start + timedelta(seconds=5)
    assert storage.claim("worker", 30, start + timedelta(seconds=4)) is None

    storage.claim("worker", 30, start + timedelta(seconds=5))
    exhausted = storage.retry(
        item.id,
        "worker",
        {"code": "temporary", "message": "retry"},
        retry_base_seconds=4,
        now=start + timedelta(seconds=6),
    )
    assert exhausted.status == WorkStatus.FAILED
    assert exhausted.attempt_count == 2


def test_queued_and_leased_cancellation_are_cooperative(tmp_path: Path) -> None:
    storage = WorkerStorage(tmp_path / "catalog.sqlite3")
    queued, _ = storage.enqueue(TaskType.INGESTION, "queued", 3)
    assert storage.request_cancel(queued.id).status == WorkStatus.CANCELLED

    leased, _ = storage.enqueue(TaskType.PROCESSING, "leased", 3)
    storage.claim("worker", 30)
    requested = storage.request_cancel(leased.id)
    assert requested.status == WorkStatus.LEASED
    assert requested.cancellation_requested is True
    with pytest.raises(LeaseLostError):
        storage.complete(leased.id, "worker")
