from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DatabaseType = Literal["mysql", "mongodb"]
MetadataKind = Literal["declared", "inferred"]
RunStatus = Literal["completed", "partial", "failed"]
ScanStatus = Literal["completed", "failed"]
FreshnessStatus = Literal["current", "stale"]
DriftSeverity = Literal["none", "low", "medium", "high"]


class DataSource(BaseModel):
    id: str
    name: str
    database_type: DatabaseType
    host: str
    port: int
    database: str
    enabled: bool = True


class ProfilingBudget(BaseModel):
    enabled: bool = True
    max_records_per_entity: int = Field(default=25, ge=0)
    max_seconds_per_entity: float = Field(default=2.0, ge=0)
    max_entities: int = Field(default=100, ge=0)
    max_total_records: int = Field(default=500, ge=0)


class SourceMetadata(BaseModel):
    source: str
    database_type: DatabaseType
    declared: dict[str, Any] = Field(default_factory=dict)
    inferred: dict[str, Any] = Field(default_factory=dict)
    profiling_metrics: dict[str, Any] = Field(default_factory=dict)


class CatalogSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    created_at: datetime
    sources: list[SourceMetadata]


class ScanError(BaseModel):
    source: str
    database_type: DatabaseType
    message: str
    code: str = "source_unavailable"


class ScanSummary(BaseModel):
    snapshot_id: int | None
    created_at: datetime
    sources_scanned: int
    sources_failed: int
    errors: list[ScanError] = Field(default_factory=list)


class SourceScanResult(BaseModel):
    id: int | None = None
    scan_run_id: int | None = None
    source_id: str
    database_type: DatabaseType
    scan_status: ScanStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    fingerprint: str | None = None
    declared_metadata: dict[str, Any] = Field(default_factory=dict)
    inferred_metadata: dict[str, Any] = Field(default_factory=dict)
    profiling_metrics: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None


class ScanRun(BaseModel):
    id: int | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    status: RunStatus
    sources_succeeded: int
    sources_failed: int
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    results: list[SourceScanResult] = Field(default_factory=list)


class CurrentCatalogSource(BaseModel):
    source_id: str
    snapshot_id: int
    freshness_status: FreshnessStatus
    latest_scan_failed: bool
    last_successful_scan_id: int
    warning: str | None = None
    fingerprint: str | None = None
    metadata: dict[str, Any]


class CurrentCatalog(BaseModel):
    generated_at: datetime
    sources: list[CurrentCatalogSource]


class DriftChange(BaseModel):
    change_type: str
    path: str
    severity: DriftSeverity
    previous: Any = None
    current: Any = None


class DriftReport(BaseModel):
    has_drift: bool
    severity: DriftSeverity
    previous_fingerprint: str | None
    current_fingerprint: str | None
    previous_scan_id: int | None
    current_scan_id: int | None
    changes: list[DriftChange] = Field(default_factory=list)
