from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DatabaseType = Literal["mysql", "mongodb"]
MetadataKind = Literal["declared", "inferred"]
RunStatus = Literal["completed", "partial", "failed"]
ScanStatus = Literal["completed", "failed"]
FreshnessStatus = Literal["current", "stale"]
DriftSeverity = Literal["none", "low", "medium", "high"]
Sensitivity = Literal["none", "potential_pii", "confidential", "unknown"]


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


class EntitySemanticAnnotation(BaseModel):
    source_id: str
    entity_name: str
    entity_kind: str
    description: str
    business_domain: str
    synonyms: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    confidence_source: str = "model_self_reported"
    language: str = "it"

    @model_validator(mode="after")
    def force_confidence_source(self) -> EntitySemanticAnnotation:
        self.confidence_source = "model_self_reported"
        return self


class FieldSemanticAnnotation(BaseModel):
    source_id: str
    entity_name: str
    field_path: str
    description: str
    semantic_type: str
    business_terms: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    unit: str | None = None
    sensitivity: Sensitivity = "unknown"
    confidence: float = Field(ge=0, le=1)
    confidence_source: str = "model_self_reported"
    language: str = "it"

    @model_validator(mode="after")
    def force_confidence_source(self) -> FieldSemanticAnnotation:
        self.confidence_source = "model_self_reported"
        return self


class EnrichmentResult(BaseModel):
    entity: EntitySemanticAnnotation
    fields: list[FieldSemanticAnnotation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unannotated_fields: list[str] = Field(default_factory=list)
    output_schema_version: str = "semantic-annotation-v1"


class EnrichmentRun(BaseModel):
    id: int | None = None
    source_id: str
    source_snapshot_id: int
    technical_fingerprint: str
    model_name: str
    prompt_version: str
    output_schema_version: str
    created_at: datetime
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    status: RunStatus
    entities_processed: int
    fields_processed: int
    failures: int
    token_metrics: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    request_count: int = 0
    retry_count: int = 0
    invalid_responses: int = 0
    reused_result: bool = False
    results: list[EnrichmentResult] = Field(default_factory=list)


class EnrichmentRequest(BaseModel):
    force: bool = False
    language: str = "it"
    max_entities: int | None = None
