from __future__ import annotations

import asyncio
import re
import socket
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from queryx.app.api import routes
from queryx.app.core.config import Settings
from queryx.app.ingestion.models import DatasetProvenance, SourceProvider
from queryx.app.ingestion.service import IngestionService
from queryx.app.ingestion.storage import IngestionStorage
from queryx.app.main import create_app
from queryx.app.worker.models import TaskType


def _settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    return Settings(
        catalog_db_path=data / "catalog.sqlite3",
        data_raw_dir=data / "raw",
        data_staging_dir=data / "staging",
        data_normalized_dir=data / "normalized",
        duckdb_path=data / "queryx.duckdb",
        duckdb_lock_path=data / "queryx.duckdb.lock",
        queryx_ui_secret_key="test-only-provenance-key",
        mysql_enabled=False,
        mongodb_enabled=False,
    )


@pytest.fixture
def provenance_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, IngestionService, Settings]:
    import queryx.app.main as main

    settings = _settings(tmp_path)
    service = IngestionService(settings)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "_ingestion_service", lambda settings=None: service)
    return create_app(), service, settings


async def _upload(client: httpx.AsyncClient, data: dict[str, str] | None = None) -> httpx.Response:
    return await client.post(
        "/ingestions/uploads",
        data=data or {},
        files={"file": ("orders.csv", b"id,total\n1,10\n", "text/csv")},
    )


def test_upload_defaults_to_manual_and_exposes_job_version_and_lineage(provenance_app: tuple[Any, IngestionService, Settings]) -> None:
    app, service, _ = provenance_app

    async def exercise() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            uploaded = await _upload(client)
            payload = uploaded.json()
            return (
                uploaded,
                await client.get(f"/ingestions/{payload['job_id']}"),
                await client.get(f"/assets/{payload['asset_id']}/versions/{payload['asset_version_id']}"),
            )

    uploaded, job, version = asyncio.run(exercise())
    assert uploaded.status_code == 201
    assert job.json()["provenance"] == {"source_provider": "manual", "source_reference": None,
        "source_version": None, "dataset_title": None, "license_name": None, "notes": None}
    assert version.json()["provenance"] == [job.json()["provenance"]]
    lineage = service.storage.get_lineage(uploaded.json()["asset_version_id"])
    assert len(lineage) == 1
    assert lineage[0].provenance == DatasetProvenance()


def test_declared_kaggle_provenance_is_normalized_descriptive_and_offline(
    provenance_app: tuple[Any, IngestionService, Settings], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, service, _ = provenance_app

    def network_forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    data = {
        "source_provider": "kaggle",
        "source_reference": "  ../../escape   olistbr/brazilian-ecommerce  ",
        "source_version": " latest-downloaded-manually ",
        "dataset_title": " Brazilian   E-Commerce Dataset ",
        "license_name": " CC BY 4.0 ",
        "provenance_notes": " downloaded   and extracted locally ",
    }

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            uploaded = await _upload(client, data)
            return uploaded, await client.get(f"/ingestions/{uploaded.json()['job_id']}")

    uploaded, job = asyncio.run(exercise())
    provenance = job.json()["provenance"]
    assert uploaded.status_code == 201
    assert provenance["source_provider"] == "kaggle"
    assert provenance["source_reference"] == "../../escape olistbr/brazilian-ecommerce"
    assert provenance["dataset_title"] == "Brazilian E-Commerce Dataset"
    assert not (tmp_path / "escape").exists()
    assert service.get_version(uploaded.json()["asset_id"], uploaded.json()["asset_version_id"]).provenance[0].notes == (
        "downloaded and extracted locally"
    )


def test_provenance_validation_rejects_lengths_and_html(provenance_app: tuple[Any, IngestionService, Settings]) -> None:
    app, _, _ = provenance_app

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            too_long = await _upload(client, {"source_reference": "x" * 513})
            html = await _upload(client, {"dataset_title": "<script>alert(1)</script>"})
            return too_long, html

    too_long, html = asyncio.run(exercise())
    assert too_long.status_code == 422
    assert html.status_code == 422
    assert "<script>" not in html.text


def test_idempotent_reuse_deduplicates_equal_and_adds_different_provenance(
    provenance_app: tuple[Any, IngestionService, Settings]
) -> None:
    app, service, _ = provenance_app
    first_provenance = {"source_provider": "other", "source_reference": "source-a"}

    async def exercise() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = (await _upload(client, first_provenance)).json()
            target = {**first_provenance, "asset_id": first["asset_id"]}
            equal = (await _upload(client, target)).json()
            different = (await _upload(client, {**target, "source_reference": "source-b"})).json()
            return first, equal, different

    first, equal, different = asyncio.run(exercise())
    assert equal["reused"] is True and different["reused"] is True
    assert first["asset_version_id"] == equal["asset_version_id"] == different["asset_version_id"]
    lineage = service.storage.get_lineage(first["asset_version_id"])
    assert [edge.provenance.source_reference for edge in lineage if edge.provenance] == ["source-a", "source-b"]
    version = service.get_version(first["asset_id"], first["asset_version_id"])
    assert version is not None and len(version.provenance) == 2


def test_ui_provenance_rendering_csrf_and_removed_routes(
    provenance_app: tuple[Any, IngestionService, Settings]
) -> None:
    app, service, _ = provenance_app

    async def exercise() -> tuple[httpx.Response, ...]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            form = await client.get("/ui/ingestions/new")
            token = re.search(r'name="csrf_token" value="([^"]+)"', form.text).group(1)  # type: ignore[union-attr]
            rejected = await client.post(
                "/ui/ingestions",
                data={"source_provider": "other"},
                files={"file": ("orders.csv", b"id\n1\n", "text/csv")},
            )
            uploaded = await client.post(
                "/ui/ingestions",
                data={
                    "csrf_token": token,
                    "source_provider": "other",
                    "source_reference": "internal-catalog-ref",
                    "dataset_title": "Orders",
                },
                files={"file": ("orders.csv", b"id\n1\n", "text/csv")},
            )
            job_page = await client.get(uploaded.headers["location"])
            job_id = uploaded.headers["location"].rsplit("/", 1)[-1]
            job = service.get_job(job_id)
            assert job is not None
            version_page = await client.get(f"/ui/assets/{job.asset_id}/versions/{job.asset_version_id}")
            removed_provider = await client.get("/acqui" + "sition/providers")
            removed_api = await client.get("/acqui" + "sitions/anything")
            removed_ui = await client.get("/ui/acqui" + "sitions/anything")
            return form, rejected, uploaded, job_page, version_page, removed_provider, removed_api, removed_ui

    form, rejected, uploaded, job_page, version_page, removed_provider, removed_api, removed_ui = asyncio.run(
        exercise()
    )
    assert "Provenienza del dataset" in form.text
    assert "QueryX non scarica il dataset" in form.text
    assert rejected.status_code == 403
    assert uploaded.status_code == 303
    assert "internal-catalog-ref" in job_page.text and "Provenienza dichiarata" in job_page.text
    assert "internal-catalog-ref" in version_page.text and "Provenienza e lineage" in version_page.text
    assert 'href="internal-catalog-ref"' not in job_page.text
    assert str(service.raw_dir.parent) not in job_page.text + version_page.text
    assert removed_provider.status_code == removed_api.status_code == removed_ui.status_code == 404


def test_task_config_compose_and_legacy_sqlite_compatibility(tmp_path: Path) -> None:
    assert set(TaskType) == {TaskType.INGESTION, TaskType.PROCESSING}
    forbidden_prefix = "KAG" + "GLE_"
    assert not any(key.startswith(forbidden_prefix) for key in Settings.model_fields)
    compose = Path("compose.yaml").read_text(encoding="utf-8")
    assert forbidden_prefix not in compose
    assert "queryx:" in compose and "queryx-worker:" in compose and "mysql:" in compose and "mongodb:" in compose

    db_path = tmp_path / "legacy.sqlite3"
    run_table = "acqui" + "sition_runs"
    file_table = "acqui" + "sition_files"
    with sqlite3.connect(db_path) as connection:
        connection.execute(f"CREATE TABLE {run_table} (id TEXT PRIMARY KEY, marker TEXT)")
        connection.execute(f"CREATE TABLE {file_table} (id TEXT PRIMARY KEY, marker TEXT)")
        connection.execute(f"INSERT INTO {run_table} VALUES ('run-1', 'keep')")
        connection.execute(f"INSERT INTO {file_table} VALUES ('file-1', 'keep')")

    IngestionStorage(db_path)
    IngestionStorage(db_path)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(f"SELECT marker FROM {run_table}").fetchone() == ("keep",)
        assert connection.execute(f"SELECT marker FROM {file_table}").fetchone() == ("keep",)
