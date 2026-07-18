from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import UploadFile

from queryx.app.api import routes as api_routes
from queryx.app.core.config import Settings
from queryx.app.ingestion.service import IngestionService
from queryx.app.main import create_app
from queryx.app.processing.models import ProcessingStatus
from queryx.app.processing.service import ProcessingService
from queryx.app.processing.storage import ProcessingStorage
from queryx.app.worker.models import TaskType, WorkStatus
from queryx.app.worker.storage import WorkerStorage


def _settings(tmp_path: Path, mode: str = "inline", *, enabled: bool = True) -> Settings:
    data = tmp_path / "data"
    return Settings(
        catalog_db_path=data / "catalog.sqlite3",
        data_raw_dir=data / "raw",
        data_staging_dir=data / "staging",
        data_normalized_dir=data / "normalized",
        duckdb_path=data / "queryx.duckdb",
        duckdb_lock_path=data / "queryx.duckdb.lock",
        queryx_execution_mode=mode,
        queryx_ui_enabled=enabled,
        queryx_ui_secret_key="test-only-ui-secret-key",
        queryx_ui_max_preview_columns=3,
        ingestion_preview_rows=2,
        processing_preview_rows=2,
        parquet_batch_rows=2,
        mysql_enabled=False,
        mongodb_enabled=False,
    )


class UIClient:
    """Synchronous facade over HTTPX's in-process ASGI transport."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.cookies = httpx.Cookies()

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async def perform() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://testserver",
                cookies=self.cookies,
                follow_redirects=False,
            ) as client:
                response = await client.request(method, path, **kwargs)
                self.cookies.update(response.cookies)
                return response

        return asyncio.run(perform())

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)


def _client(monkeypatch: object, settings: Settings) -> UIClient:
    import queryx.app.main as main

    monkeypatch.setattr(main, "get_settings", lambda: settings)  # type: ignore[attr-defined]
    return UIClient(create_app())


def _csrf(client: UIClient, path: str = "/ui/ingestions/new") -> str:
    response = client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match
    return match.group(1)


def _upload(client: UIClient, *, name: str = "people.csv", logical_name: str = "") -> str:
    token = _csrf(client)
    response = client.post(
        "/ui/ingestions",
        data={"csrf_token": token, "logical_name": logical_name},
        files={"file": (name, b"id,name\n1,Ada\n2,Grace\n3,Linus\n", "text/csv")},
    )
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def _upload_service(settings: Settings, name: str = "people.csv"):
    stream = tempfile.SpooledTemporaryFile()
    stream.write(b"id,name\n1,Ada\n2,Grace\n3,Linus\n")
    stream.seek(0)
    return asyncio.run(IngestionService(settings).ingest_upload(UploadFile(stream, filename=name)))


def test_dashboard_static_assets_and_worker_offline(monkeypatch: object, tmp_path: Path) -> None:
    client = _client(monkeypatch, _settings(tmp_path, "worker"))
    response = client.get("/ui")
    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Worker offline" in response.text
    assert 'src="/ui/static/htmx.min.js"' in response.text
    assert client.get("/ui/static/queryx.css").status_code == 200
    assert "cdn" not in response.text.lower()


def test_ui_can_be_disabled(monkeypatch: object, tmp_path: Path) -> None:
    client = _client(monkeypatch, _settings(tmp_path, enabled=False))
    assert client.get("/ui").status_code == 404
    assert client.get("/ui/static/queryx.css").status_code == 404


def test_csrf_and_structured_html_errors(monkeypatch: object, tmp_path: Path) -> None:
    client = _client(monkeypatch, _settings(tmp_path))
    missing = client.post(
        "/ui/ingestions",
        files={"file": ("people.csv", b"id\n1\n", "text/csv")},
    )
    assert missing.status_code == 403
    assert "Token CSRF" in missing.text
    invalid = client.post(
        "/ui/ingestions",
        data={"csrf_token": "invalid"},
        files={"file": ("people.csv", b"id\n1\n", "text/csv")},
    )
    assert invalid.status_code == 403
    not_found = client.get("/ui/assets/does-not-exist")
    assert not_found.status_code == 404
    assert "Asset non trovato" in not_found.text


def test_upload_errors_are_html_and_sanitized(monkeypatch: object, tmp_path: Path) -> None:
    settings = _settings(tmp_path).model_copy(update={"ingestion_max_upload_bytes": 8})
    client = _client(monkeypatch, settings)
    token = _csrf(client)
    too_large = client.post(
        "/ui/ingestions",
        data={"csrf_token": token},
        files={"file": ("large.csv", b"id\n123456789\n", "text/csv")},
    )
    assert too_large.status_code == 413 and "Importazione non accettata" in too_large.text
    token = _csrf(client)
    unsupported = client.post(
        "/ui/ingestions",
        data={"csrf_token": token},
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert unsupported.status_code == 415
    assert str(tmp_path) not in unsupported.text
    invalid_form = client.post("/ui/ingestions", data={"csrf_token": token})
    assert invalid_form.status_code == 422 and "Dati non validi" in invalid_form.text


def test_inline_upload_job_preview_polling_and_xss_escape(monkeypatch: object, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings)
    job_id = _upload(client, logical_name="<script>alert(1)</script>")
    page = client.get(f"/ui/ingestions/{job_id}")
    fragment = client.get(f"/ui/ingestions/{job_id}/status")
    assert page.status_code == 200
    assert "Preview raw" in page.text and "Ada" in page.text
    assert "hx-trigger" not in fragment.text
    job = IngestionService(settings).get_job(job_id)
    assert job and job.asset_id
    asset_page = client.get(f"/ui/assets/{job.asset_id}")
    assert "<script>alert(1)</script>" not in asset_page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in asset_page.text
    assert str(tmp_path) not in page.text


def test_worker_upload_redirect_status_polling_cancel_and_asset_target(
    monkeypatch: object, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, "worker")
    client = _client(monkeypatch, settings)
    first = _upload_service(settings)
    token = _csrf(client)
    response = client.post(
        "/ui/ingestions",
        data={"csrf_token": token, "asset_id": first.asset_id},
        files={"file": ("v2.csv", b"id,name\n2,Grace\n", "text/csv")},
    )
    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    fragment = client.get(f"/ui/ingestions/{job_id}/status")
    assert 'hx-trigger="every 2s"' in fragment.text
    assert first.asset_id in client.get(f"/ui/ingestions/{job_id}").text
    token = _csrf(client, f"/ui/ingestions/{job_id}")
    cancelled = client.post(f"/ui/ingestions/{job_id}/cancel", data={"csrf_token": token})
    assert cancelled.status_code == 303
    assert IngestionService(settings).get_job(job_id).status == "cancelled"
    item = WorkerStorage(settings.catalog_db_path).latest_for(TaskType.INGESTION, job_id)
    assert item and item.status == WorkStatus.CANCELLED


def test_assets_version_prepare_processing_and_duckdb_preview(monkeypatch: object, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings)
    job_id = _upload(client)
    job = IngestionService(settings).get_job(job_id)
    assert job and job.asset_id and job.asset_version_id
    assets = client.get("/ui/assets")
    detail = client.get(f"/ui/assets/{job.asset_id}")
    version_url = f"/ui/assets/{job.asset_id}/versions/{job.asset_version_id}"
    before = client.get(version_url)
    assert all(response.status_code == 200 for response in (assets, detail, before))
    assert "Observed schema" in before.text and "Preview raw" in before.text
    token = _csrf(client, version_url)
    prepared = client.post(f"{version_url}/prepare", data={"csrf_token": token})
    assert prepared.status_code == 303
    run_id = prepared.headers["location"].rsplit("/", 1)[-1]
    run_page = client.get(f"/ui/processing/runs/{run_id}")
    run_fragment = client.get(f"/ui/processing/runs/{run_id}/status")
    version_page = client.get(version_url)
    assert "completed" in run_page.text
    assert "hx-trigger" not in run_fragment.text
    assert "Preview DuckDB" in version_page.text
    assert "normalized" in version_page.text and "serving" in version_page.text
    assert str(tmp_path) not in version_page.text


def test_worker_processing_202_polling_conflict_and_cancellation(monkeypatch: object, tmp_path: Path) -> None:
    settings = _settings(tmp_path, "worker")
    uploaded = _upload_service(settings)
    client = _client(monkeypatch, settings)
    version_url = f"/ui/assets/{uploaded.asset_id}/versions/{uploaded.asset_version_id}"
    token = _csrf(client, version_url)
    prepared = client.post(f"{version_url}/prepare", data={"csrf_token": token})
    assert prepared.status_code == 303
    run_id = prepared.headers["location"].rsplit("/", 1)[-1]
    assert 'hx-trigger="every 2s"' in client.get(f"/ui/processing/runs/{run_id}/status").text
    token = _csrf(client, version_url)
    conflict = client.post(f"{version_url}/prepare", data={"csrf_token": token})
    assert conflict.status_code == 409 and "Preparazione non disponibile" in conflict.text
    token = _csrf(client, f"/ui/processing/runs/{run_id}")
    cancelled = client.post(f"/ui/processing/runs/{run_id}/cancel", data={"csrf_token": token})
    assert cancelled.status_code == 303
    assert ProcessingService(settings).get_run(run_id).status == ProcessingStatus.CANCELLED


def test_partial_run_is_presented_as_recoverable(monkeypatch: object, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploaded = _upload_service(settings)
    processing = ProcessingService(settings)
    run, _, _ = processing.create_processing_run(uploaded.asset_id or "", uploaded.asset_version_id or "")
    storage = ProcessingStorage(settings.catalog_db_path)
    run = storage.transition_run(run.id, ProcessingStatus.NORMALIZING)
    run = storage.transition_run(run.id, ProcessingStatus.REGISTERING)
    storage.transition_run(run.id, ProcessingStatus.PARTIAL)
    client = _client(monkeypatch, settings)
    page = client.get(f"/ui/processing/runs/{run.id}")
    fragment = client.get(f"/ui/processing/runs/{run.id}/status")
    assert page.status_code == 200
    assert "recuperabile" in page.text.lower()
    assert 'hx-trigger="every 2s"' in fragment.text


def test_sources_pages_render_offline(monkeypatch: object, tmp_path: Path) -> None:
    client = _client(monkeypatch, _settings(tmp_path))
    assert client.get("/ui/sources").status_code == 200
    assert client.get("/ui/sources/missing").status_code == 404


def test_json_api_contract_is_unchanged(monkeypatch: object, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ingestion = IngestionService(settings)
    original_ingestion = api_routes._ingestion_service
    monkeypatch.setattr(api_routes, "get_settings", lambda: settings)  # type: ignore[attr-defined]
    monkeypatch.setattr(api_routes, "_ingestion_service", lambda settings=None: ingestion)  # type: ignore[attr-defined]
    try:
        client = _client(monkeypatch, settings)
        response = client.post(
            "/ingestions/uploads",
            files={"file": ("api.csv", b"id\n1\n", "text/csv")},
        )
        assert response.status_code == 201
        assert response.headers["content-type"].startswith("application/json")
        assert "job_id" in response.json()
    finally:
        api_routes._ingestion_service = original_ingestion
