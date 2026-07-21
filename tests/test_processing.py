from __future__ import annotations

import asyncio
import io
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from starlette.datastructures import UploadFile

from queryx.app.core.config import Settings
from queryx.app.ingestion.service import IngestionService
from queryx.app.ingestion.models import BindingRole, DataFormat, InspectionResult, SchemaField
from queryx.app.processing.models import ProcessingStatus
from queryx.app.processing.normalizers.parquet import (
    CanonicalParquetNormalizer,
    NormalizationError,
    _cast_batch,
)
from queryx.app.processing.recipe import canonical_parquet_recipe
from queryx.app.processing.service import ProcessingService, ProcessingServiceError
from queryx.app.processing.serving.duckdb import DuckDBServingAdapter


def _settings(tmp_path: Path, **updates: Any) -> Settings:
    values: dict[str, Any] = {
        "catalog_db_path": tmp_path / "catalog.sqlite3",
        "data_raw_dir": tmp_path / "data" / "raw",
        "data_staging_dir": tmp_path / "data" / "staging",
        "data_normalized_dir": tmp_path / "data" / "normalized",
        "duckdb_path": tmp_path / "data" / "queryx.duckdb",
        "duckdb_schema": "queryx_managed",
        "processing_preview_rows": 2,
        "processing_stale_run_seconds": 10,
        "parquet_batch_rows": 2,
        "ingestion_preview_rows": 2,
        "ingestion_inspection_rows": 20,
        "ingestion_csv_count_rows": 100,
        "mysql_enabled": False,
        "mongodb_enabled": False,
    }
    values.update(updates)
    return Settings(**values)


def _upload(settings: Settings, content: bytes, filename: str = "data.csv"):
    stream = tempfile.SpooledTemporaryFile()
    stream.write(content)
    stream.seek(0)
    return asyncio.run(
        IngestionService(settings).ingest_upload(UploadFile(stream, filename=filename))
    )


def _parquet_bytes() -> bytes:
    stream = io.BytesIO()
    pq.write_table(pa.table({"id": [1, 2, 3], "name": ["Ada", "Grace", "Linus"]}), stream, compression=None)
    return stream.getvalue()


def test_csv_to_canonical_parquet_duckdb_and_preview(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploaded = _upload(settings, b"id,name\n1,Ada\n2,Grace\n3,Linus\n")
    service = ProcessingService(settings)

    run = service.prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")
    bindings = service.list_bindings(uploaded.asset_id or "", uploaded.asset_version_id or "")
    preview = service.data_preview(uploaded.asset_id or "", uploaded.asset_version_id or "", 2)

    assert run.status == ProcessingStatus.COMPLETED
    assert run.records_read == run.records_written == 3
    assert run.records_rejected == 0
    assert run.observed_schema[0]["data_type"] == "integer"
    assert run.canonical_schema[0]["data_type"] == "int64"
    assert run.serving_schema[0]["data_type"] == "BIGINT"
    assert bindings is not None
    assert [(binding.binding_role, binding.status) for binding in bindings] == [
        ("raw", "ready"),
        ("normalized", "ready"),
        ("serving", "ready"),
    ]
    normalized = next(binding for binding in bindings if binding.binding_role == "normalized")
    assert (settings.data_normalized_dir / Path(normalized.physical_location).name).is_file()
    assert service.serving.view_exists(next(binding for binding in bindings if binding.binding_role == "serving").metadata["relation"])
    assert len(preview["rows"]) == 2
    assert preview["rows"][0] == {"id": 1, "name": "Ada"}
    with pytest.raises(ProcessingServiceError) as exc:
        service.data_preview(uploaded.asset_id or "", uploaded.asset_version_id or "", 3)
    assert exc.value.code == "preview_limit_exceeded"


def test_parquet_is_rewritten_canonically_not_copied(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    raw_content = _parquet_bytes()
    uploaded = _upload(settings, raw_content, "source.parquet")
    service = ProcessingService(settings)

    run = service.prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")
    binding = service.storage.get_binding(run.normalized_binding_id or "")
    assert binding is not None
    normalized_path = settings.data_normalized_dir / Path(binding.physical_location).name

    assert run.status == ProcessingStatus.COMPLETED
    assert normalized_path.read_bytes() != raw_content
    assert pq.ParquetFile(normalized_path).metadata.num_rows == 3
    assert [field["name"] for field in run.canonical_schema] == ["id", "name"]


def test_recipe_and_canonical_schema_are_deterministic(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploaded = _upload(settings, b"b,a\n2,1\n")
    ingestion = IngestionService(settings)
    inspection = ingestion.storage.get_version_inspection(uploaded.asset_version_id or "")
    assert inspection is not None

    first = canonical_parquet_recipe(inspection, "zstd")
    second = canonical_parquet_recipe(inspection, "zstd")
    run = ProcessingService(settings).prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")

    assert first.fingerprint == second.fingerprint
    assert first.column_order == ["b", "a"]
    assert [field["name"] for field in run.canonical_schema] == ["b", "a"]


def test_different_files_produce_different_normalized_fingerprints(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = _upload(settings, b"id\n1\n")
    second = _upload(settings, b"id\n2\n")
    service = ProcessingService(settings)

    run_a = service.prepare(first.asset_id or "", first.asset_version_id or "")
    run_b = service.prepare(second.asset_id or "", second.asset_version_id or "")
    binding_a = service.storage.get_binding(run_a.normalized_binding_id or "")
    binding_b = service.storage.get_binding(run_b.normalized_binding_id or "")

    assert binding_a is not None and binding_b is not None
    assert binding_a.content_fingerprint != binding_b.content_fingerprint


def test_strict_conversion_failure_removes_temporary_output(tmp_path: Path) -> None:
    settings = _settings(tmp_path, ingestion_inspection_rows=1)
    uploaded = _upload(settings, b"id\n1\nnot-an-integer\n")
    service = ProcessingService(settings)

    run = service.prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")

    assert run.status == ProcessingStatus.FAILED
    assert run.errors[0]["code"] == "strict_conversion_failed"
    assert not list(settings.data_normalized_dir.iterdir())
    assert service.ingestion_storage.get_version(uploaded.asset_id or "", uploaded.asset_version_id or "").status == "ready"  # type: ignore[union-attr]


def test_sampled_csv_optional_timestamps_normalize_empty_fields_as_null(tmp_path: Path) -> None:
    settings = _settings(tmp_path, ingestion_inspection_rows=1, parquet_batch_rows=2)
    uploaded = _upload(
        settings,
        b"order_id,order_approved_at,order_delivered_customer_date\n"
        b"a1,2018-01-01 10:00:00,2018-01-03 12:00:00\n"
        b"a2,2018-01-02 11:00:00,\n"
        b"a3,,\n",
        "olist_orders_dataset.csv",
    )
    service = ProcessingService(settings)

    run = service.prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")
    binding = service.storage.get_binding(run.normalized_binding_id or "")
    assert binding is not None
    table = pq.read_table(settings.data_normalized_dir / Path(binding.physical_location).name)

    assert run.status == ProcessingStatus.COMPLETED
    assert run.records_written == 3
    assert all(field["nullable"] for field in run.canonical_schema)
    assert table.column("order_delivered_customer_date").to_pylist()[1:] == [None, None]
    assert table.column("order_approved_at").to_pylist()[2] is None


@pytest.mark.parametrize(
    ("content", "column_name", "expected_type", "secret_value"),
    [
        (
            b"event_at\n2018-01-01 10:00:00\ndefinitely-secret-invalid-timestamp\n",
            "event_at",
            "timestamp[us]",
            "definitely-secret-invalid-timestamp",
        ),
        (b"amount\n10\nsecret-number\n", "amount", "int64", "secret-number"),
        (
            b"amount\n10\n999999999999999999999999999999999\n",
            "amount",
            "int64",
            "999999999999999999999999999999999",
        ),
    ],
)
def test_strict_conversion_error_is_structured_without_source_value(
    tmp_path: Path,
    content: bytes,
    column_name: str,
    expected_type: str,
    secret_value: str,
) -> None:
    settings = _settings(tmp_path, ingestion_inspection_rows=1)
    uploaded = _upload(settings, content)

    run = ProcessingService(settings).prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")
    error = run.errors[0]

    assert run.status == ProcessingStatus.FAILED
    assert error["code"] == "strict_conversion_failed"
    assert error["column_name"] == column_name
    assert error["expected_type"] == expected_type
    assert error["reason"] == "type_conversion_failed"
    assert error["batch_number"] == 1
    assert error["row_number"] == 2
    assert secret_value not in str(error)
    assert not list(settings.data_normalized_dir.glob(".tmp-*.parquet"))


def test_strict_policy_rejects_incompatible_csv_structure_without_exposing_value(tmp_path: Path) -> None:
    settings = _settings(tmp_path, ingestion_inspection_rows=1)
    uploaded = _upload(settings, b"id\n1\n2,secret-extra-field\n")

    run = ProcessingService(settings).prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")
    error = run.errors[0]

    assert run.status == ProcessingStatus.FAILED
    assert error["code"] == "strict_conversion_failed"
    assert error["reason"] == "type_conversion_failed"
    assert "secret-extra-field" not in str(error)


def test_legacy_sampled_csv_non_nullable_schema_is_treated_conservatively(tmp_path: Path) -> None:
    source = tmp_path / "legacy.csv"
    destination = tmp_path / "normalized.parquet"
    source.write_text("event_at,marker\n2018-01-01 10:00:00,a\n,b\n", encoding="utf-8")
    inspection = InspectionResult(
        format=DataFormat.CSV,
        fields=[
            SchemaField(name="event_at", data_type="datetime", nullable=False),
            SchemaField(name="marker", data_type="string", nullable=False),
        ],
        metadata={"delimiter": ",", "sampled_rows": 1},
    )

    result = CanonicalParquetNormalizer().normalize(
        source,
        destination,
        inspection,
        canonical_parquet_recipe(inspection),
    )

    assert result.records_written == 2
    assert result.canonical_schema[0]["nullable"] is True
    assert pq.read_table(destination).column("event_at").to_pylist()[1] is None


def test_declared_non_nullable_schema_still_rejects_null_with_structured_reason() -> None:
    batch = pa.record_batch([pa.array([1, None], type=pa.int64())], names=["required_id"])
    schema = pa.schema([pa.field("required_id", pa.int64(), nullable=False)])

    with pytest.raises(NormalizationError) as exc:
        _cast_batch(batch, schema, batch_number=1)

    assert exc.value.code == "strict_conversion_failed"
    assert exc.value.details == {
        "column_name": "required_id",
        "expected_type": "int64",
        "reason": "nullability_violation",
        "batch_number": 1,
        "row_number": 2,
    }


class _FailOnceNormalizer:
    def __init__(self) -> None:
        self.delegate = CanonicalParquetNormalizer()
        self.failures = 1

    def normalize(self, *args: Any, **kwargs: Any):
        if self.failures:
            self.failures -= 1
            raise NormalizationError(
                "strict_conversion_failed",
                "A source value is incompatible with the observed schema",
                {
                    "column_name": "id",
                    "expected_type": "int64",
                    "reason": "type_conversion_failed",
                },
            )
        return self.delegate.normalize(*args, **kwargs)


def test_failed_processing_run_can_retry_same_recipe_without_temporary_conflict(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploaded = _upload(settings, b"id\n1\n")
    normalizer = _FailOnceNormalizer()
    service = ProcessingService(settings, normalizer=normalizer)  # type: ignore[arg-type]

    failed = service.prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")
    retried = service.prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")

    assert failed.status == ProcessingStatus.FAILED
    assert retried.status == ProcessingStatus.COMPLETED
    assert retried.id != failed.id
    assert retried.recipe_fingerprint == failed.recipe_fingerprint
    assert not list(settings.data_normalized_dir.glob(".tmp-*.parquet"))


class _FailOnceServing:
    def __init__(self, delegate: DuckDBServingAdapter) -> None:
        self.delegate = delegate
        self.failures = 1

    def register_view(self, relation_name: str, parquet_path: Path):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("simulated DuckDB failure")
        return self.delegate.register_view(relation_name, parquet_path)

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)


def test_partial_run_retries_only_registration(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploaded = _upload(settings, b"id,name\n1,Ada\n")
    flaky = _FailOnceServing(DuckDBServingAdapter(settings.duckdb_path, settings.duckdb_schema))
    service = ProcessingService(settings, serving=flaky)  # type: ignore[arg-type]

    partial = service.prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")
    normalized_before = service.storage.get_binding(partial.normalized_binding_id or "")
    completed = service.prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")
    normalized_after = service.storage.get_binding(completed.normalized_binding_id or "")

    assert partial.status == ProcessingStatus.PARTIAL
    assert normalized_before is not None and normalized_before.status == "ready"
    assert completed.status == ProcessingStatus.COMPLETED
    assert normalized_after is not None and normalized_after.id == normalized_before.id
    assert len(service.storage.list_bindings(uploaded.asset_version_id or "", role=BindingRole.NORMALIZED)) == 1


def test_completed_idempotency_active_conflict_and_different_recipe(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploaded = _upload(settings, b"id\n1\n")
    service = ProcessingService(settings)
    first = service.prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")
    reused = service.prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")
    inspection = service.ingestion_storage.get_version_inspection(uploaded.asset_version_id or "")
    assert inspection is not None
    different_recipe = canonical_parquet_recipe(inspection, "snappy")
    different = service.prepare(uploaded.asset_id or "", uploaded.asset_version_id or "", different_recipe)

    assert reused.id == first.id and reused.reused is True
    assert different.id != first.id
    assert different.recipe_fingerprint != first.recipe_fingerprint
    assert len(service.storage.list_bindings(uploaded.asset_version_id or "")) == 5

    other = _upload(settings, b"id\n9\n")
    other_inspection = service.ingestion_storage.get_version_inspection(other.asset_version_id or "")
    other_raw = service.storage.list_bindings(other.asset_version_id or "")[0]
    recipe = canonical_parquet_recipe(  # type: ignore[arg-type]
        other_inspection,
        "zstd",
        settings.parquet_batch_rows,
    )
    active, _ = service.storage.create_or_reuse_run(
        other.asset_version_id or "",
        other_raw.id,
        recipe.name,
        recipe.version,
        recipe.fingerprint,
        [field.model_dump() for field in other_inspection.fields],  # type: ignore[union-attr]
    )
    service.storage.transition_run(active.id, ProcessingStatus.NORMALIZING)
    with pytest.raises(ProcessingServiceError) as exc:
        service.prepare(other.asset_id or "", other.asset_version_id or "")
    assert exc.value.status_code == 409
    assert exc.value.code == "processing_in_progress"


def test_reconciliation_detects_missing_outputs_and_orphans(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = _upload(settings, b"id\n1\n")
    second = _upload(settings, b"id\n2\n")
    service = ProcessingService(settings)
    normalized_missing = service.prepare(first.asset_id or "", first.asset_version_id or "")
    view_missing = service.prepare(second.asset_id or "", second.asset_version_id or "")

    normalized_binding = service.storage.get_binding(normalized_missing.normalized_binding_id or "")
    serving_binding = service.storage.get_binding(view_missing.serving_binding_id or "")
    assert normalized_binding is not None and serving_binding is not None
    (settings.data_normalized_dir / Path(normalized_binding.physical_location).name).unlink()
    service.serving.drop_view(serving_binding.metadata["relation"])
    orphan = settings.data_normalized_dir / "orphan.parquet"
    orphan.write_bytes(b"orphan")
    existing_normalized = service.storage.get_binding(view_missing.normalized_binding_id or "")
    assert existing_normalized is not None
    service.serving.register_view("asset_orphan_v1_deadbeef", settings.data_normalized_dir / Path(existing_normalized.physical_location).name)

    report = service.reconcile()

    assert normalized_binding.id in report.missing_normalized_bindings
    assert serving_binding.id in report.missing_serving_bindings
    assert normalized_missing.id in report.failed_runs
    assert view_missing.id in report.resumable_partial_runs
    assert "normalized/orphan.parquet" in report.orphan_normalized_files
    assert "asset_orphan_v1_deadbeef" in report.orphan_duckdb_views


def test_stale_run_and_sqlite_initialization_are_reconciled_idempotently(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploaded = _upload(settings, b"id\n1\n")
    service = ProcessingService(settings)
    inspection = service.ingestion_storage.get_version_inspection(uploaded.asset_version_id or "")
    raw = service.storage.list_bindings(uploaded.asset_version_id or "")[0]
    recipe = canonical_parquet_recipe(inspection, "zstd")  # type: ignore[arg-type]
    run, _ = service.storage.create_or_reuse_run(
        uploaded.asset_version_id or "",
        raw.id,
        recipe.name,
        recipe.version,
        recipe.fingerprint,
        [field.model_dump() for field in inspection.fields],  # type: ignore[union-attr]
    )
    service.storage.transition_run(run.id, ProcessingStatus.NORMALIZING)
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with sqlite3.connect(settings.catalog_db_path) as connection:
        connection.execute("UPDATE processing_runs SET updated_at = ? WHERE id = ?", (old, run.id))

    report = service.reconcile()
    ProcessingService(settings)

    assert run.id in report.stale_runs
    assert run.id in report.failed_runs
    with sqlite3.connect(settings.catalog_db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_version WHERE version = 7").fetchone()[0] == 1


def test_legacy_binding_is_migrated_as_raw_ready(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE data_assets (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, asset_kind TEXT NOT NULL,
                description TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE asset_versions (
                id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, version_number INTEGER NOT NULL,
                source_fingerprint TEXT NOT NULL, schema_fingerprint TEXT,
                recipe_fingerprint TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(asset_id, version_number)
            );
            CREATE TABLE storage_bindings (
                id TEXT PRIMARY KEY, asset_version_id TEXT NOT NULL, backend_type TEXT NOT NULL,
                physical_location TEXT NOT NULL, format TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(backend_type, physical_location)
            );
            """
        )
        connection.execute("INSERT INTO data_assets VALUES ('a', 'legacy', 'file', NULL, ?, ?)", (now, now))
        connection.execute("INSERT INTO asset_versions VALUES ('v', 'a', 1, 'x', NULL, NULL, 'ready', ?)", (now,))
        connection.execute("INSERT INTO storage_bindings VALUES ('b', 'v', 'file', 'raw/legacy.csv', 'csv', ?)", (now,))

    settings = Settings(
        catalog_db_path=db_path,
        data_raw_dir=tmp_path / "raw",
        data_staging_dir=tmp_path / "stage",
        data_normalized_dir=tmp_path / "normalized",
        duckdb_path=tmp_path / "queryx.duckdb",
    )
    binding = ProcessingService(settings).storage.get_binding("b")

    assert binding is not None
    assert binding.binding_role == "raw"
    assert binding.status == "ready"
