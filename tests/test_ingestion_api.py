from __future__ import annotations

import io
import asyncio
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import httpx
from fastapi import FastAPI

from queryx.app.api import routes
from queryx.app.core.config import Settings
from queryx.app.ingestion.service import IngestionService
from queryx.app.main import create_app


@pytest.fixture
def ingestion_client(tmp_path: Path) -> tuple[FastAPI, IngestionService, Path]:
    settings = Settings(
        catalog_db_path=tmp_path / "catalog.sqlite3",
        data_raw_dir=tmp_path / "data" / "raw",
        data_staging_dir=tmp_path / "data" / "staging",
        data_normalized_dir=tmp_path / "data" / "normalized",
        ingestion_max_upload_bytes=1024,
        ingestion_preview_rows=2,
        ingestion_inspection_rows=10,
        ingestion_csv_count_rows=100,
        mysql_enabled=False,
        mongodb_enabled=False,
    )
    service = IngestionService(settings)
    original = routes._ingestion_service
    routes._ingestion_service = lambda settings=None: service  # type: ignore[assignment]
    try:
        yield create_app(), service, tmp_path
    finally:
        routes._ingestion_service = original


def test_valid_csv_upload_job_preview_and_asset(ingestion_client: tuple[FastAPI, IngestionService, Path]) -> None:
    app, _, tmp_path = ingestion_client

    async def exercise() -> tuple[httpx.Response, httpx.Response, httpx.Response, httpx.Response]:
        async with _client(app) as client:
            response = await client.post(
                "/ingestions/uploads",
                files={"file": ("people.csv", b"id,name\n1,Ada\n2,Grace\n3,Linus\n", "text/csv")},
            )
            uploaded = response.json()
            return (
                response,
                await client.get(f"/ingestions/{uploaded['job_id']}"),
                await client.get(f"/ingestions/{uploaded['job_id']}/preview"),
                await client.get(f"/assets/{uploaded['asset_id']}"),
            )

    response, job, preview, asset = asyncio.run(exercise())

    assert response.status_code == 201
    uploaded = response.json()
    assert uploaded["status"] == "ready"

    assert job.status_code == preview.status_code == asset.status_code == 200
    assert job.json()["records_detected"] == 3
    assert len(preview.json()["rows"]) == 2
    assert preview.json()["preview_limit"] == 2
    assert asset.json()["versions"][0]["source_fingerprint"]
    assert not any((tmp_path / "data" / "staging").iterdir())
    assert len(list((tmp_path / "data" / "raw").iterdir())) == 1
    serialized = str(job.json()) + str(preview.json()) + str(asset.json())
    assert str(tmp_path) not in serialized


def test_valid_parquet_upload(ingestion_client: tuple[FastAPI, IngestionService, Path]) -> None:
    app, _, tmp_path = ingestion_client
    stream = io.BytesIO()
    pq.write_table(pa.table({"id": [1, 2], "active": [True, False]}), stream)

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        async with _client(app) as client:
            response = await client.post(
                "/ingestions/uploads",
                files={"file": ("events.parquet", stream.getvalue(), "application/vnd.apache.parquet")},
            )
            return response, await client.get(f"/ingestions/{response.json()['job_id']}/preview")

    response, preview_response = asyncio.run(exercise())

    assert response.status_code == 201
    preview = preview_response.json()
    assert preview["records_detected"] == 2
    assert [field["name"] for field in preview["schema"]] == ["id", "active"]
    assert str(tmp_path) not in str(preview)


def test_upload_rejections_are_structured_and_persist_failed_job(
    ingestion_client: tuple[FastAPI, IngestionService, Path],
) -> None:
    app, _, _ = ingestion_client

    async def exercise() -> tuple[list[httpx.Response], list[httpx.Response]]:
        async with _client(app) as client:
            responses = [
                await client.post("/ingestions/uploads", files={"file": ("data.json", b"{}", "application/json")}),
                await client.post("/ingestions/uploads", files={"file": ("../data.csv", b"id\n1\n", "text/csv")}),
                await client.post("/ingestions/uploads", files={"file": ("large.csv", b"a" * 1025, "text/csv")}),
            ]
            jobs = [await client.get(f"/ingestions/{item.json()['detail']['job_id']}") for item in responses]
            return responses, jobs

    responses, jobs = asyncio.run(exercise())
    unsupported, traversal, oversized = responses

    assert unsupported.status_code == 415
    assert unsupported.json()["detail"]["error"]["code"] == "unsupported_format"
    assert traversal.status_code == 400
    assert traversal.json()["detail"]["error"]["code"] == "unsafe_filename"
    assert oversized.status_code == 413
    assert oversized.json()["detail"]["error"]["code"] == "upload_too_large"
    for job in jobs:
        assert job.status_code == 200
        assert job.json()["status"] == "failed"
        assert "Traceback" not in str(job.json())


def test_assets_list_and_existing_sources_contract_do_not_regress(
    ingestion_client: tuple[FastAPI, IngestionService, Path],
) -> None:
    app, _, _ = ingestion_client

    async def exercise() -> httpx.Response:
        async with _client(app) as client:
            await client.post("/ingestions/uploads", files={"file": ("data.csv", b"id\n1\n", "text/csv")})
            return await client.get("/assets")

    assets = asyncio.run(exercise())

    assert len(assets.json()["assets"]) == 1
    assert "sources" in routes.list_sources()


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
