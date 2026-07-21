from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as arrow_csv
import pyarrow.parquet as parquet

from queryx.app.ingestion.fingerprint import file_fingerprint, technical_schema_fingerprint
from queryx.app.ingestion.models import DataFormat, InspectionResult
from queryx.app.processing.models import NormalizationResult
from queryx.app.processing.recipe import CanonicalParquetRecipe


class NormalizationError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class CanonicalParquetNormalizer:
    def normalize(
        self,
        source: Path,
        destination: Path,
        inspection: InspectionResult,
        recipe: CanonicalParquetRecipe,
        batch_rows: int | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> NormalizationResult:
        if destination.exists():
            raise NormalizationError("output_exists", "Temporary normalization output already exists")
        target_schema = (
            _parquet_target_schema(source, inspection)
            if inspection.format == DataFormat.PARQUET
            else _target_schema(inspection)
        )
        records = 0
        effective_batch_rows = batch_rows or recipe.batch_rows
        try:
            batches = (
                self._csv_batches(source, inspection, target_schema, effective_batch_rows)
                if inspection.format == DataFormat.CSV
                else self._parquet_batches(source, target_schema, effective_batch_rows)
            )
            compression = None if recipe.compression == "none" else recipe.compression
            with parquet.ParquetWriter(
                destination,
                target_schema,
                compression=compression,
                version=recipe.parquet_version,
                use_dictionary=recipe.use_dictionary,
                write_statistics=recipe.write_statistics,
            ) as writer:
                for batch_number, batch in enumerate(batches, start=1):
                    if checkpoint is not None:
                        checkpoint()
                    canonical = _cast_batch(
                        batch,
                        target_schema,
                        batch_number=batch_number,
                        row_offset=records,
                    )
                    writer.write_batch(canonical)
                    records += canonical.num_rows
            canonical_schema = arrow_schema_payload(target_schema)
            return NormalizationResult(
                records_read=records,
                records_written=records,
                bytes_written=destination.stat().st_size,
                canonical_schema=canonical_schema,
                content_fingerprint=file_fingerprint(destination),
                schema_fingerprint=technical_schema_fingerprint(canonical_schema),
            )
        except NormalizationError:
            raise
        except Exception as exc:
            raise NormalizationError(
                "strict_conversion_failed",
                "A source value is incompatible with the observed schema",
            ) from exc

    @staticmethod
    def _csv_batches(
        source: Path,
        inspection: InspectionResult,
        target_schema: pa.Schema,
        batch_rows: int,
    ) -> Iterable[pa.RecordBatch]:
        # Read lexical CSV values first, then cast each column with the strict
        # target type. This keeps conversion errors attributable to a column
        # without ever including the source value in persisted diagnostics.
        column_types = {field.name: pa.string() for field in target_schema}
        try:
            reader = arrow_csv.open_csv(
                source,
                read_options=arrow_csv.ReadOptions(use_threads=False, encoding="utf8"),
                parse_options=arrow_csv.ParseOptions(
                    delimiter=str(inspection.metadata.get("delimiter", ",")),
                ),
                convert_options=arrow_csv.ConvertOptions(
                    column_types=column_types,
                    null_values=[""],
                    strings_can_be_null=True,
                    quoted_strings_can_be_null=True,
                    timestamp_parsers=[arrow_csv.ISO8601, "%Y-%m-%d %H:%M:%S"],
                ),
            )
            for batch in reader:
                for offset in range(0, batch.num_rows, batch_rows):
                    yield batch.slice(offset, batch_rows)
        except NormalizationError:
            raise
        except Exception as exc:
            raise NormalizationError(
                "strict_conversion_failed",
                "A CSV row is incompatible with the observed schema",
                {
                    "column_name": None,
                    "expected_type": "CSV row matching header",
                    "reason": "type_conversion_failed",
                },
            ) from exc

    @staticmethod
    def _parquet_batches(
        source: Path,
        target_schema: pa.Schema,
        batch_rows: int,
    ) -> Iterable[pa.RecordBatch]:
        parquet_file = parquet.ParquetFile(source)
        source_names = parquet_file.schema_arrow.names
        if source_names != target_schema.names:
            raise NormalizationError("schema_mismatch", "Parquet columns differ from the observed schema")
        yield from parquet_file.iter_batches(batch_size=batch_rows, use_threads=False)


def _cast_batch(
    batch: pa.RecordBatch,
    schema: pa.Schema,
    *,
    batch_number: int | None = None,
    row_offset: int = 0,
) -> pa.RecordBatch:
    if batch.schema.names != schema.names:
        raise NormalizationError("schema_mismatch", "Source columns differ from the canonical order")
    arrays: list[pa.Array] = []
    for index, field in enumerate(schema):
        source = batch.column(index)
        try:
            array = _cast_array(source, field.type)
        except Exception as exc:
            failed_index = _first_conversion_failure(source, field.type)
            raise NormalizationError(
                "strict_conversion_failed",
                "A source value is incompatible with the observed schema",
                _conversion_details(
                    field.name,
                    field.type,
                    "type_conversion_failed",
                    batch_number,
                    row_offset,
                    failed_index,
                ),
            ) from exc
        if not field.nullable and array.null_count:
            failed_index = array.is_null().to_pylist().index(True)
            raise NormalizationError(
                "strict_conversion_failed",
                "A null violates the observed schema",
                _conversion_details(
                    field.name,
                    field.type,
                    "nullability_violation",
                    batch_number,
                    row_offset,
                    failed_index,
                ),
            )
        arrays.append(array)
    return pa.RecordBatch.from_arrays(arrays, schema=schema)


def _first_conversion_failure(array: pa.Array, target_type: pa.DataType) -> int | None:
    for index in range(len(array)):
        if array[index].is_valid:
            try:
                _cast_array(array.slice(index, 1), target_type)
            except Exception:
                return index
    return None


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$")


def _cast_array(array: pa.Array, target_type: pa.DataType) -> pa.Array:
    if pa.types.is_date32(target_type) and pa.types.is_string(array.type):
        converted: list[date | None] = []
        for scalar in array:
            if not scalar.is_valid:
                converted.append(None)
                continue
            value = scalar.as_py()
            if not isinstance(value, str):
                raise ValueError("CSV date source must be textual")
            if _DATE.fullmatch(value):
                converted.append(date.fromisoformat(value))
            elif _DATE_TIME.fullmatch(value):
                converted.append(datetime.fromisoformat(value).date())
            else:
                raise ValueError("CSV date format is invalid")
        return pa.array(converted, type=pa.date32())
    return pc.cast(array, target_type, safe=True)


def _conversion_details(
    column_name: str,
    expected_type: pa.DataType,
    reason: str,
    batch_number: int | None,
    row_offset: int,
    failed_index: int | None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "column_name": column_name,
        "expected_type": str(expected_type),
        "reason": reason,
    }
    if batch_number is not None:
        details["batch_number"] = batch_number
    if failed_index is not None:
        details["row_number"] = row_offset + failed_index + 1
    return details


def _target_schema(inspection: InspectionResult) -> pa.Schema:
    fields = [
        pa.field(
            field.name,
            _observed_type(field.data_type),
            # CSV nullability is observed from a bounded sample and therefore
            # cannot be enforced as non-null over the complete file.
            nullable=True if inspection.format == DataFormat.CSV else field.nullable,
        )
        for field in inspection.fields
    ]
    return pa.schema(fields, metadata=None)


def _parquet_target_schema(source: Path, inspection: InspectionResult) -> pa.Schema:
    native = parquet.ParquetFile(source).schema_arrow
    if native.names != [field.name for field in inspection.fields]:
        raise NormalizationError("schema_mismatch", "Parquet schema differs from the observed schema")
    return pa.schema(
        [pa.field(field.name, field.type, nullable=field.nullable) for field in native],
        metadata=None,
    )


def _observed_type(value: str) -> pa.DataType:
    simple = {
        "integer": pa.int64(),
        "number": pa.float64(),
        "boolean": pa.bool_(),
        "date": pa.date32(),
        "datetime": pa.timestamp("us"),
        "string": pa.string(),
        "null": pa.string(),
    }
    if value in simple:
        return simple[value]
    try:
        return pa.type_for_alias(value)
    except ValueError as exc:
        raise NormalizationError("unsupported_type", f"Observed type '{value}' is not supported") from exc


def arrow_schema_payload(schema: pa.Schema) -> list[dict[str, object]]:
    return [
        {"name": field.name, "data_type": str(field.type), "nullable": field.nullable}
        for field in schema
    ]
