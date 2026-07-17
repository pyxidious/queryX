from __future__ import annotations

from datetime import datetime, timezone

from queryx.app.catalog.drift import detect_schema_drift
from queryx.app.catalog.fingerprint import normalized_schema, schema_fingerprint
from queryx.app.catalog.models import SourceScanResult


def _mysql_declared(columns: list[dict], indexes: list[dict] | None = None) -> dict:
    return {
        "tables": [
            {
                "name": "customers",
                "columns": columns,
                "primary_key": {"columns": ["id"]},
                "foreign_keys": [],
                "indexes": indexes or [{"name": "idx_email", "columns": ["email"], "unique": True}],
            }
        ]
    }


def _result(declared: dict, inferred: dict | None = None, source_id: str = "mysql") -> SourceScanResult:
    now = datetime.now(timezone.utc)
    database_type = "mongodb" if source_id == "mongodb" else "mysql"
    return SourceScanResult(
        scan_run_id=1,
        source_id=source_id,
        database_type=database_type,
        scan_status="completed",
        started_at=now,
        finished_at=now,
        duration_ms=1,
        fingerprint=schema_fingerprint(database_type, declared, inferred or {}),
        declared_metadata=declared,
        inferred_metadata=inferred or {},
    )


def test_fingerprint_is_stable_when_order_changes() -> None:
    first = _mysql_declared(
        [
            {"name": "id", "type": "INTEGER", "nullable": False},
            {"name": "email", "type": "VARCHAR", "nullable": False},
        ],
        [
            {"name": "idx_email", "columns": ["email"], "unique": True},
            {"name": "idx_id", "columns": ["id"], "unique": False},
        ],
    )
    second = _mysql_declared(
        [
            {"name": "email", "type": "VARCHAR", "nullable": False},
            {"name": "id", "type": "INTEGER", "nullable": False},
        ],
        [
            {"name": "idx_id", "columns": ["id"], "unique": False},
            {"name": "idx_email", "columns": ["email"], "unique": True},
        ],
    )

    assert schema_fingerprint("mysql", first, {}) == schema_fingerprint("mysql", second, {})


def test_fingerprint_changes_for_field_type_and_primary_key_changes() -> None:
    base = _mysql_declared(
        [
            {"name": "id", "type": "INTEGER", "nullable": False},
            {"name": "email", "type": "VARCHAR", "nullable": False},
        ]
    )
    with_field = _mysql_declared(
        [
            {"name": "id", "type": "INTEGER", "nullable": False},
            {"name": "email", "type": "VARCHAR", "nullable": False},
            {"name": "name", "type": "VARCHAR", "nullable": True},
        ]
    )
    with_type = _mysql_declared(
        [
            {"name": "id", "type": "BIGINT", "nullable": False},
            {"name": "email", "type": "VARCHAR", "nullable": False},
        ]
    )
    with_pk = _mysql_declared(
        [
            {"name": "id", "type": "INTEGER", "nullable": False},
            {"name": "email", "type": "VARCHAR", "nullable": False},
        ]
    )
    with_pk["tables"][0]["primary_key"] = {"columns": ["email"]}

    base_fp = schema_fingerprint("mysql", base, {})
    assert schema_fingerprint("mysql", with_field, {}) != base_fp
    assert schema_fingerprint("mysql", with_type, {}) != base_fp
    assert schema_fingerprint("mysql", with_pk, {}) != base_fp


def test_drift_detects_mysql_field_type_and_index_changes() -> None:
    previous = _result(
        _mysql_declared(
            [
                {"name": "id", "type": "INTEGER", "nullable": False},
                {"name": "email", "type": "VARCHAR", "nullable": False},
                {"name": "legacy", "type": "VARCHAR", "nullable": True},
            ],
            [{"name": "idx_email", "columns": ["email"], "unique": True}],
        )
    )
    current = _result(
        _mysql_declared(
            [
                {"name": "id", "type": "BIGINT", "nullable": False},
                {"name": "email", "type": "VARCHAR", "nullable": False},
                {"name": "name", "type": "VARCHAR", "nullable": True},
            ],
            [
                {"name": "idx_email", "columns": ["email"], "unique": True},
                {"name": "idx_name", "columns": ["name"], "unique": False},
            ],
        )
    )

    report = detect_schema_drift("mysql", previous, current)
    change_types = {change.change_type for change in report.changes}

    assert report.has_drift is True
    assert "field_added" in change_types
    assert "field_removed" in change_types
    assert "type_changed" in change_types
    assert "index_added" in change_types


def test_drift_detects_new_mongodb_observed_type() -> None:
    declared = {"collections": [{"name": "events", "indexes": [{"name": "_id_", "keys": [["_id", 1]]}]}]}
    previous = _result(
        declared,
        {"collections": [{"name": "events", "fields": [{"path": "score", "types": ["int"]}]}]},
        "mongodb",
    )
    current = _result(
        declared,
        {"collections": [{"name": "events", "fields": [{"path": "score", "types": ["int", "str"]}]}]},
        "mongodb",
    )

    report = detect_schema_drift("mongodb", previous, current)

    assert any(change.change_type == "mongo_type_added" for change in report.changes)
    assert report.severity == "medium"


def test_failed_scan_is_not_compared_for_drift() -> None:
    now = datetime.now(timezone.utc)
    previous = _result(_mysql_declared([{"name": "id", "type": "INTEGER", "nullable": False}]))
    failed = SourceScanResult(
        scan_run_id=2,
        source_id="mysql",
        database_type="mysql",
        scan_status="failed",
        started_at=now,
        finished_at=now,
        duration_ms=1,
        error={"code": "source_unavailable", "message": "down"},
    )

    report = detect_schema_drift("mysql", previous, failed)

    assert report.has_drift is False
    assert not any(change.change_type in {"table_removed", "field_removed"} for change in report.changes)


def test_mongodb_id_index_is_unique_in_normalized_schema() -> None:
    normalized = normalized_schema(
        "mongodb",
        {"collections": [{"name": "events", "indexes": [{"name": "_id_", "keys": [["_id", 1]]}]}]},
        {"collections": [{"name": "events", "fields": []}]},
    )

    assert normalized["collections"][0]["indexes"][0]["unique"] is True
