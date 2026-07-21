from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from queryx.app.catalog.models import DataSource
from queryx.app.ingestion.models import (
    AssetSchemaDiff,
    AssetVersion,
    DataAsset,
    DatasetProvenance,
    IngestionJob,
    StorageBinding,
)
from queryx.app.processing.models import ProcessingRun
from queryx.app.ui.formatting import (
    abbreviate,
    format_bytes,
    format_timestamp,
    format_value,
    status_class,
    structured_message,
)
from queryx.app.worker.models import WorkItem


@dataclass(frozen=True)
class BadgeVM:
    label: str
    css_class: str


@dataclass(frozen=True)
class AlertVM:
    level: str
    message: str


@dataclass(frozen=True)
class WorkItemVM:
    id: str
    short_id: str
    status: BadgeVM
    attempts: str
    claimed_by: str
    heartbeat: str
    cancellation_requested: bool
    error: str | None


@dataclass(frozen=True)
class JobVM:
    id: str
    short_id: str
    status: BadgeVM
    filename: str
    logical_name: str
    bytes_received: str
    records_detected: str
    created_at: str
    updated_at: str
    requested_asset_id: str | None
    asset_id: str | None
    asset_version_id: str | None
    warnings: list[str]
    error: str | None
    work_item: WorkItemVM | None
    terminal: bool
    can_cancel: bool
    provenance: ProvenanceVM


@dataclass(frozen=True)
class AssetVM:
    id: str
    short_id: str
    name: str
    kind: str
    latest_version_id: str | None
    latest_version_number: str
    updated_at: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DriftVM:
    has_drift: bool
    added: list[str]
    removed: list[str]
    type_changes: list[str]
    nullability_changes: list[str]


@dataclass(frozen=True)
class VersionVM:
    id: str
    short_id: str
    asset_id: str
    number: int
    status: BadgeVM
    created_at: str
    source_fingerprint: str
    schema_fingerprint: str
    recipe_fingerprint: str
    drift: DriftVM | None
    provenance: list[ProvenanceVM]


@dataclass(frozen=True)
class ProvenanceVM:
    provider: str
    reference: str
    version: str
    title: str
    license_name: str
    notes: str


@dataclass(frozen=True)
class BindingVM:
    id: str
    short_id: str
    role: str
    backend: str
    status: BadgeVM
    format: str
    recipe_fingerprint: str
    content_fingerprint: str
    schema_fingerprint: str
    updated_at: str


@dataclass(frozen=True)
class SchemaFieldVM:
    name: str
    data_type: str
    nullable: str


@dataclass(frozen=True)
class PreviewVM:
    source: str
    columns: list[str]
    rows: list[list[str]]
    truncated_columns: bool = False


@dataclass(frozen=True)
class ProcessingRunVM:
    id: str
    short_id: str
    version_id: str
    status: BadgeVM
    operation: str
    recipe_name: str
    recipe_fingerprint: str
    records_read: int
    records_written: int
    records_rejected: int
    bytes_written: str
    created_at: str
    updated_at: str
    warnings: list[str]
    errors: list[str]
    work_item: WorkItemVM | None
    terminal: bool
    partial: bool
    can_cancel: bool


@dataclass(frozen=True)
class SourceVM:
    id: str
    name: str
    database_type: str
    enabled: bool
    freshness: BadgeVM
    warning: str | None
    fingerprint: str


@dataclass(frozen=True)
class ScanVM:
    status: BadgeVM
    finished_at: str
    fingerprint: str
    warnings: list[str]


def badge(status: Any) -> BadgeVM:
    value = str(status)
    return BadgeVM(value, status_class(value))


def work_item_vm(item: WorkItem | None) -> WorkItemVM | None:
    if item is None:
        return None
    return WorkItemVM(
        id=item.id,
        short_id=abbreviate(item.id),
        status=badge(item.status),
        attempts=f"{item.attempt_count}/{item.max_attempts}",
        claimed_by=item.claimed_by or "—",
        heartbeat=format_timestamp(item.heartbeat_at),
        cancellation_requested=item.cancellation_requested,
        error=structured_message(item.last_error),
    )


def job_vm(job: IngestionJob, item: WorkItem | None = None) -> JobVM:
    terminal = str(job.status) in {"ready", "failed", "cancelled"}
    return JobVM(
        id=job.id,
        short_id=abbreviate(job.id),
        status=badge(job.status),
        filename=job.original_filename,
        logical_name=job.logical_name or "—",
        bytes_received=format_bytes(job.bytes_received),
        records_detected=str(job.records_detected) if job.records_detected is not None else "—",
        created_at=format_timestamp(job.created_at),
        updated_at=format_timestamp(job.updated_at),
        requested_asset_id=job.requested_asset_id,
        asset_id=job.asset_id,
        asset_version_id=job.asset_version_id,
        warnings=[structured_message(value) or "Warning" for value in job.warnings],
        error=structured_message(job.error),
        work_item=work_item_vm(item),
        terminal=terminal,
        can_cancel=not terminal,
        provenance=provenance_vm(job.provenance),
    )


def asset_vm(asset: DataAsset, warnings: list[str] | None = None) -> AssetVM:
    return AssetVM(
        id=asset.id,
        short_id=abbreviate(asset.id),
        name=asset.name,
        kind=str(asset.asset_kind),
        latest_version_id=asset.latest_version_id,
        latest_version_number=str(asset.latest_version_number or "—"),
        updated_at=format_timestamp(asset.updated_at),
        warnings=warnings or [],
    )


def drift_vm(diff: AssetSchemaDiff | None) -> DriftVM | None:
    if diff is None:
        return None
    return DriftVM(
        has_drift=diff.has_drift,
        added=diff.fields_added,
        removed=diff.fields_removed,
        type_changes=[f"{item.field}: {item.previous} → {item.current}" for item in diff.type_changes],
        nullability_changes=[
            f"{item.field}: {item.previous} → {item.current}" for item in diff.nullability_changes
        ],
    )


def version_vm(version: AssetVersion) -> VersionVM:
    return VersionVM(
        id=version.id,
        short_id=abbreviate(version.id),
        asset_id=version.asset_id,
        number=version.version_number,
        status=badge(version.status),
        created_at=format_timestamp(version.created_at),
        source_fingerprint=abbreviate(version.source_fingerprint),
        schema_fingerprint=abbreviate(version.schema_fingerprint),
        recipe_fingerprint=abbreviate(version.recipe_fingerprint),
        drift=drift_vm(version.schema_diff),
        provenance=[provenance_vm(item) for item in version.provenance],
    )


def provenance_vm(provenance: DatasetProvenance) -> ProvenanceVM:
    return ProvenanceVM(
        provider=str(provenance.source_provider),
        reference=provenance.source_reference or "—",
        version=provenance.source_version or "—",
        title=provenance.dataset_title or "—",
        license_name=provenance.license_name or "—",
        notes=provenance.notes or "—",
    )


def binding_vm(binding: StorageBinding) -> BindingVM:
    return BindingVM(
        id=binding.id,
        short_id=abbreviate(binding.id),
        role=str(binding.binding_role),
        backend=str(binding.backend_type),
        status=badge(binding.status),
        format=str(binding.format) if binding.format else "—",
        recipe_fingerprint=abbreviate(binding.recipe_fingerprint),
        content_fingerprint=abbreviate(binding.content_fingerprint),
        schema_fingerprint=abbreviate(binding.schema_fingerprint),
        updated_at=format_timestamp(binding.updated_at or binding.created_at),
    )


def schema_vm(fields: list[dict[str, Any]]) -> list[SchemaFieldVM]:
    return [
        SchemaFieldVM(
            name=str(item.get("name", "—")),
            data_type=str(item.get("data_type", "—")),
            nullable="sì" if item.get("nullable", True) else "no",
        )
        for item in fields
    ]


def preview_vm(payload: dict[str, Any], source: str, max_columns: int) -> PreviewVM:
    rows = payload.get("rows") or []
    schema = payload.get("schema") or []
    columns = [str(item.get("name")) for item in schema if isinstance(item, dict) and item.get("name")]
    if not columns and rows:
        columns = [str(value) for value in rows[0].keys()]
    truncated = len(columns) > max_columns
    selected = columns[:max_columns]
    return PreviewVM(
        source=source,
        columns=selected,
        rows=[[format_value(row.get(column)) for column in selected] for row in rows if isinstance(row, dict)],
        truncated_columns=truncated,
    )


def processing_run_vm(run: ProcessingRun, item: WorkItem | None = None) -> ProcessingRunVM:
    terminal = str(run.status) in {"completed", "failed", "cancelled"}
    return ProcessingRunVM(
        id=run.id,
        short_id=abbreviate(run.id),
        version_id=run.asset_version_id,
        status=badge(run.status),
        operation=str(run.operation),
        recipe_name=run.recipe_name,
        recipe_fingerprint=abbreviate(run.recipe_fingerprint),
        records_read=run.records_read,
        records_written=run.records_written,
        records_rejected=run.records_rejected,
        bytes_written=format_bytes(run.bytes_written),
        created_at=format_timestamp(run.created_at),
        updated_at=format_timestamp(run.updated_at),
        warnings=[structured_message(value) or "Warning" for value in run.warnings],
        errors=[structured_message(value) or "Errore" for value in run.errors],
        work_item=work_item_vm(item),
        terminal=terminal,
        partial=str(run.status) == "partial",
        can_cancel=not terminal,
    )


def source_vm(source: DataSource, freshness: Any | None = None) -> SourceVM:
    status = str(getattr(freshness, "freshness_status", "unknown"))
    return SourceVM(
        id=source.id,
        name=source.name,
        database_type=str(source.database_type),
        enabled=source.enabled,
        freshness=badge(status),
        warning=getattr(freshness, "warning", None),
        fingerprint=abbreviate(getattr(freshness, "fingerprint", None)),
    )


def scan_vm(scan: Any) -> ScanVM:
    return ScanVM(
        status=badge(scan.scan_status),
        finished_at=format_timestamp(scan.finished_at),
        fingerprint=abbreviate(scan.fingerprint),
        warnings=[str(value) for value in scan.warnings],
    )
