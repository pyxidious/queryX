from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI
from starlette.datastructures import UploadFile

from queryx.app.api import routes
from queryx.app.core.config import Settings
from queryx.app.ingestion.service import IngestionService
from queryx.app.main import create_app
from queryx.app.processing.service import ProcessingService
from queryx.app.worker.models import TaskType, WorkStatus
from queryx.app.worker.service import WorkerService
from queryx.app.worker.storage import WorkerStorage


class _HealthySources:
    def health_checks(self) -> dict[str, dict[str, bool]]:
        return {"sources": {"ok": True}}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        catalog_db_path=tmp_path / "data" / "catalog.sqlite3",
        data_raw_dir=tmp_path / "data" / "raw",
        data_staging_dir=tmp_path / "data" / "staging",
        data_normalized_dir=tmp_path / "data" / "normalized",
        duckdb_path=tmp_path / "data" / "queryx.duckdb",
        duckdb_lock_path=tmp_path / "data" / "queryx.duckdb.lock",
        queryx_execution_mode="worker",
        worker_lease_seconds=10,
        worker_max_attempts=2,
        mysql_enabled=False,
        mongodb_enabled=False,
    )


def test_worker_mode_api_returns_202_pollable_states_and_rejects_duplicate_active_run(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    settings = _settings(tmp_path)
    ingestion = IngestionService(settings)
    processing = ProcessingService(settings)
    worker = WorkerService(settings, ingestion=ingestion, processing=processing, worker_id="worker")
    monkeypatch.setattr(routes, "get_settings", lambda: settings)  # type: ignore[attr-defined]
    monkeypatch.setattr(routes, "_ingestion_service", lambda settings=None: ingestion)  # type: ignore[attr-defined]
    monkeypatch.setattr(routes, "_processing_service", lambda settings=None: processing)  # type: ignore[attr-defined]
    monkeypatch.setattr(routes, "_worker_service", lambda settings=None: worker)  # type: ignore[attr-defined]

    async def exercise(app: FastAPI) -> tuple[httpx.Response, ...]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            accepted = await client.post(
                "/ingestions/uploads",
                files={"file": ("people.csv", b"id,name\n1,Ada\n2,Grace\n", "text/csv")},
            )
            before = await client.get(f"/ingestions/{accepted.json()['job_id']}")
            worker.run_once()
            ready = await client.get(f"/ingestions/{accepted.json()['job_id']}")
            prepare = await client.post(
                f"/assets/{ready.json()['asset_id']}/versions/{ready.json()['asset_version_id']}/prepare"
            )
            duplicate = await client.post(
                f"/assets/{ready.json()['asset_id']}/versions/{ready.json()['asset_version_id']}/prepare"
            )
            worker.run_once()
            completed = await client.get(f"/processing/runs/{prepare.json()['id']}")
            status_response = await client.get("/worker/status")
            return accepted, before, ready, prepare, duplicate, completed, status_response

    accepted, before, ready, prepare, duplicate, completed, status_response = asyncio.run(exercise(create_app()))

    assert accepted.status_code == 202
    assert accepted.json()["work_item_id"]
    assert before.json()["status"] == "acquiring"
    assert ready.json()["status"] == "ready"
    assert prepare.status_code == 202 and prepare.json()["status"] == "created"
    assert duplicate.status_code == 409
    assert completed.json()["status"] == "completed"
    assert status_response.json()["status"] == "online"
    assert status_response.json()["leased"] == 0


def test_worker_mode_cancellation_and_health_degraded_when_stale(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    settings = _settings(tmp_path)
    ingestion = IngestionService(settings)
    worker = WorkerService(settings, ingestion=ingestion, worker_id="worker")
    monkeypatch.setattr(routes, "get_settings", lambda: settings)  # type: ignore[attr-defined]
    monkeypatch.setattr(routes, "_ingestion_service", lambda settings=None: ingestion)  # type: ignore[attr-defined]
    monkeypatch.setattr(routes, "_worker_service", lambda settings=None: worker)  # type: ignore[attr-defined]
    monkeypatch.setattr(routes, "_build_orchestrator", lambda settings=None: _HealthySources())  # type: ignore[attr-defined]

    async def exercise(app: FastAPI) -> tuple[httpx.Response, ...]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            queued = await client.post(
                "/ingestions/uploads",
                files={"file": ("queued.csv", b"id\n1\n", "text/csv")},
            )
            cancelled = await client.post(f"/ingestions/{queued.json()['job_id']}/cancel")
            leased = await client.post(
                "/ingestions/uploads",
                files={"file": ("leased.csv", b"id\n2\n", "text/csv")},
            )
            item = WorkerStorage(settings.catalog_db_path).active_for(
                TaskType.INGESTION,
                leased.json()["job_id"],
            )
            assert item is not None
            WorkerStorage(settings.catalog_db_path).claim("worker", 10)
            requested = await client.post(f"/ingestions/{leased.json()['job_id']}/cancel")
            return cancelled, requested, await client.get("/worker/status"), await client.get("/health")

    cancelled, requested, _, _ = asyncio.run(exercise(create_app()))
    assert cancelled.json()["work_item"]["status"] == WorkStatus.CANCELLED
    assert cancelled.json()["job"]["status"] == "cancelled"
    assert requested.json()["work_item"]["status"] == "leased"
    assert requested.json()["work_item"]["cancellation_requested"] is True

    stale_time = datetime.now(timezone.utc) - timedelta(seconds=60)
    worker.storage.touch_worker("worker", now=stale_time)
    status_payload = worker.status()
    assert status_payload["status"] == "stale"
    assert routes.health()["status"] == "degraded"


def test_processing_run_queued_cancellation_is_coherent(tmp_path: Path, monkeypatch: object) -> None:
    settings = _settings(tmp_path)
    ingestion = IngestionService(settings)
    stream = tempfile.SpooledTemporaryFile()
    stream.write(b"id\n1\n")
    stream.seek(0)
    uploaded = asyncio.run(ingestion.ingest_upload(UploadFile(stream, filename="data.csv")))
    processing = ProcessingService(settings)
    run, _, _ = processing.create_processing_run(
        uploaded.asset_id or "",
        uploaded.asset_version_id or "",
    )
    item, _ = WorkerStorage(settings.catalog_db_path).enqueue(TaskType.PROCESSING, run.id, 2)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)  # type: ignore[attr-defined]
    monkeypatch.setattr(routes, "_processing_service", lambda settings=None: processing)  # type: ignore[attr-defined]

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()),
            base_url="http://test",
        ) as client:
            cancelled = await client.post(f"/processing/runs/{run.id}/cancel")
            conflict = await client.post(f"/processing/runs/{run.id}/cancel")
            return cancelled, conflict

    cancelled, conflict = asyncio.run(exercise())
    assert cancelled.status_code == 200
    assert cancelled.json()["run"]["status"] == "cancelled"
    assert cancelled.json()["work_item"]["id"] == item.id
    assert cancelled.json()["work_item"]["status"] == "cancelled"
    assert conflict.status_code == 409
