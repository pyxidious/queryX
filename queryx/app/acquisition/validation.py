from __future__ import annotations

import re
from pathlib import PurePosixPath


_DATASET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_VERSION = re.compile(r"^(latest|[A-Za-z0-9][A-Za-z0-9_.-]{0,127})$")


class AcquisitionValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_dataset_reference(value: str) -> str:
    canonical = value.strip().lower()
    if not _DATASET.fullmatch(canonical):
        raise AcquisitionValidationError("invalid_dataset_reference", "Dataset must use owner/dataset format")
    return canonical


def validate_version(value: str | None) -> str:
    resolved = (value or "latest").strip()
    if not _VERSION.fullmatch(resolved):
        raise AcquisitionValidationError("invalid_dataset_version", "Dataset version is invalid")
    return resolved


def validate_provider_file_reference(value: str) -> str:
    if not value or len(value) > 512 or "\\" in value or "\x00" in value:
        raise AcquisitionValidationError("unsafe_provider_file", "Provider file reference is unsafe")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise AcquisitionValidationError("unsafe_provider_file", "Provider file reference is unsafe")
    return str(path)


def file_format(name: str, allowed: set[str]) -> str | None:
    suffix = PurePosixPath(name).suffix.lower().lstrip(".")
    return suffix if suffix in allowed else None

