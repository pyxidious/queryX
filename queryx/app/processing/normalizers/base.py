from __future__ import annotations

from pathlib import Path
from typing import Protocol

from queryx.app.ingestion.models import InspectionResult
from queryx.app.processing.models import NormalizationResult
from queryx.app.processing.recipe import CanonicalParquetRecipe


class DatasetNormalizer(Protocol):
    def normalize(
        self,
        source: Path,
        destination: Path,
        inspection: InspectionResult,
        recipe: CanonicalParquetRecipe,
        batch_rows: int,
    ) -> NormalizationResult: ...
