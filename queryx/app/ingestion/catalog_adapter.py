from __future__ import annotations

from typing import Any

from queryx.app.ingestion.models import AssetSchemaDiff, InspectionResult


def inspection_to_technical_metadata(inspection: InspectionResult) -> dict[str, Any]:
    """Map deterministic file inspection to a catalog-neutral technical schema.

    The adapter deliberately emits no semantic annotations and does not pretend
    that a managed file is an external database source.
    """
    return {
        "entity_kind": "file",
        "format": inspection.format.value,
        "fields": [field.model_dump(mode="json") for field in inspection.fields],
    }


def compare_technical_metadata(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    previous_version_id: str | None,
    current_version_id: str,
) -> AssetSchemaDiff:
    if previous_version_id is None:
        return AssetSchemaDiff(
            has_drift=False,
            previous_version_id=None,
            current_version_id=current_version_id,
        )
    previous_fields = {field["name"]: field for field in (previous or {}).get("fields", [])}
    current_fields = {field["name"]: field for field in current.get("fields", [])}
    shared = sorted(previous_fields.keys() & current_fields.keys())
    type_changes = [
        {
            "field": name,
            "previous": str(previous_fields[name].get("data_type")),
            "current": str(current_fields[name].get("data_type")),
        }
        for name in shared
        if previous_fields[name].get("data_type") != current_fields[name].get("data_type")
    ]
    nullability_changes = [
        {
            "field": name,
            "previous": bool(previous_fields[name].get("nullable", True)),
            "current": bool(current_fields[name].get("nullable", True)),
        }
        for name in shared
        if previous_fields[name].get("nullable") != current_fields[name].get("nullable")
    ]
    fields_added = sorted(current_fields.keys() - previous_fields.keys())
    fields_removed = sorted(previous_fields.keys() - current_fields.keys())
    return AssetSchemaDiff(
        has_drift=bool(fields_added or fields_removed or type_changes or nullability_changes),
        previous_version_id=previous_version_id,
        current_version_id=current_version_id,
        fields_added=fields_added,
        fields_removed=fields_removed,
        type_changes=type_changes,
        nullability_changes=nullability_changes,
    )
