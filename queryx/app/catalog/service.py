from __future__ import annotations

from queryx.app.catalog.models import CatalogSnapshot, SourceMetadata
from queryx.app.catalog.storage import CatalogStorage


class CatalogService:
    def __init__(self, storage: CatalogStorage) -> None:
        self.storage = storage

    def save_scan(self, sources: list[SourceMetadata]) -> CatalogSnapshot:
        return self.storage.save_snapshot(sources)

    def latest(self) -> CatalogSnapshot | None:
        return self.storage.get_latest_snapshot()
