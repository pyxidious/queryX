from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    COLLECTION = "collection"
    GRAPH = "graph"


class AssetVersionStatus(StrEnum):
    READY = "ready"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class BackendType(StrEnum):
    FILE = "file"
    DUCKDB = "duckdb"
    SQL = "sql"
    MONGODB = "mongodb"
    GRAPHDB = "graphdb"


class DataFormat(StrEnum):
    CSV = "csv"
    PARQUET = "parquet"


class DataAsset(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    asset_kind: AssetKind
    description: str | None = None
    created_at: datetime
    updated_at: datetime
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
    storage_bindings: list[StorageBinding] = Field(default_factory=list)


class StorageBinding(BaseModel):
    id: str
    asset_version_id: str
    backend_type: BackendType
    physical_location: str
    format: DataFormat
    created_at: datetime


class LineageEdge(BaseModel):
    id: str
    source_reference: str
    target_asset_version_id: str
    operation: str
    created_at: datetime


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


class IngestionJob(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: IngestionStatus
    source_type: str
    original_filename: str
    source_reference: str | None = None
    target_backend: BackendType
    bytes_received: int = Field(default=0, ge=0)
    records_detected: int | None = Field(default=None, ge=0)
    records_loaded: int | None = Field(default=None, ge=0)
    records_rejected: int | None = Field(default=None, ge=0)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    source_fingerprint: str | None = None
    asset_id: str | None = None
    asset_version_id: str | None = None
    inspection: InspectionResult | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class UploadResult(BaseModel):
    job_id: str
    status: IngestionStatus
    asset_id: str | None = None
    asset_version_id: str | None = None
