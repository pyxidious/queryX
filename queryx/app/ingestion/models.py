from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IngestionStatus(StrEnum):
    CREATED = "created"
    ACQUIRING = "acquiring"
    INSPECTING = "inspecting"
    READY = "ready"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AssetKind(StrEnum):
    FILE = "file"
    TABLE = "table"
    MYSQL_TABLE = "mysql_table"
    COLLECTION = "collection"
    GRAPH = "graph"


class AssetVersionStatus(StrEnum):
    PREPARING = "preparing"
    READY = "ready"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    STALE = "stale"


class BackendType(StrEnum):
    FILE = "file"
    DUCKDB = "duckdb"
    SQL = "sql"
    MONGODB = "mongodb"
    GRAPHDB = "graphdb"


class BindingRole(StrEnum):
    RAW = "raw"
    NORMALIZED = "normalized"
    SERVING = "serving"


class BindingStatus(StrEnum):
    PREPARING = "preparing"
    READY = "ready"
    FAILED = "failed"


class DataFormat(StrEnum):
    CSV = "csv"
    PARQUET = "parquet"


class SourceProvider(StrEnum):
    MANUAL = "manual"
    KAGGLE = "kaggle"
    OTHER = "other"


class DatasetProvenance(BaseModel):
    """User-declared descriptive origin; it never triggers external access."""

    source_provider: SourceProvider = SourceProvider.MANUAL
    source_reference: str | None = Field(default=None, max_length=512)
    source_version: str | None = Field(default=None, max_length=128)
    dataset_title: str | None = Field(default=None, max_length=256)
    license_name: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator(
        "source_reference", "source_version", "dataset_title", "license_name", "notes", mode="before"
    )
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = " ".join(value.split())
        if not normalized:
            return None
        if "<" in normalized or ">" in normalized:
            raise ValueError("HTML is not allowed in provenance fields")
        return normalized


class DataAsset(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    asset_kind: AssetKind
    description: str | None = None
    created_at: datetime
    updated_at: datetime
    latest_version_id: str | None = None
    latest_version_number: int | None = None
    versions: list[AssetVersion] = Field(default_factory=list)


class AssetVersion(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    asset_id: str
    version_number: int = Field(ge=1)
    source_fingerprint: str
    schema_fingerprint: str | None = None
    recipe_fingerprint: str | None = None
    status: AssetVersionStatus
    created_at: datetime
    technical_metadata: dict[str, Any] = Field(default_factory=dict)
    schema_diff: AssetSchemaDiff | None = None
    storage_bindings: list[StorageBinding] = Field(default_factory=list)
    provenance: list[DatasetProvenance] = Field(default_factory=list)


class StorageBinding(BaseModel):
    id: str
    asset_version_id: str
    backend_type: BackendType
    binding_role: BindingRole = BindingRole.RAW
    status: BindingStatus = BindingStatus.READY
    physical_location: str
    format: DataFormat | None = None
    recipe_fingerprint: str | None = None
    content_fingerprint: str | None = None
    schema_fingerprint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime | None = None


class LineageEdge(BaseModel):
    id: str
    source_reference: str
    target_asset_version_id: str
    operation: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: DatasetProvenance | None = None


class SchemaField(BaseModel):
    name: str
    data_type: str
    nullable: bool = True


class InspectionResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    format: DataFormat
    fields: list[SchemaField] = Field(validation_alias="schema", serialization_alias="schema")
    metadata: dict[str, Any] = Field(default_factory=dict)
    preview: list[dict[str, Any]] = Field(default_factory=list)
    records_detected: int | None = None
    records_estimated: bool = False

    def without_preview(self) -> InspectionResult:
        return self.model_copy(update={"preview": []})


class SchemaTypeChange(BaseModel):
    field: str
    previous: str
    current: str


class SchemaNullabilityChange(BaseModel):
    field: str
    previous: bool
    current: bool


class AssetSchemaDiff(BaseModel):
    has_drift: bool
    previous_version_id: str | None = None
    current_version_id: str
    fields_added: list[str] = Field(default_factory=list)
    fields_removed: list[str] = Field(default_factory=list)
    type_changes: list[SchemaTypeChange] = Field(default_factory=list)
    nullability_changes: list[SchemaNullabilityChange] = Field(default_factory=list)


class IngestionJob(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: IngestionStatus
    source_type: str
    original_filename: str
    logical_name: str | None = None
    source_reference: str | None = None
    target_backend: BackendType
    bytes_received: int = Field(default=0, ge=0)
    records_detected: int | None = Field(default=None, ge=0)
    records_loaded: int | None = Field(default=None, ge=0)
    records_rejected: int | None = Field(default=None, ge=0)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    source_fingerprint: str | None = None
    requested_asset_id: str | None = None
    asset_id: str | None = None
    asset_version_id: str | None = None
    inspection: InspectionResult | None = None
    created_at: datetime
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    provenance: DatasetProvenance = Field(default_factory=DatasetProvenance)


class UploadResult(BaseModel):
    job_id: str
    status: IngestionStatus
    work_item_id: str | None = None
    asset_id: str | None = None
    asset_version_id: str | None = None
    reused: bool = False


class ReconciliationReport(BaseModel):
    interrupted_jobs: list[str] = Field(default_factory=list)
    recovered_jobs: list[str] = Field(default_factory=list)
    failed_jobs: list[str] = Field(default_factory=list)
    missing_bindings: list[str] = Field(default_factory=list)
    orphan_staging_files: list[str] = Field(default_factory=list)
    orphan_raw_files: list[str] = Field(default_factory=list)
