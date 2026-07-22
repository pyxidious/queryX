from __future__ import annotations

from benchmark.run import compare_results, structural_equal, summarize_records


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
