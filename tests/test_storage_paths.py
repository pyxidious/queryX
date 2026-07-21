from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi import UploadFile

from queryx.app.core.config import Settings
from queryx.app.core.storage_paths import StorageReferenceError, resolve_storage_reference
from queryx.app.ingestion.service import IngestionService
from queryx.app.processing.service import ProcessingService


def _settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    return Settings(
        catalog_db_path=data / "catalog.sqlite3",
        data_raw_dir=data / "raw",
        data_staging_dir=data / "staging",
        data_normalized_dir=data / "normalized",
        duckdb_path=data / "queryx.duckdb",
        duckdb_lock_path=data / "queryx.duckdb.lock",
        mysql_enabled=False,
        mongodb_enabled=False,
    )


def _upload(settings: Settings):
    stream = tempfile.SpooledTemporaryFile()
    stream.write(b"id,name\n1,Ada\n")
    stream.seek(0)
    return asyncio.run(
        IngestionService(settings).ingest_upload(UploadFile(stream, filename="people.csv"))
    )


def test_storage_reference_resolves_raw_normalized_and_legacy_without_duplicate_prefix(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "data" / "raw"
    normalized = tmp_path / "data" / "normalized"
    assert resolve_storage_reference("raw/file.csv", raw, "raw") == raw.resolve() / "file.csv"
    assert resolve_storage_reference("normalized/file.parquet", normalized, "normalized") == (
        normalized.resolve() / "file.parquet"
    )
    assert resolve_storage_reference("file.csv", raw, "raw") == raw.resolve() / "file.csv"
    assert "raw/raw" not in str(resolve_storage_reference("raw/file.csv", raw, "raw"))


@pytest.mark.parametrize(
    "reference",
    ["../file.csv", "./file.csv", "raw/../file.csv", "/app/data/raw/file.csv", "raw/sub/file.csv"],
)
def test_storage_reference_rejects_absolute_and_traversal(tmp_path: Path, reference: str) -> None:
    with pytest.raises(StorageReferenceError):
        resolve_storage_reference(reference, tmp_path / "raw", "raw")


def test_ingestion_reconciliation_ignores_valid_normalized_binding(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploaded = _upload(settings)
    ProcessingService(settings).prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")

    report = IngestionService(settings).reconcile()
    job = IngestionService(settings).get_job(uploaded.job_id)

    assert report.failed_jobs == []
    assert report.missing_bindings == []
    assert job is not None and job.status == "ready"


def test_reconciliation_restores_false_raw_missing_idempotently(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploaded = _upload(settings)
    error = {"code": "raw_file_missing", "message": "Storage binding points to a missing raw file"}
    with sqlite3.connect(settings.catalog_db_path) as connection:
        connection.execute(
            "UPDATE ingestion_jobs SET status = 'failed', error_json = ? WHERE id = ?",
            (json.dumps(error), uploaded.job_id),
        )
        connection.execute(
            "UPDATE asset_versions SET status = 'failed' WHERE id = ?",
            (uploaded.asset_version_id,),
        )

    service = IngestionService(settings)
    first = service.reconcile()
    second = service.reconcile()
    job = service.get_job(uploaded.job_id)
    version = service.get_version(uploaded.asset_id or "", uploaded.asset_version_id or "")

    assert first.recovered_jobs == [uploaded.job_id]
    assert second.recovered_jobs == []
    assert job is not None and job.status == "ready" and job.error is None
    assert version is not None and version.status == "ready"
