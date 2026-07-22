from __future__ import annotations

import logging

from queryx.app.catalog.service import CatalogService
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.core.config import Settings
from queryx.app.sources.registry import SourceRegistry


logger = logging.getLogger(__name__)


def backfill_mysql_assets(settings: Settings) -> None:
    """Promote the current historical MySQL scan without blocking startup."""
    try:
        registry = SourceRegistry(settings)
        catalog = CatalogService(CatalogStorage(settings.catalog_db_path))
        sources = registry.list_sources()
        catalog.upsert_sources(sources)
        catalog.backfill_mysql_assets(sources)
    except Exception:
        logger.exception("MySQL catalog asset backfill failed")
