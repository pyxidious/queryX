from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from starlette.datastructures import UploadFile

from queryx.app.core.config import Settings
from queryx.app.ingestion.catalog_adapter import inspection_to_technical_metadata
from queryx.app.ingestion.fingerprint import technical_schema_fingerprint
from queryx.app.ingestion.models import DataFormat, IngestionStatus
from queryx.app.ingestion.service import IngestionService, IngestionServiceError
from queryx.app.ingestion.storage import IngestionStorage


def _service(tmp_path: Path, **updates: Any) -> IngestionService:
    settings = Settings(
        catalog_db_path=tmp_path / "catalog.sqlite3",
        data_raw_dir=tmp_path / "data" / "raw",
        data_staging_dir=tmp_path / "data" / "staging",
        data_normalized_dir=tmp_path / "data" / "normalized",
        ingestion_preview_rows=2,
        ingestion_inspection_rows=20,
        ingestion_csv_count_rows=100,
        ingestion_stale_job_seconds=10,
        mysql_enabled=False,
        mongodb_enabled=False,
        **updates,
    )
    return IngestionService(settings)


def _upload(
    service: IngestionService,
    content: bytes,
    asset_id: str | None = None,
    filename: str = "data.csv",
):
    stream = tempfile.SpooledTemporaryFile()
    stream.write(content)
    stream.seek(0)
    return asyncio.run(service.ingest_upload(UploadFile(stream, filename=filename), asset_id=asset_id))


def test_new_asset_second_version_progression_and_latest_identity(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _upload(service, b"id,name\n1,Ada\n")
    second = _upload(service, b"id,name\n2,Grace\n", first.asset_id)
    third = _upload(service, b"id,name\n3,Linus\n", first.asset_id)

    asset = service.get_asset(first.asset_id or "")

    assert asset is not None
    assert [version.version_number for version in asset.versions] == [3, 2, 1]
    assert second.asset_id == third.asset_id == first.asset_id
    assert asset.latest_version_id == third.asset_version_id
    assert asset.latest_version_number == 3


def test_idempotent_retry_reuses_version_and_duplicate_asset_warns(tmp_path: Path) -> None:
    service = _service(tmp_path)
    content = b"id,name\n1,Ada\n"
    first = _upload(service, content)
    retry = _upload(service, content, first.asset_id)
    duplicate = _upload(service, content)

    retry_job = service.get_job(retry.job_id)
    duplicate_job = service.get_job(duplicate.job_id)
    original = service.get_asset(first.asset_id or "")

    assert retry.reused is True
    assert retry.asset_version_id == first.asset_version_id
    assert original is not None and len(original.versions) == 1
    assert retry_job is not None and retry_job.warnings[0]["code"] == "idempotent_retry"
    assert duplicate.asset_id != first.asset_id
    assert duplicate_job is not None and duplicate_job.warnings[0]["code"] == "duplicate_content"
    assert duplicate_job.warnings[0]["matches"][0]["asset_id"] == first.asset_id


def test_unknown_asset_is_structured_and_does_not_leave_files(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(IngestionServiceError) as exc:
        _upload(service, b"id\n1\n", "missing")

    assert exc.value.status_code == 404
    assert exc.value.code == "asset_not_found"
    assert service.get_job(exc.value.job_id or "").status == IngestionStatus.FAILED  # type: ignore[union-attr]
    assert not list(service.staging_dir.iterdir())
    assert not list(service.raw_dir.iterdir())


def test_schema_diff_no_drift_added_removed_and_type_change(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _upload(service, b"id,name\n1,Ada\n")
    second = _upload(service, b"id,name\n2,Grace\n", first.asset_id)
    third = _upload(service, b"id,age\nx,36\n", first.asset_id)

    no_drift = service.get_version_diff(first.asset_id or "", second.asset_version_id or "")
    drift = service.get_version_diff(first.asset_id or "", third.asset_version_id or "")

    assert no_drift is not None and no_drift.has_drift is False
    assert drift is not None and drift.has_drift is True
    assert drift.fields_added == ["age"]
    assert drift.fields_removed == ["name"]
    assert [change.model_dump() for change in drift.type_changes] == [
        {"field": "id", "previous": "integer", "current": "string"}
    ]
    assert drift.previous_version_id == second.asset_version_id


def test_schema_diff_reports_nullability_change_when_observed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _upload(service, b"id,name\n1,Ada\n")
    second = _upload(service, b"id,name\n2,\n", first.asset_id)

    drift = service.get_version_diff(first.asset_id or "", second.asset_version_id or "")

    assert drift is not None
    assert [change.model_dump() for change in drift.nullability_changes] == [
        {"field": "name", "previous": False, "current": True}
    ]


def test_preview_is_read_from_raw_and_new_preview_is_not_persisted(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = _upload(service, b"id,name\n1,Ada\n2,Grace\n3,Linus\n")
    job = service.get_job(result.job_id)
    assert job is not None and job.inspection is not None
    binding = service.storage.get_binding(result.asset_version_id or "")
    assert binding is not None

    raw_path = service.raw_dir / Path(binding.physical_location).name
    raw_path.write_text("id,name\n9,OnDemand\n", encoding="utf-8")
    preview = service.get_preview(result.job_id)

    assert job.inspection.preview == []
    assert preview is not None and preview["rows"] == [{"id": "9", "name": "OnDemand"}]
    with sqlite3.connect(service.settings.catalog_db_path) as connection:
        persisted = connection.execute(
            "SELECT inspection_json FROM ingestion_jobs WHERE id = ?", (result.job_id,)
        ).fetchone()[0]
    assert '"preview": []' in persisted


def test_simulated_concurrent_allocation_is_unique_and_progressive(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _upload(service, b"id\n1\n")
    storage_a = IngestionStorage(service.settings.catalog_db_path)
    storage_b = IngestionStorage(service.settings.catalog_db_path)

    def prepare(storage: IngestionStorage, suffix: str, value: str) -> int:
        job = storage.create_job(f"{suffix}.csv")
        storage.transition_job(job.id, IngestionStatus.ACQUIRING, source_reference=f"staging/{suffix}.csv")
        storage.transition_job(job.id, IngestionStatus.INSPECTING)
        path = tmp_path / f"{suffix}.csv"
        path.write_text(f"id\n{value}\n", encoding="utf-8")
        inspection = service.readers[DataFormat.CSV].inspect(path, 1, 10)
        technical = inspection_to_technical_metadata(inspection)
        prepared = storage.prepare_version(
            job.id,
            "data",
            first.asset_id,
            f"raw/{suffix}.csv",
            DataFormat.CSV,
            suffix * 64,
            technical_schema_fingerprint(technical["fields"]),
            suffix * 63 + "x",
            inspection,
            technical,
        )
        return prepared.version.version_number

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(prepare, storage_a, "a", "2"),
            executor.submit(prepare, storage_b, "b", "x"),
        ]
        numbers = sorted(future.result() for future in futures)

    assert numbers == [2, 3]


def test_filesystem_and_database_failures_are_compensated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fs_service = _service(tmp_path / "fs")

    def fail_promotion(staged: Path, raw: Path) -> None:
        raise OSError("simulated promotion failure")

    monkeypatch.setattr(fs_service, "_promote", fail_promotion)
    with pytest.raises(IngestionServiceError) as fs_error:
        _upload(fs_service, b"id\n1\n")
    assert not list(fs_service.raw_dir.iterdir())
    assert fs_service.get_job(fs_error.value.job_id or "").status == IngestionStatus.FAILED  # type: ignore[union-attr]
    assert not any(version.status == "ready" for asset in fs_service.list_assets() for version in asset.versions)

    db_service = _service(tmp_path / "db")

    def fail_finalize(*args: Any, **kwargs: Any) -> None:
        raise sqlite3.OperationalError("simulated finalize failure")

    monkeypatch.setattr(db_service.storage, "finalize_version", fail_finalize)
    with pytest.raises(IngestionServiceError) as db_error:
        _upload(db_service, b"id\n1\n")
    assert not list(db_service.raw_dir.iterdir())
    assert db_service.get_job(db_error.value.job_id or "").status == IngestionStatus.FAILED  # type: ignore[union-attr]
    assert not db_service.storage.list_bindings()


def test_reconciliation_missing_binding_interrupted_job_and_orphan_staging(tmp_path: Path) -> None:
    service = _service(tmp_path)
    ready = _upload(service, b"id\n1\n")
    binding = service.storage.get_binding(ready.asset_version_id or "")
    assert binding is not None
    (service.raw_dir / Path(binding.physical_location).name).unlink()

    staged_name = "recover.csv"
    staged_path = service.staging_dir / staged_name
    staged_path.write_bytes(b"id\n2\n")
    job = service.storage.create_job(staged_name)
    service.storage.transition_job(
        job.id, IngestionStatus.ACQUIRING, source_reference=f"staging/{staged_name}"
    )
    service.storage.transition_job(job.id, IngestionStatus.INSPECTING, bytes_received=5)
    inspection = service.readers[DataFormat.CSV].inspect(staged_path, 1, 10)
    technical = inspection_to_technical_metadata(inspection)
    prepared = service.storage.prepare_version(
        job.id,
        "recover",
        None,
        "raw/recover.csv",
        DataFormat.CSV,
        __import__("hashlib").sha256(b"id\n2\n").hexdigest(),
        technical_schema_fingerprint(technical["fields"]),
        "r" * 64,
        inspection,
        technical,
    )
    orphan = service.staging_dir / "orphan.csv"
    orphan.write_text("id\n3\n", encoding="utf-8")
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with sqlite3.connect(service.settings.catalog_db_path) as connection:
        connection.execute("UPDATE ingestion_jobs SET updated_at = ? WHERE id = ?", (old, job.id))

    report = service.reconcile()

    assert binding.id in report.missing_bindings
    assert ready.job_id in report.failed_jobs
    assert job.id in report.recovered_jobs
    assert "staging/orphan.csv" in report.orphan_staging_files
    assert not orphan.exists()
    recovered = service.get_job(job.id)
    assert recovered is not None and recovered.status == IngestionStatus.READY
    assert service.storage.get_binding(prepared.version.id) is not None
