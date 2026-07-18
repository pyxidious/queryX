from __future__ import annotations

from pathlib import Path

import pytest

from queryx.app.ingestion.fingerprint import configuration_fingerprint, file_fingerprint
from queryx.app.ingestion.validation import IngestionValidationError, validate_filename, validate_size


def test_file_and_configuration_fingerprints_are_stable_and_sensitive(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    same = tmp_path / "same.csv"
    different = tmp_path / "different.csv"
    first.write_bytes(b"id\n1\n")
    same.write_bytes(b"id\n1\n")
    different.write_bytes(b"id\n2\n")

    assert file_fingerprint(first) == file_fingerprint(first)
    assert file_fingerprint(first) == file_fingerprint(same)
    assert file_fingerprint(first) != file_fingerprint(different)
    assert configuration_fingerprint({"b": 2, "a": 1}) == configuration_fingerprint({"a": 1, "b": 2})


@pytest.mark.parametrize("filename", ["../secret.csv", "folder/data.csv", r"..\secret.csv"])
def test_filename_path_traversal_is_rejected(filename: str) -> None:
    with pytest.raises(IngestionValidationError) as exc:
        validate_filename(filename)
    assert exc.value.code == "unsafe_filename"


def test_unsupported_format_and_size_limit_are_rejected() -> None:
    with pytest.raises(IngestionValidationError) as format_error:
        validate_filename("data.json")
    with pytest.raises(IngestionValidationError) as size_error:
        validate_size(11, 10)

    assert format_error.value.code == "unsupported_format"
    assert size_error.value.code == "upload_too_large"
