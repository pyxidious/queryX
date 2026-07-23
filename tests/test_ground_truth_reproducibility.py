from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path

import pytest

import benchmark.generate_ground_truth as ground_truth


def test_numeric_normalization_float_integer_decimal_and_nested_values() -> None:
    payload = {
        "rows": [
            [1205005.6799999962, 45_140, True, None, "1205005.6799999962"],
            [13591643.69999868],
        ],
        "rows_prefix": (
            [Decimal("255.1044600")],
            [{"metric": Decimal("1.2345675")}],
        ),
    }

    normalized = ground_truth.normalize_numeric(payload, case_id="numeric")

    assert normalized["rows"] == [
        [1205005.68, 45_140, True, None, "1205005.6799999962"],
        [13591643.7],
    ]
    assert normalized["rows_prefix"] == (
        [255.10446],
        [{"metric": 1.234568}],
    )
    assert isinstance(normalized["rows"][0][1], int)
    assert ground_truth.normalize_numeric(
        Decimal("2.0000004"), case_id="numeric"
    ) == 2


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf, Decimal("NaN")])
def test_non_finite_numbers_are_rejected_with_case_and_path(invalid) -> None:
    with pytest.raises(ground_truth.GroundTruthError) as captured:
        ground_truth.normalize_numeric(
            {"rows": [[invalid]]},
            case_id="bad_case",
        )

    message = str(captured.value)
    assert "case_id=bad_case" in message
    assert "expected_result.rows[0][0]" in message
    assert "NaN and Infinity" in message


def test_normalization_is_idempotent() -> None:
    original = {
        "rows": [[1205005.6799999962], [Decimal("13591643.69999868")]],
        "metadata": {"count": 500, "enabled": False},
    }
    once = ground_truth.normalize_numeric(original, case_id="repeat")
    twice = ground_truth.normalize_numeric(once, case_id="repeat")
    assert once == twice


def test_update_cases_preserves_contract_and_reports_semantic_changes() -> None:
    cases = [
        {
            "id": "scalar",
            "expected_result": {
                "columns": ["total"],
                "rows": [[1205005.6799999964]],
                "numeric_tolerance": 1e-6,
            },
        },
        {
            "id": "ranked",
            "expected_result": {
                "row_count": 9,
                "rows_prefix": [["old", 1], ["old", 2]],
                "unordered": False,
            },
        },
        {"id": "legacy_without_result", "question": "kept untouched"},
        {
            "id": "integers_only",
            "expected_result": {"rows": [[45_140]]},
        },
    ]
    generated = {
        "scalar": {"rows": [[1205005.6799999962]]},
        "ranked": {
            "rows": [["first", 10], ["second", 9], ["third", 8]],
        },
        "integers_only": {"rows": [[45_140]]},
    }

    report = ground_truth.update_cases(cases, generated)

    assert report == ground_truth.UpdateReport(processed=3, changed=1, unchanged=2)
    assert cases[0]["expected_result"] == {
        "columns": ["total"],
        "rows": [[1205005.68]],
        "numeric_tolerance": 1e-6,
    }
    assert cases[1]["expected_result"] == {
        "row_count": 3,
        "rows_prefix": [["first", 10], ["second", 9]],
        "unordered": False,
    }
    assert cases[2] == {
        "id": "legacy_without_result",
        "question": "kept untouched",
    }
    assert cases[3]["expected_result"]["rows"] == [[45_140]]


def test_two_generations_are_byte_for_byte_identical(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    initial = [
        {
            "id": "sum",
            "expected_result": {
                "rows": [[13591643.699998675]],
                "numeric_tolerance": 1e-6,
            },
        }
    ]
    ground_truth.update_cases(
        initial, {"sum": {"rows": [[13591643.69999868]]}}
    )
    ground_truth._write_json(cases_path, initial)
    first = cases_path.read_bytes()

    second_cases = json.loads(cases_path.read_text(encoding="utf-8"))
    report = ground_truth.update_cases(
        second_cases, {"sum": {"rows": [[13591643.699998675]]}}
    )
    ground_truth._write_json(cases_path, second_cases)

    assert report.changed == 0
    assert cases_path.read_bytes() == first
    assert first.endswith(b"\n")
    assert not first.endswith(b"\n\n")


def test_atomic_write_preserves_original_when_replace_fails(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "cases.json"
    original = b'[\n  {"id": "original"}\n]\n'
    path.write_bytes(original)

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(ground_truth.os, "replace", fail_replace)
    with pytest.raises(ground_truth.GroundTruthError):
        ground_truth._write_json(path, [{"id": "replacement"}])

    assert path.read_bytes() == original
    assert list(tmp_path.glob(".cases.json.*.tmp")) == []


def test_main_is_cwd_independent_and_reports_processed_values(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps([{"id": "known", "expected_result": {"rows": [[1]]}}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(ground_truth, "Settings", lambda **kwargs: object())
    monkeypatch.setattr(
        ground_truth,
        "generate",
        lambda settings: {"known": {"rows": [[2]]}},
    )
    unrelated_cwd = tmp_path / "elsewhere"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    assert ground_truth.main(["--cases", str(cases_path)]) == 0
    updated = json.loads(cases_path.read_text(encoding="utf-8"))
    assert updated[0]["expected_result"]["rows"] == [[2]]
    output = capsys.readouterr().out
    assert "Ground-truth cases processed: 1" in output
    assert "Ground-truth values changed: 1" in output
    assert "Ground-truth values unchanged: 0" in output
    assert f"Output: {cases_path}" in output


def test_missing_duckdb_has_actionable_error(tmp_path: Path) -> None:
    class _Settings:
        duckdb_path = tmp_path / "missing.duckdb"

    with pytest.raises(ground_truth.GroundTruthError) as captured:
        ground_truth._duckdb(_Settings())

    assert str(tmp_path / "missing.duckdb") in str(captured.value)
    assert "Run the ingestion/discovery setup" in str(captured.value)
