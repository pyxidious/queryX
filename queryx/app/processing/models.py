from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ProcessingOperation(StrEnum):
    NORMALIZE_AND_REGISTER = "normalize_and_register"


class ProcessingStatus(StrEnum):
    CREATED = "created"
    NORMALIZING = "normalizing"
    REGISTERING = "registering"
    VALIDATING = "validating"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProcessingRun(BaseModel):
    id: str
    asset_version_id: str
    operation: ProcessingOperation
    status: ProcessingStatus
    input_binding_id: str
    recipe_name: str
    recipe_version: str
    recipe_fingerprint: str
    recipe: dict[str, Any] = Field(default_factory=dict)
    normalized_binding_id: str | None = None
    serving_binding_id: str | None = None
    records_read: int = Field(default=0, ge=0)
    records_written: int = Field(default=0, ge=0)
    records_rejected: int = Field(default=0, ge=0)
    bytes_written: int = Field(default=0, ge=0)
    observed_schema: list[dict[str, Any]] = Field(default_factory=list)
    canonical_schema: list[dict[str, Any]] = Field(default_factory=list)
    serving_schema: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime
    finished_at: datetime | None = None
    reused: bool = False


class NormalizationResult(BaseModel):
    records_read: int
    records_written: int
    records_rejected: int = 0
    bytes_written: int
    canonical_schema: list[dict[str, Any]]
    content_fingerprint: str
    schema_fingerprint: str


class ProcessingReconciliationReport(BaseModel):
    stale_runs: list[str] = Field(default_factory=list)
    recovered_runs: list[str] = Field(default_factory=list)
    failed_runs: list[str] = Field(default_factory=list)
    resumable_partial_runs: list[str] = Field(default_factory=list)
    missing_normalized_bindings: list[str] = Field(default_factory=list)
    missing_serving_bindings: list[str] = Field(default_factory=list)
    orphan_normalized_files: list[str] = Field(default_factory=list)
    orphan_duckdb_views: list[str] = Field(default_factory=list)
