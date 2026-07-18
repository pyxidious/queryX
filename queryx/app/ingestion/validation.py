from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

from queryx.app.ingestion.models import DataFormat


class IngestionValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_filename(filename: str | None) -> tuple[str, DataFormat]:
    if not filename or filename in {".", ".."}:
        raise IngestionValidationError("invalid_filename", "A valid filename is required")
    if PurePosixPath(filename).name != filename or PureWindowsPath(filename).name != filename:
        raise IngestionValidationError("unsafe_filename", "Filename must not contain a path")
    if any(character in filename for character in ("\x00", "\r", "\n")):
        raise IngestionValidationError("invalid_filename", "Filename contains invalid characters")
    suffix = Path(filename).suffix.lower()
    formats = {".csv": DataFormat.CSV, ".parquet": DataFormat.PARQUET}
    if suffix not in formats:
        raise IngestionValidationError("unsupported_format", "Only CSV and Parquet files are supported")
    return filename, formats[suffix]


def validate_size(bytes_received: int, maximum: int) -> None:
    if bytes_received > maximum:
        raise IngestionValidationError(
            "upload_too_large",
            f"Upload exceeds the configured limit of {maximum} bytes",
        )
