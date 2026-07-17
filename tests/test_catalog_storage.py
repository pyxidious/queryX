from __future__ import annotations

from pathlib import Path

from queryx.app.catalog.models import SourceMetadata
from queryx.app.catalog.storage import CatalogStorage


def test_catalog_snapshot_serializes_declared_and_inferred_metadata(tmp_path: Path) -> None:
    storage = CatalogStorage(tmp_path / "catalog.sqlite3")
    storage.save_snapshot(
        [
            SourceMetadata(
                source="mysql",
                database_type="mysql",
                declared={"tables": [{"name": "customers"}]},
                inferred={},
            ),
            SourceMetadata(
                source="mongodb",
                database_type="mongodb",
                declared={"collections": [{"name": "events"}]},
                inferred={"collections": [{"name": "events", "fields": []}]},
            ),
        ]
    )

    latest = storage.get_latest_snapshot()

    assert latest is not None
    assert latest.id == 1
    assert latest.sources[0].declared["tables"][0]["name"] == "customers"
    assert latest.sources[1].inferred["collections"][0]["name"] == "events"
