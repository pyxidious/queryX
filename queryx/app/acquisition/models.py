from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AcquisitionStatus(StrEnum):
    CREATED = "created"
    INSPECTING = "inspecting"
    AWAITING_SELECTION = "awaiting_selection"
    DOWNLOADING = "downloading"
    AWAITING_INGESTION = "awaiting_ingestion"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AcquisitionFileStatus(StrEnum):
    DISCOVERED = "discovered"
    SELECTED = "selected"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    QUEUED_FOR_INGESTION = "queued_for_ingestion"
    READY = "ready"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AcquisitionRun(BaseModel):
    id: str
    provider: str = "kaggle"
    dataset_reference: str
    requested_version: str
    resolved_version: str | None = None
    title: str | None = None
    license_name: str | None = None
    request_fingerprint: str | None = None
    status: AcquisitionStatus
    files_total: int = 0
    files_selected: int = 0
    files_downloaded: int = 0
    files_ready: int = 0
    files_failed: int = 0
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime
    finished_at: datetime | None = None


class AcquisitionFile(BaseModel):
    id: str
    acquisition_run_id: str
    provider_file_reference: str
    display_name: str
    size_bytes: int | None = None
    format: str
    selected: bool = False
    logical_name: str | None = None
    target_asset_id: str | None = None
    status: AcquisitionFileStatus
    ingestion_job_id: str | None = None
    asset_id: str | None = None
    asset_version_id: str | None = None
    content_fingerprint: str | None = None
    warning: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class ManifestFile(BaseModel):
    reference: str
    name: str
    size_bytes: int | None = Field(default=None, ge=0)


class DatasetManifest(BaseModel):
    dataset_reference: str
    resolved_version: str
    title: str | None = None
    license_name: str | None = None
    files: list[ManifestFile]


class FileSelection(BaseModel):
    file_id: str
    logical_name: str | None = Field(default=None, max_length=200)
    target_asset_id: str | None = None


class AcquisitionReconciliationReport(BaseModel):
    runs_updated: list[str] = Field(default_factory=list)
    missing_jobs: list[str] = Field(default_factory=list)
    jobs_requeued: list[str] = Field(default_factory=list)
    orphan_temporaries: list[str] = Field(default_factory=list)

