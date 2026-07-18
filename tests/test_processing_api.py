from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import httpx
from fastapi import FastAPI
from starlette.datastructures import UploadFile

from queryx.app.api import routes
from queryx.app.core.config import Settings
from queryx.app.ingestion.service import IngestionService
from queryx.app.main import create_app
from queryx.app.processing.service import ProcessingService


def test_processing_endpoints_complete_and_expose_only_bounded_preview(tmp_path: Path) -> None:
    settings = Settings(
        catalog_db_path=tmp_path / "data" / "catalog.sqlite3",
        data_raw_dir=tmp_path / "data" / "raw",
        data_staging_dir=tmp_path / "data" / "staging",
        data_normalized_dir=tmp_path / "data" / "normalized",
        duckdb_path=tmp_path / "data" / "queryx.duckdb",
        processing_preview_rows=2,
        parquet_batch_rows=2,
        mysql_enabled=False,
        mongodb_enabled=False,
    )
    ingestion = IngestionService(settings)
    stream = tempfile.SpooledTemporaryFile()
    stream.write(b"id,name\n1,Ada\n2,Grace\n3,Linus\n")
    stream.seek(0)
    uploaded = asyncio.run(ingestion.ingest_upload(UploadFile(stream, filename="people.csv")))
    processing = ProcessingService(settings)
    original_processing = routes._processing_service
    original_ingestion = routes._ingestion_service
    routes._processing_service = lambda settings=None: processing  # type: ignore[assignment]
    routes._ingestion_service = lambda settings=None: ingestion  # type: ignore[assignment]
    try:
        responses = asyncio.run(_exercise(create_app(), uploaded.asset_id or "", uploaded.asset_version_id or ""))
    finally:
        routes._processing_service = original_processing
        routes._ingestion_service = original_ingestion

    prepared, fetched, bindings, preview, too_large, arbitrary = responses
    assert prepared.status_code == 200
    assert prepared.json()["status"] == "completed"
    assert fetched.json()["id"] == prepared.json()["id"]
    assert [item["binding_role"] for item in bindings.json()["bindings"]] == ["raw", "normalized", "serving"]
    assert len(preview.json()["rows"]) == 2
    assert too_large.status_code == 400
    assert too_large.json()["detail"]["error"]["code"] == "preview_limit_exceeded"
    assert arbitrary.status_code == 400
    assert arbitrary.json()["detail"]["error"]["code"] == "unsupported_preview_parameter"
    assert "Traceback" not in str(responses)
    assert str(tmp_path) not in str(responses)


async def _exercise(
    app: FastAPI,
    asset_id: str,
    version_id: str,
) -> tuple[httpx.Response, ...]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        prepared = await client.post(f"/assets/{asset_id}/versions/{version_id}/prepare")
        return (
            prepared,
            await client.get(f"/processing/runs/{prepared.json()['id']}"),
            await client.get(f"/assets/{asset_id}/versions/{version_id}/bindings"),
            await client.get(f"/assets/{asset_id}/versions/{version_id}/data-preview?limit=2"),
            await client.get(f"/assets/{asset_id}/versions/{version_id}/data-preview?limit=3"),
            await client.get(
                f"/assets/{asset_id}/versions/{version_id}/data-preview?sql=DROP%20TABLE%20x"
            ),
        )
