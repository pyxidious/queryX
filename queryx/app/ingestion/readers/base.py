from __future__ import annotations

from pathlib import Path
from typing import Protocol

from queryx.app.ingestion.models import InspectionResult


class DatasetReader(Protocol):
    def inspect(self, path: Path, preview_limit: int, sample_limit: int) -> InspectionResult:
        """Inspect a bounded sample and return deterministic technical metadata."""
