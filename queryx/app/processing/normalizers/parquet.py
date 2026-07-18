from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as arrow_csv
import pyarrow.parquet as parquet

from queryx.app.ingestion.fingerprint import file_fingerprint, technical_schema_fingerprint
from queryx.app.ingestion.models import DataFormat, InspectionResult
from queryx.app.processing.models import NormalizationResult
from queryx.app.processing.recipe import CanonicalParquetRecipe


class NormalizationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CanonicalParquetNormalizer:
    def normalize(
        self,
        source: Path,
        destination: Path,
        inspection: InspectionResult,
        recipe: CanonicalParquetRecipe,
        batch_rows: int | None = None,
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
                for batch in batches:
                    canonical = _cast_batch(batch, target_schema)
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
        column_types = {field.name: field.type for field in target_schema}
        reader = arrow_csv.open_csv(
            source,
            read_options=arrow_csv.ReadOptions(use_threads=False, encoding="utf8"),
            parse_options=arrow_csv.ParseOptions(
                delimiter=str(inspection.metadata.get("delimiter", ",")),
            ),
            convert_options=arrow_csv.ConvertOptions(
                column_types=column_types,
                strings_can_be_null=True,
                null_values=[""],
            ),
        )
        for batch in reader:
            for offset in range(0, batch.num_rows, batch_rows):
                yield batch.slice(offset, batch_rows)

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


def _cast_batch(batch: pa.RecordBatch, schema: pa.Schema) -> pa.RecordBatch:
    if batch.schema.names != schema.names:
        raise NormalizationError("schema_mismatch", "Source columns differ from the canonical order")
    arrays = [
        pc.cast(batch.column(index), field.type, safe=True)
        for index, field in enumerate(schema)
    ]
    for array, field in zip(arrays, schema, strict=True):
        if not field.nullable and array.null_count:
            raise NormalizationError("strict_conversion_failed", f"Non-nullable field '{field.name}' contains null")
    return pa.RecordBatch.from_arrays(arrays, schema=schema)


def _target_schema(inspection: InspectionResult) -> pa.Schema:
    fields = [
        pa.field(field.name, _observed_type(field.data_type), nullable=field.nullable)
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
