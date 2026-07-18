from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as parquet

from queryx.app.ingestion.models import DataFormat, InspectionResult, SchemaField
from queryx.app.ingestion.validation import IngestionValidationError


class ParquetReader:
    def inspect(self, path: Path, preview_limit: int, sample_limit: int) -> InspectionResult:
        del sample_limit
        try:
            parquet_file = parquet.ParquetFile(path)
            arrow_schema = parquet_file.schema_arrow
            preview: list[dict[str, Any]] = []
            if preview_limit > 0 and parquet_file.metadata.num_rows > 0:
                batch = next(parquet_file.iter_batches(batch_size=preview_limit), None)
                if batch is not None:
                    preview = [
                        {key: _json_value(value) for key, value in row.items()}
                        for row in batch.to_pylist()[:preview_limit]
                    ]
        except Exception as exc:
            raise IngestionValidationError("invalid_parquet", "Parquet metadata could not be read") from exc

        schema = [
            SchemaField(name=field.name, data_type=str(field.type), nullable=field.nullable)
            for field in arrow_schema
        ]
        metadata = parquet_file.metadata
        return InspectionResult(
            format=DataFormat.PARQUET,
            fields=schema,
            metadata={
                "columns": metadata.num_columns,
                "row_groups": metadata.num_row_groups,
                "created_by": metadata.created_by,
                "format_version": metadata.format_version,
            },
            preview=preview,
            records_detected=metadata.num_rows,
            records_estimated=False,
        )


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return f"<binary:{len(value)} bytes>"
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)
