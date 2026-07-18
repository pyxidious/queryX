from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow.parquet as parquet

from queryx.app.ingestion.fingerprint import file_fingerprint


class ProcessingValidationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_normalized_file(
    path: Path,
    expected_fingerprint: str,
    canonical_schema: list[dict[str, Any]],
) -> int:
    if not path.is_file():
        raise ProcessingValidationError("normalized_file_missing", "Normalized Parquet file is missing")
    if file_fingerprint(path) != expected_fingerprint:
        raise ProcessingValidationError("normalized_fingerprint_mismatch", "Normalized file fingerprint differs")
    try:
        parquet_file = parquet.ParquetFile(path)
        metadata = parquet_file.metadata
    except Exception as exc:
        raise ProcessingValidationError("invalid_normalized_parquet", "Normalized Parquet footer is invalid") from exc
    if metadata.num_columns != len(canonical_schema):
        raise ProcessingValidationError("canonical_column_count_mismatch", "Canonical column count differs")
    actual_schema = [
        {"name": field.name, "data_type": str(field.type), "nullable": field.nullable}
        for field in parquet_file.schema_arrow
    ]
    if actual_schema != canonical_schema:
        raise ProcessingValidationError("canonical_schema_mismatch", "Canonical Parquet schema differs")
    return metadata.num_rows


def schemas_compatible(
    canonical: list[dict[str, Any]],
    serving: list[dict[str, Any]],
) -> bool:
    if [field["name"] for field in canonical] != [field["name"] for field in serving]:
        return False
    return all(
        _type_family(left["data_type"]) == _type_family(right["data_type"])
        for left, right in zip(canonical, serving, strict=True)
    )


def _type_family(value: Any) -> str:
    normalized = str(value).lower()
    if normalized in {"int64", "bigint", "integer", "int32"}:
        return "integer"
    if normalized in {"double", "float", "float64", "real"}:
        return "number"
    if normalized in {"bool", "boolean"}:
        return "boolean"
    if normalized.startswith("timestamp"):
        return "datetime"
    if normalized.startswith("date"):
        return "date"
    if normalized in {"string", "varchar", "text"}:
        return "string"
    return normalized
