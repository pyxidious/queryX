from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from queryx.app.ingestion.fingerprint import configuration_fingerprint
from queryx.app.ingestion.models import DataFormat, InspectionResult


class CanonicalParquetRecipe(BaseModel):
    name: str = "canonical-parquet-v1"
    version: str = "1"
    source_format: DataFormat
    schema_conversion: list[dict[str, Any]]
    column_order: list[str]
    output_format: str = "parquet"
    compression: str = "zstd"
    batch_rows: int = 10_000
    parquet_version: str = "2.6"
    use_dictionary: bool = True
    write_statistics: bool = True
    conversion_policy: str = "strict"
    csv_options: dict[str, Any]
    normalizer_version: str = "pyarrow-canonical-v1"

    @property
    def fingerprint(self) -> str:
        return configuration_fingerprint(self.model_dump(mode="json"))


def canonical_parquet_recipe(
    inspection: InspectionResult,
    compression: str = "zstd",
    batch_rows: int = 10_000,
) -> CanonicalParquetRecipe:
    return CanonicalParquetRecipe(
        source_format=inspection.format,
        schema_conversion=[field.model_dump(mode="json") for field in inspection.fields],
        column_order=[field.name for field in inspection.fields],
        compression=compression,
        batch_rows=batch_rows,
        csv_options={
            "encoding": inspection.metadata.get("encoding", "utf-8"),
            "delimiter": inspection.metadata.get("delimiter", ","),
            "has_header": bool(inspection.metadata.get("has_header", True)),
        },
    )
