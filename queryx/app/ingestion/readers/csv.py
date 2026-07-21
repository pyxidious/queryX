from __future__ import annotations

import csv
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from queryx.app.ingestion.models import DataFormat, InspectionResult, SchemaField
from queryx.app.ingestion.validation import IngestionValidationError


class CSVReader:
    def __init__(self, count_limit: int = 10_000) -> None:
        self.count_limit = count_limit

    def inspect(self, path: Path, preview_limit: int, sample_limit: int) -> InspectionResult:
        delimiter = self._delimiter(path)
        sampled: list[dict[str, str | None]] = []
        rows_seen = 0
        exhausted = True
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, delimiter=delimiter)
                headers = reader.fieldnames
                self._validate_headers(headers)
                for row in reader:
                    rows_seen += 1
                    if rows_seen <= sample_limit:
                        sampled.append(
                            {key: _null_if_empty(value) for key, value in row.items() if key is not None}
                        )
                    if rows_seen >= self.count_limit:
                        exhausted = next(reader, None) is None
                        break
        except UnicodeDecodeError as exc:
            raise IngestionValidationError("invalid_csv_encoding", "CSV must use UTF-8 encoding") from exc
        except csv.Error as exc:
            raise IngestionValidationError("invalid_csv", "CSV content is malformed") from exc

        assert headers is not None
        schema = [
            SchemaField(
                name=header,
                data_type=_infer_column_type([row.get(header) for row in sampled]),
                # A bounded sample can infer a useful type, but cannot prove
                # that the full CSV column contains no nulls.
                nullable=True,
            )
            for header in headers
        ]
        return InspectionResult(
            format=DataFormat.CSV,
            fields=schema,
            metadata={
                "encoding": "utf-8",
                "delimiter": delimiter,
                "has_header": True,
                "columns": len(headers),
                "sampled_rows": len(sampled),
                "nullability_basis": "sampled_conservative",
                "count_limit": self.count_limit,
            },
            preview=sampled[:preview_limit],
            records_detected=rows_seen,
            records_estimated=not exhausted,
        )

    def preview(self, path: Path, limit: int) -> list[dict[str, Any]]:
        delimiter = self._delimiter(path)
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, delimiter=delimiter)
                self._validate_headers(reader.fieldnames)
                rows: list[dict[str, Any]] = []
                for row in reader:
                    rows.append(
                        {key: _null_if_empty(value) for key, value in row.items() if key is not None}
                    )
                    if len(rows) >= limit:
                        break
                return rows
        except (UnicodeDecodeError, csv.Error) as exc:
            raise IngestionValidationError("invalid_csv", "CSV content could not be previewed") from exc

    @staticmethod
    def _delimiter(path: Path) -> str:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                sample = stream.read(8192)
        except UnicodeDecodeError as exc:
            raise IngestionValidationError("invalid_csv_encoding", "CSV must use UTF-8 encoding") from exc
        if not sample.strip():
            raise IngestionValidationError("empty_file", "The uploaded file is empty")
        try:
            return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            return ","

    @staticmethod
    def _validate_headers(headers: list[str] | None) -> None:
        if not headers or any(not header.strip() for header in headers):
            raise IngestionValidationError("invalid_csv_header", "CSV requires non-empty column headers")
        if len(headers) != len(set(headers)):
            raise IngestionValidationError("invalid_csv_header", "CSV column headers must be unique")


_INTEGER = re.compile(r"^[+-]?\d+$")
_NUMBER = re.compile(r"^[+-]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?$")


def _infer_column_type(values: list[str | None]) -> str:
    types = {_value_type(value) for value in values if not _is_null(value)}
    if not types:
        return "null"
    if types <= {"integer"}:
        return "integer"
    if types <= {"integer", "number"}:
        return "number"
    if types <= {"boolean"}:
        return "boolean"
    if types <= {"date"}:
        return "date"
    if types <= {"datetime"}:
        return "datetime"
    return "string"


def _value_type(value: str | None) -> str:
    assert value is not None
    normalized = value.strip()
    if normalized.lower() in {"true", "false"}:
        return "boolean"
    if _INTEGER.fullmatch(normalized):
        return "integer"
    if _NUMBER.fullmatch(normalized):
        return "number"
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        return "datetime" if parsed.time() != datetime.min.time() or "T" in normalized else "date"
    except ValueError:
        try:
            date.fromisoformat(normalized)
            return "date"
        except ValueError:
            return "string"


def _is_null(value: Any) -> bool:
    return value is None or value == ""


def _null_if_empty(value: str | None) -> str | None:
    return None if _is_null(value) else value
