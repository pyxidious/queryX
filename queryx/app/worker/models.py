from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TaskType(StrEnum):
    INGESTION = "ingestion"
    PROCESSING = "processing"


class WorkStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkItem(BaseModel):
    id: str
    task_type: TaskType
    aggregate_id: str
    status: WorkStatus
    priority: int = 0
    available_at: datetime
    claimed_by: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    cancellation_requested: bool = False
    last_error: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class WorkerRuntime(BaseModel):
    worker_id: str | None = None
    heartbeat_at: datetime | None = None
    reconciliation_at: datetime | None = None


class WorkReconciliationReport(BaseModel):
    expired_leases_requeued: list[str] = Field(default_factory=list)
    exhausted_items: list[str] = Field(default_factory=list)
    completed_from_aggregate: list[str] = Field(default_factory=list)
    inconsistent_completed: list[str] = Field(default_factory=list)
    missing_aggregates: list[str] = Field(default_factory=list)
    recreated_items: list[str] = Field(default_factory=list)
    duplicate_items: list[str] = Field(default_factory=list)

