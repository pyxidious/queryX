from __future__ import annotations

import json
from typing import Any

from queryx.app.catalog.fingerprint import normalized_schema
from queryx.app.catalog.models import DatabaseType, DriftChange, DriftReport, DriftSeverity, SourceScanResult


def detect_schema_drift(
    database_type: DatabaseType,
    previous: SourceScanResult | None,
    current: SourceScanResult | None,
) -> DriftReport:
    if previous is None or current is None:
        return _empty_report(previous, current)
    if previous.scan_status != "completed" or current.scan_status != "completed":
        return _empty_report(previous, current)

    previous_schema = normalized_schema(
        database_type,
        previous.declared_metadata,
        previous.inferred_metadata,
    )
    current_schema = normalized_schema(
        database_type,
        current.declared_metadata,
        current.inferred_metadata,
    )
    changes = (
        _compare_mysql(previous_schema, current_schema)
        if database_type == "mysql"
        else _compare_mongodb(previous_schema, current_schema)
    )
    severity = drift_severity(changes)
    return DriftReport(
        has_drift=bool(changes),
        severity=severity,
        previous_fingerprint=previous.fingerprint,
        current_fingerprint=current.fingerprint,
        previous_scan_id=previous.scan_run_id,
        current_scan_id=current.scan_run_id,
        changes=changes,
    )


def drift_severity(changes: list[DriftChange]) -> DriftSeverity:
    if not changes:
        return "none"
    order: dict[DriftSeverity, int] = {"none": 0, "low": 1, "medium": 2, "high": 3}
    return max((change.severity for change in changes), key=lambda item: order[item])


def _empty_report(
    previous: SourceScanResult | None,
    current: SourceScanResult | None,
) -> DriftReport:
    return DriftReport(
        has_drift=False,
        severity="none",
        previous_fingerprint=previous.fingerprint if previous else None,
        current_fingerprint=current.fingerprint if current else None,
        previous_scan_id=previous.scan_run_id if previous else None,
        current_scan_id=current.scan_run_id if current else None,
        changes=[],
    )


def _compare_mysql(previous: dict[str, Any], current: dict[str, Any]) -> list[DriftChange]:
    changes: list[DriftChange] = []
    previous_tables = _by_name(previous.get("tables", []))
    current_tables = _by_name(current.get("tables", []))
    changes.extend(_entity_changes(previous_tables, current_tables, "table"))

    for table_name in sorted(previous_tables.keys() & current_tables.keys()):
        previous_table = previous_tables[table_name]
        current_table = current_tables[table_name]
        path = f"tables.{table_name}"
        changes.extend(
            _field_changes(
                _by_name(previous_table.get("columns", [])),
                _by_name(current_table.get("columns", [])),
                path,
                type_label="type",
                nullable=True,
            )
        )
        if previous_table.get("primary_key") != current_table.get("primary_key"):
            changes.append(
                DriftChange(
                    change_type="primary_key_changed",
                    path=f"{path}.primary_key",
                    severity="high",
                    previous=previous_table.get("primary_key"),
                    current=current_table.get("primary_key"),
                )
            )
        _append_modified(
            changes,
            f"{path}.foreign_keys",
            previous_table.get("foreign_keys", []),
            current_table.get("foreign_keys", []),
            "foreign_key",
            "medium",
        )
        _append_modified(
            changes,
            f"{path}.indexes",
            previous_table.get("indexes", []),
            current_table.get("indexes", []),
            "index",
            "low",
        )
    return changes


def _compare_mongodb(previous: dict[str, Any], current: dict[str, Any]) -> list[DriftChange]:
    changes: list[DriftChange] = []
    previous_collections = _by_name(previous.get("collections", []))
    current_collections = _by_name(current.get("collections", []))
    changes.extend(_entity_changes(previous_collections, current_collections, "collection"))

    for collection_name in sorted(previous_collections.keys() & current_collections.keys()):
        previous_collection = previous_collections[collection_name]
        current_collection = current_collections[collection_name]
        path = f"collections.{collection_name}"
        changes.extend(
            _field_changes(
                {field["path"]: field for field in previous_collection.get("fields", [])},
                {field["path"]: field for field in current_collection.get("fields", [])},
                path,
                type_label="types",
                nullable=False,
            )
        )
        _append_modified(
            changes,
            f"{path}.indexes",
            previous_collection.get("indexes", []),
            current_collection.get("indexes", []),
            "index",
            "low",
        )
        if previous_collection.get("validator") != current_collection.get("validator"):
            change_type = "validator_modified"
            if previous_collection.get("validator") is None:
                change_type = "validator_added"
            elif current_collection.get("validator") is None:
                change_type = "validator_removed"
            changes.append(
                DriftChange(
                    change_type=change_type,
                    path=f"{path}.validator",
                    severity="medium",
                    previous=previous_collection.get("validator"),
                    current=current_collection.get("validator"),
                )
            )
    return changes


def _entity_changes(
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    label: str,
) -> list[DriftChange]:
    changes: list[DriftChange] = []
    for name in sorted(current.keys() - previous.keys()):
        changes.append(DriftChange(change_type=f"{label}_added", path=name, severity="low"))
    for name in sorted(previous.keys() - current.keys()):
        changes.append(DriftChange(change_type=f"{label}_removed", path=name, severity="high"))
    return changes


def _field_changes(
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    path: str,
    type_label: str,
    nullable: bool,
) -> list[DriftChange]:
    changes: list[DriftChange] = []
    for name in sorted(current.keys() - previous.keys()):
        severity: DriftSeverity = "low"
        if nullable and current[name].get("nullable") is False:
            severity = "medium"
        changes.append(DriftChange(change_type="field_added", path=f"{path}.{name}", severity=severity))
    for name in sorted(previous.keys() - current.keys()):
        changes.append(DriftChange(change_type="field_removed", path=f"{path}.{name}", severity="high"))
    for name in sorted(previous.keys() & current.keys()):
        previous_types = previous[name].get(type_label)
        current_types = current[name].get(type_label)
        if previous_types != current_types:
            change_type = "type_changed"
            severity: DriftSeverity = "high"
            if type_label == "types":
                previous_set = set(previous_types or [])
                current_set = set(current_types or [])
                if current_set > previous_set:
                    change_type = "mongo_type_added"
                    severity = "medium"
                elif previous_set > current_set:
                    change_type = "mongo_type_removed"
                    severity = "medium"
            changes.append(
                DriftChange(
                    change_type=change_type,
                    path=f"{path}.{name}",
                    severity=severity,
                    previous=previous_types,
                    current=current_types,
                )
            )
        if nullable and previous[name].get("nullable") != current[name].get("nullable"):
            changes.append(
                DriftChange(
                    change_type="nullability_changed",
                    path=f"{path}.{name}",
                    severity="medium",
                    previous=previous[name].get("nullable"),
                    current=current[name].get("nullable"),
                )
            )
    return changes


def _append_modified(
    changes: list[DriftChange],
    path: str,
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    label: str,
    severity: DriftSeverity,
) -> None:
    previous_by_key = {_identity(item): item for item in previous}
    current_by_key = {_identity(item): item for item in current}
    for key in sorted(current_by_key.keys() - previous_by_key.keys()):
        changes.append(DriftChange(change_type=f"{label}_added", path=f"{path}.{key}", severity=severity))
    for key in sorted(previous_by_key.keys() - current_by_key.keys()):
        changes.append(DriftChange(change_type=f"{label}_removed", path=f"{path}.{key}", severity="medium"))
    for key in sorted(previous_by_key.keys() & current_by_key.keys()):
        if previous_by_key[key] != current_by_key[key]:
            changes.append(
                DriftChange(
                    change_type=f"{label}_modified",
                    path=f"{path}.{key}",
                    severity="medium",
                    previous=previous_by_key[key],
                    current=current_by_key[key],
                )
            )


def _by_name(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("name")): item for item in items}


def _identity(item: dict[str, Any]) -> str:
    return str(item.get("name") or json.dumps(item, sort_keys=True))
