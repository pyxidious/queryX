from __future__ import annotations

import json
from pathlib import Path

from benchmark import report


def _artifacts(tmp_path: Path) -> tuple[Path, Path]:
    stem = "benchmark-demo-model-20260723T120000Z"
    summary_path = tmp_path / f"{stem}.summary.json"
    details_path = tmp_path / f"{stem}.json"
    summary = {
        "model_label": "demo|model",
        "base_url": "http://127.0.0.1:8000",
        "cases_file": "/app/benchmark/cases.json",
        "generated_at": "2026-07-23T12:00:00+00:00",
        "total_cases": 2,
        "total_executions": 4,
        "executed_queries": 3,
        "pass_rate": 0.5,
        "classification_accuracy": 0.5,
        "backend_selection_accuracy": 1.0,
        "valid_plan_rate": 0.5,
        "execution_accuracy": 0.5,
        "result_verified_rate": 0.5,
        "result_accuracy": 1.0,
        "repeat_consistency_rate": 1.0,
        "semantic_consistency_rate": None,
        "prudent_refusal_rate": None,
        "structural_hallucination_rate": 0.0,
        "timeout_rate": 0.0,
        "error_rate": 0.25,
        "result_verified_cases": 1,
        "result_verified_executions": 2,
        "latency_ms": {
            "planning": {"median": 10.125, "p95": 20.5},
            "execution": {"median": 2.0, "p95": 3.0},
            "explanation": {"median": None, "p95": None},
            "total": {"median": 15.0, "p95": 25.0},
        },
        "by_backend": {
            "mysql": {
                "total_cases": 2,
                "pass_rate": 0.5,
                "valid_plan_rate": 0.5,
                "execution_accuracy": 0.5,
                "result_verified_executions": 2,
                "result_accuracy": 1.0,
                "semantic_consistency_rate": None,
                "error_rate": 0.25,
            }
        },
        "by_operation_type": {
            "count": {
                "total_cases": 2,
                "pass_rate": 0.5,
                "result_verified_executions": 2,
                "result_accuracy": 1.0,
                "error_rate": 0.25,
            }
        },
        "by_difficulty": {
            "easy": {
                "total_cases": 2,
                "pass_rate": 0.5,
                "result_accuracy": 1.0,
                "error_rate": 0.25,
            }
        },
        "by_equivalence_group": {},
        "result_accuracy_by_backend": {
            "mysql": {"verified_executions": 2, "result_accuracy": 1.0}
        },
        "result_accuracy_by_operation_type": {
            "count": {"verified_executions": 2, "result_accuracy": 1.0}
        },
    }
    details = {
        "model_label": "demo|model",
        "base_url": "http://127.0.0.1:8000",
        "cases_file": "/app/benchmark/cases.json",
        "records": [
            {
                "case_id": "passing",
                "category": "mysql",
                "operation_type": "count",
                "difficulty": "easy",
                "classification": "answerable",
                "expected_classification": "answerable",
                "backend": "mysql",
                "expected_backend": "mysql",
                "expected_asset_name": "orders",
                "asset_names": ["orders"],
                "plan_valid": True,
                "requested_execute": True,
                "executed": True,
                "result_match": True,
                "error_code": None,
                "expected_error_code": None,
                "expected_result": {"rows": [[1]]},
                "repeat_count": 3,
                "repeat_consistency_classification": True,
                "repeat_consistency_backend": True,
                "repeat_consistency_plan_validity": True,
                "repeat_consistency_result": True,
                "full_repeat_consistency": True,
                "pass": True,
            },
            {
                "case_id": "failed|case",
                "category": "mysql",
                "operation_type": "count",
                "difficulty": "easy",
                "classification": "ambiguous",
                "expected_classification": "answerable",
                "backend": "mysql",
                "expected_backend": "mysql",
                "asset_names": ["orders"],
                "plan_valid": False,
                "requested_execute": True,
                "executed": False,
                "result_match": None,
                "error_code": None,
                "expected_error_code": None,
                "expected_result": None,
                "repeat_count": 1,
                "pass": False,
            },
        ],
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    details_path.write_text(json.dumps(details), encoding="utf-8")
    return summary_path, details_path


def test_bilingual_reports_include_sections_rates_failures_and_ground_truth(
    tmp_path: Path,
) -> None:
    summary_path, details_path = _artifacts(tmp_path)

    paths = report.generate_reports(
        summary_path=summary_path,
        details_path=details_path,
        output_dir=tmp_path,
        languages=["it", "en"],
    )

    assert [path.name for path in paths] == [
        "benchmark-demo-model-20260723T120000Z.report.it.md",
        "benchmark-demo-model-20260723T120000Z.report.en.md",
    ]
    italian = paths[0].read_text(encoding="utf-8")
    english = paths[1].read_text(encoding="utf-8")
    assert "# QueryX – Report del benchmark" in italian
    assert "# QueryX – Benchmark Report" in english
    for heading in (
        "## Metriche complessive",
        "## Latenze",
        "## Risultati per backend",
        "## Consistenza temporale",
        "## Robustezza semantica",
        "## Ground truth",
        "## Casi falliti",
        "## Limiti",
        "## Riproducibilità",
        "## Appendice casi",
    ):
        assert heading in italian
    assert "50.00%" in italian
    assert "n/d" in italian and "n/a" in english
    assert "failed\\|case" in italian
    assert "classificazione errata" in italian
    assert "verified executions only" in english
    assert "benchmark-demo-model-20260723T120000Z.summary.json" in english
    assert "Latency is an observable indicator of computational cost" in english


def test_standalone_mode_generates_reports_without_external_calls(
    tmp_path: Path, capsys
) -> None:
    summary_path, details_path = _artifacts(tmp_path)
    output_dir = tmp_path / "reports"

    assert report.main(
        [
            "--summary",
            str(summary_path),
            "--details",
            str(details_path),
            "--output-dir",
            str(output_dir),
            "--languages",
            "it,en",
        ]
    ) == 0

    assert len(list(output_dir.glob("*.report.it.md"))) == 1
    assert len(list(output_dir.glob("*.report.en.md"))) == 1
    assert ".report.it.md" in capsys.readouterr().out


def test_incompatible_summary_and_details_return_readable_error(
    tmp_path: Path, capsys
) -> None:
    summary_path, details_path = _artifacts(tmp_path)
    details = json.loads(details_path.read_text(encoding="utf-8"))
    details["model_label"] = "different"
    details_path.write_text(json.dumps(details), encoding="utf-8")

    assert report.main(
        [
            "--summary",
            str(summary_path),
            "--details",
            str(details_path),
            "--output-dir",
            str(tmp_path),
        ]
    ) == 2
    assert "model_label mismatch" in capsys.readouterr().err
