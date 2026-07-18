from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from queryx.app.acquisition.models import DatasetManifest


class AcquisitionProvider(Protocol):
    def inspect_dataset(self, dataset_reference: str, requested_version: str) -> DatasetManifest: ...

    def download_file(
        self,
        dataset_reference: str,
        resolved_version: str,
        file_reference: str,
        target: Path,
        max_bytes: int,
        heartbeat: Callable[[], None] | None = None,
    ) -> int: ...

