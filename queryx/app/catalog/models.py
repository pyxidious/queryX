from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DatabaseType = Literal["mysql", "mongodb"]
MetadataKind = Literal["declared", "inferred"]


class SourceMetadata(BaseModel):
    source: str
    database_type: DatabaseType
    declared: dict[str, Any] = Field(default_factory=dict)
    inferred: dict[str, Any] = Field(default_factory=dict)


class CatalogSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    created_at: datetime
    sources: list[SourceMetadata]


class ScanError(BaseModel):
    source: str
    database_type: DatabaseType
    message: str


class ScanSummary(BaseModel):
    snapshot_id: int | None
    created_at: datetime
    sources_scanned: int
    sources_failed: int
    errors: list[ScanError] = Field(default_factory=list)
