from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from queryx.app.ingestion.models import DataFormat, IngestionStatus, InspectionResult, SchemaField
from queryx.app.ingestion.storage import IngestionStorage, InvalidJobTransition


def test_job_persistence_and_validated_transitions(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    storage = IngestionStorage(path)
    job = storage.create_job("people.csv")
    acquiring = storage.transition_job(job.id, IngestionStatus.ACQUIRING)
    inspecting = storage.transition_job(job.id, IngestionStatus.INSPECTING, bytes_received=12)

    reopened = IngestionStorage(path).get_job(job.id)

    assert acquiring.started_at is not None
    assert inspecting.status == IngestionStatus.INSPECTING
    assert reopened is not None
    assert reopened.bytes_received == 12
    with pytest.raises(InvalidJobTransition):
        storage.transition_job(job.id, IngestionStatus.COMPLETED)


def test_asset_version_binding_and_lineage_are_created_atomically(tmp_path: Path) -> None:
    storage = IngestionStorage(tmp_path / "catalog.sqlite3")
    job = storage.create_job("people.csv")
    storage.transition_job(job.id, IngestionStatus.ACQUIRING)
    storage.transition_job(job.id, IngestionStatus.INSPECTING)
    inspection = InspectionResult(
        format=DataFormat.CSV,
        fields=[SchemaField(name="id", data_type="integer", nullable=False)],
        records_detected=2,
    )

    asset, version = storage.create_asset_for_job(
        job.id, "people", "raw/internal.csv", DataFormat.CSV, "a" * 64, "b" * 64, "c" * 64, inspection
    )
    persisted = storage.get_asset(asset.id)
    completed_job = storage.get_job(job.id)

    assert version.version_number == 1
    assert persisted is not None
    assert persisted.versions[0].storage_bindings[0].physical_location == "raw/internal.csv"
    assert storage.get_lineage(version.id)[0].operation == "upload"
    assert completed_job is not None
    assert completed_job.status == IngestionStatus.READY
    assert completed_job.asset_version_id == version.id


def test_sqlite_initialization_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    IngestionStorage(path)
    IngestionStorage(path)

    with sqlite3.connect(path) as connection:
        versions = connection.execute("SELECT version, COUNT(*) FROM schema_version WHERE version = 4").fetchone()
        assets_table = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'data_assets'"
        ).fetchone()

    assert versions == (4, 1)
    assert assets_table == (1,)
