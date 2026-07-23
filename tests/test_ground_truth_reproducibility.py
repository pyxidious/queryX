from __future__ import annotations

import json
from pathlib import Path

import benchmark.generate_ground_truth as ground_truth


def test_update_cases_preserves_result_contract_and_updates_prefix() -> None:
    cases = [
        {
            "id": "scalar",
            "expected_result": {
                "columns": ["total"],
                "rows": [[1]],
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
    ]
    generated = {
        "scalar": {"rows": [[100_000]]},
        "ranked": {
            "rows": [["first", 10], ["second", 9], ["third", 8]],
        },
    }

    assert ground_truth.update_cases(cases, generated) == 2
    assert cases[0]["expected_result"] == {
        "columns": ["total"],
        "rows": [[100_000]],
        "numeric_tolerance": 1e-6,
    }
    assert cases[1]["expected_result"] == {
        "row_count": 3,
        "rows_prefix": [["first", 10], ["second", 9]],
        "unordered": False,
    }


def test_main_uses_package_relative_cases_and_persists_updates(
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
    assert "Updated 1 ground-truth cases" in capsys.readouterr().out


def test_missing_duckdb_has_actionable_error(tmp_path: Path) -> None:
    class _Settings:
        duckdb_path = tmp_path / "missing.duckdb"

    try:
        ground_truth._duckdb(_Settings())
    except ground_truth.GroundTruthError as exc:
        assert str(tmp_path / "missing.duckdb") in str(exc)
        assert "Run the ingestion/discovery setup" in str(exc)
    else:
        raise AssertionError("missing DuckDB must fail")
