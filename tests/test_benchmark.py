from __future__ import annotations

import csv
import json

import pytest

from benchmark import run as benchmark_run
from benchmark.run import (
    _write_outputs,
    aggregate_repetitions,
    compare_results,
    normalize_case,
    structural_equal,
    summarize_records,
)


def test_result_comparison_ignores_row_order_and_uses_numeric_tolerance() -> None:
    actual = {
        "columns": ["status", "total"],
        "rows": [["pending", "89.5000"], ["paid", 149.9000001]],
        "row_count": 2,
        "execution_time_ms": 5.0,
    }
    expected = {
        "columns": ["status", "total"],
        "rows": [["paid", 149.9], ["pending", 89.5]],
        "row_count": 2,
    }

    assert compare_results(actual, expected, tolerance=1e-5) is True
    assert compare_results(actual, expected, ordered_rows=True, tolerance=1e-5) is False


def test_result_comparison_is_structural_and_optional() -> None:
    assert structural_equal(
        {"outer": {"value": 10.001, "ignored": True}},
        {"outer": {"value": 10.0}},
        0.01,
    )
    assert not structural_equal([1, 2], [1], 0.01)
    assert compare_results({"rows": []}, None) is None
    assert compare_results({"rows": [[1]]}, {"rows": [[2]]}) is False


def test_summary_metrics_and_category_breakdown() -> None:
    records = [
        {
            "category": "query",
            "pass": True,
            "classification": "answerable",
            "expected_classification": "answerable",
            "backend": "duckdb",
            "expected_backend": "duckdb",
            "plan_valid": True,
            "requested_execute": True,
            "executed": True,
            "result_match": True,
            "expected_error_code": None,
            "planning_ms": 10,
            "execution_ms": 2,
            "total_ms": 15,
        },
        {
            "category": "query",
            "pass": False,
            "classification": "ambiguous",
            "expected_classification": "answerable",
            "backend": None,
            "expected_backend": "mysql",
            "plan_valid": False,
            "requested_execute": True,
            "executed": False,
            "result_match": False,
            "expected_error_code": None,
            "planning_ms": 30,
            "execution_ms": None,
            "total_ms": 35,
        },
        {
            "category": "uncertainty",
            "pass": True,
            "classification": "ambiguous",
            "expected_classification": "ambiguous",
            "backend": None,
            "expected_backend": None,
            "plan_valid": False,
            "requested_execute": False,
            "executed": False,
            "result_match": None,
            "expected_error_code": None,
            "planning_ms": 20,
            "execution_ms": None,
            "total_ms": 22,
        },
        {
            "category": "uncertainty",
            "pass": True,
            "classification": "answerable",
            "expected_classification": "answerable",
            "backend": "mongodb",
            "expected_backend": "mongodb",
            "plan_valid": True,
            "requested_execute": False,
            "executed": False,
            "result_match": None,
            "expected_error_code": None,
            "planning_ms": 40,
            "execution_ms": None,
            "total_ms": 41,
        },
    ]

    summary = summarize_records(records)

    assert summary["total_cases"] == 4
    assert summary["pass_rate"] == 0.75
    assert summary["classification_accuracy"] == 0.75
    assert summary["backend_selection_accuracy"] == 2 / 3
    assert summary["valid_plan_rate"] == 2 / 3
    assert summary["execution_accuracy"] == 0.5
    assert summary["latency_ms"]["planning"] == {"median": 25.0, "p95": 40.0}
    assert summary["latency_ms"]["execution"] == {"median": 2.0, "p95": 2.0}
    assert summary["latency_ms"]["total"] == {"median": 28.5, "p95": 41.0}
    assert summary["by_category"]["query"]["total_cases"] == 2
    assert summary["by_category"]["uncertainty"]["pass_rate"] == 1.0


def test_new_case_fields_are_optional_and_validated() -> None:
    legacy = normalize_case({
        "id": "legacy", "category": "query", "question": "How many?",
        "execute": False, "expected_classification": "ambiguous",
    })
    assert legacy["operation_type"] == "uncertainty"
    assert legacy["difficulty"] == "medium"
    assert legacy["uncertainty_type"] == "none"
    assert legacy["repeat_count"] == 1

    current = normalize_case({
        **legacy,
        "operation_type": "count",
        "difficulty": "hard",
        "uncertainty_type": "incomplete_request",
        "repeat_count": 3,
        "equivalence_group": "count_group",
    })
    assert current["repeat_count"] == 3
    assert current["equivalence_group"] == "count_group"
    with pytest.raises(ValueError, match="repeat_count"):
        normalize_case({**legacy, "repeat_count": 0})


def test_repeat_aggregation_records_consistency_and_outcomes() -> None:
    case = {"expected_result": {"rows": [[1]]}}
    repetitions = [
        {
            "classification": "answerable", "backend": "mysql",
            "asset_names": ["orders"], "plan_valid": True, "pass": True,
            "result_match": True, "result": {"rows": [[1]]},
        },
        {
            "classification": "answerable", "backend": "mysql",
            "asset_names": ["orders"], "plan_valid": True, "pass": True,
            "result_match": True, "result": {"rows": [[1]]},
        },
    ]
    aggregate = aggregate_repetitions(case, repetitions)
    assert aggregate["repeat_count"] == 2
    assert aggregate["repeat_consistency_classification"] is True
    assert aggregate["repeat_consistency_backend"] is True
    assert aggregate["repeat_consistency_asset"] is True
    assert aggregate["repeat_consistency_plan_validity"] is True
    assert aggregate["repeat_consistency_result"] is True
    assert aggregate["full_repeat_consistency"] is True
    assert aggregate["repetitions"] == repetitions


def test_extended_summary_consistency_prudence_hallucination_and_breakdowns() -> None:
    base = {
        "category": "robustness", "operation_type": "count", "difficulty": "easy",
        "uncertainty_type": "none", "equivalence_group": "same_count",
        "classification": "answerable", "expected_classification": "answerable",
        "backend": "mysql", "expected_backend": "mysql", "asset_names": ["orders"],
        "plan_valid": True, "requested_execute": True, "executed": True,
        "result_match": None, "expected_result": None, "error_code": None,
        "pass": True, "planning_ms": 10, "execution_ms": 2,
        "explanation_ms": 3, "total_ms": 16, "observed_operation": "count",
        "hallucinated_asset": False, "hallucinated_field": False,
        "hallucinated_backend": False, "unsupported_operation": False,
        "forced_answer_on_missing_data": False, "repeat_count": 1,
    }
    repeated = aggregate_repetitions({}, [dict(base), dict(base)])
    second = dict(base, repeat_count=1)
    prudent = dict(
        base,
        category="missing_data", operation_type="unsupported_analysis",
        difficulty="hard", uncertainty_type="missing_data", equivalence_group=None,
        classification="unanswerable", expected_classification="unanswerable",
        backend=None, expected_backend=None, asset_names=[], plan_valid=False,
        requested_execute=False, executed=False, **{"pass": True},
    )
    forced = dict(
        prudent,
        classification="answerable", plan_valid=True, **{"pass": False},
        hallucinated_field=True, forced_answer_on_missing_data=True,
    )

    summary = summarize_records([repeated, second, prudent, forced])

    assert summary["total_cases"] == 4 and summary["total_executions"] == 5
    assert summary["repeat_consistency_rate"] == 1.0
    assert summary["semantic_consistency_rate"] == 1.0
    assert summary["forced_answer_rate"] == 0.5
    assert summary["prudent_refusal_rate"] == 0.5
    assert summary["structural_hallucination_rate"] == 0.2
    assert summary["result_verified_rate"] == 0.0
    assert summary["latency_ms"]["explanation"] == {"median": 3.0, "p95": 3.0}
    assert summary["by_operation_type"]["count"]["total_cases"] == 2
    assert summary["by_difficulty"]["hard"]["total_cases"] == 2
    assert summary["by_equivalence_group"]["same_count"]["full_semantic_consistency"] is True


def test_output_files_include_repetitions_csv_rows_and_extended_summary(tmp_path) -> None:
    case = {
        "case_id": "repeat", "category": "query", "operation_type": "count",
        "difficulty": "easy", "uncertainty_type": "none", "question": "Count",
        "classification": "answerable", "expected_classification": "answerable",
        "backend": "duckdb", "expected_backend": "duckdb", "asset_ids": ["a"],
        "asset_names": ["orders"], "plan_valid": True, "executed": False,
        "requested_execute": False, "result_match": None, "error_code": None,
        "warnings": [], "planning_ms": 1, "execution_ms": None,
        "explanation_ms": None, "total_ms": 2, "pass": True,
        "repeat_index": 1, "repeat_count": 2,
    }
    aggregate = aggregate_repetitions({}, [case, {**case, "repeat_index": 2}])
    paths = _write_outputs(
        tmp_path, [aggregate], summarize_records([aggregate]),
        model_label="demo/model", base_url="http://test",
    )

    details = json.loads(paths[0].read_text(encoding="utf-8"))
    summary = json.loads(paths[2].read_text(encoding="utf-8"))
    with paths[1].open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(details["records"][0]["repetitions"]) == 2
    assert [row["repeat_index"] for row in rows] == ["1", "2"]
    assert summary["total_cases"] == 1 and summary["total_executions"] == 2


def test_main_honors_repeat_count_without_real_http(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases_path = tmp_path / "cases.json"
    output_dir = tmp_path / "results"
    cases_path.write_text(json.dumps([{
        "id": "repeated", "category": "query", "operation_type": "count",
        "difficulty": "easy", "question": "Count", "execute": False,
        "expected_classification": "answerable", "repeat_count": 3,
    }]), encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(benchmark_run, "_asset_catalog", lambda *_: ({}, None))

    def fake_run(case, **_kwargs):
        calls.append(case["id"])
        return {
            "case_id": case["id"], "category": case["category"],
            "operation_type": case["operation_type"], "difficulty": case["difficulty"],
            "uncertainty_type": case["uncertainty_type"], "question": case["question"],
            "classification": "answerable", "expected_classification": "answerable",
            "backend": None, "expected_backend": None, "asset_ids": [], "asset_names": [],
            "plan_valid": True, "executed": False, "requested_execute": False,
            "result_match": None, "error_code": None, "warnings": [], "pass": True,
            "planning_ms": 1, "execution_ms": None, "explanation_ms": None, "total_ms": 2,
        }

    monkeypatch.setattr(benchmark_run, "run_case", fake_run)
    assert benchmark_run.main([
        "--cases", str(cases_path), "--output-dir", str(output_dir),
        "--model-label", "mock",
    ]) == 0
    assert calls == ["repeated", "repeated", "repeated"]
    details_path = next(output_dir.glob("*.json"))
    if details_path.name.endswith(".summary.json"):
        details_path = next(path for path in output_dir.glob("*.json") if not path.name.endswith(".summary.json"))
    details = json.loads(details_path.read_text(encoding="utf-8"))
    assert len(details["records"][0]["repetitions"]) == 3
