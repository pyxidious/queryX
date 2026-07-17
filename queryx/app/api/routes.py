from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from queryx.app.agent.orchestrator import ScanOrchestrator
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


def _build_orchestrator(settings: Settings | None = None) -> ScanOrchestrator:
    resolved = settings or get_settings()
    return ScanOrchestrator.from_settings(resolved)


@router.get("/health")
def health() -> dict[str, Any]:
    orchestrator = _build_orchestrator()
    checks = orchestrator.health_checks()
    return {
        "status": "ok" if all(check["ok"] for check in checks.values()) else "degraded",
        "checks": checks,
    }


@router.post("/catalog/scan")
def scan_catalog() -> dict[str, Any]:
    try:
        return _build_orchestrator().scan()
    except Exception as exc:
        logger.exception("Catalog scan failed")
        raise HTTPException(status_code=500, detail="Catalog scan failed") from exc


@router.get("/catalog/latest")
def latest_catalog() -> dict[str, Any]:
    storage = CatalogStorage(get_settings().catalog_db_path)
    snapshot = storage.get_latest_snapshot()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No catalog snapshot available")
    return snapshot.model_dump(mode="json")
