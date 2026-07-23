from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


TRANSLATIONS: dict[str, dict[str, Any]] = {
    "it": {
        "title": "# QueryX – Report del benchmark",
        "metadata": "Metadati",
        "summary": "Sintesi",
        "overall": "Metriche complessive",
        "latencies": "Latenze",
        "backend_results": "Risultati per backend",
        "operation_results": "Risultati per operation type",
        "difficulty_results": "Risultati per difficoltà",
        "temporal_consistency": "Consistenza temporale",
        "semantic_robustness": "Robustezza semantica",
        "ground_truth": "Ground truth",
        "failed_cases": "Casi falliti",
        "limits": "Limiti",
        "reproducibility": "Riproducibilità",
        "files": "File prodotti",
        "appendix": "Appendice casi",
        "metric": "Metrica",
        "value": "Valore",
        "model_label": "Model label",
        "timestamp": "Timestamp UTC",
        "base_url": "Base URL",
        "cases_file": "File dei casi",
        "logical_cases": "Casi logici",
        "executions": "Esecuzioni",
        "executed_queries": "Query eseguite",
        "backend": "Backend",
        "cases": "Casi",
        "operation_type": "Operation type",
        "difficulty": "Difficoltà",
        "verified_executions": "Esecuzioni verificate",
        "median_ms": "Mediana (ms)",
        "p95_ms": "p95 (ms)",
        "case_id": "case_id",
        "repeat_count": "Ripetizioni",
        "classification_consistency": "Classificazione consistente",
        "backend_consistency": "Backend consistente",
        "plan_consistency": "Piano consistente",
        "result_consistency": "Risultato consistente",
        "full_consistency": "Consistenza completa",
        "equivalence_group": "Equivalence group",
        "operation_consistency": "Operazione consistente",
        "group_pass_rate": "Pass rate gruppo",
        "category": "Categoria",
        "observed_classification": "Classificazione osservata",
        "observed_backend": "Backend osservato",
        "error_code": "Codice errore",
        "plan_valid": "Piano valido",
        "executed": "Eseguito",
        "result_match": "Risultato corrispondente",
        "reason": "Motivo",
        "expected_backend": "Backend atteso",
        "ground_truth_present": "Ground truth presente",
        "result_verified_cases": "Casi con risultato verificato",
        "result_verified_executions": "Esecuzioni con risultato verificato",
        "outcome": "Esito",
        "metric_labels": {
            "pass_rate": "Pass rate",
            "classification_accuracy": "Accuratezza classificazione",
            "backend_selection_accuracy": "Accuratezza selezione backend",
            "valid_plan_rate": "Tasso piani validi",
            "execution_accuracy": "Accuratezza esecuzione",
            "result_verified_rate": "Tasso risultati verificati",
            "result_accuracy": "Accuratezza risultati",
            "repeat_consistency_rate": "Tasso consistenza ripetizioni",
            "semantic_consistency_rate": "Tasso consistenza semantica",
            "prudent_refusal_rate": "Tasso rifiuto prudente",
            "structural_hallucination_rate": "Tasso allucinazioni strutturali",
            "timeout_rate": "Tasso timeout",
            "error_rate": "Tasso errori",
        },
        "yes": "sì",
        "no": "no",
        "na": "n/d",
        "pass": "pass",
        "fail": "fail",
        "summary_text": (
            "Le metriche seguenti derivano esclusivamente dagli artefatti strutturati "
            "del benchmark, senza valutazioni soggettive."
        ),
        "result_scope": (
            "La result accuracy riguarda esclusivamente le esecuzioni dotate di "
            "`expected_result`."
        ),
        "latency_note": (
            "La latenza è un indicatore osservabile del costo computazionale, non una "
            "misura diretta di CPU, RAM, VRAM o consumo energetico."
        ),
        "repeat_note": (
            "Un caso può essere consistente pur fallendo in tutte le ripetizioni."
        ),
        "ground_truth_note": (
            "Il denominatore della result accuracy include soltanto le esecuzioni "
            "verificate."
        ),
        "repeated_cases": "Casi ripetuti",
        "equivalence_groups": "Equivalence group",
        "limits_items": [
            "La ground truth copre soltanto un sottoinsieme dei casi.",
            "La consistenza temporale è misurata solo sui casi ripetuti.",
            "La qualità linguistica delle spiegazioni non è valutata semanticamente.",
            "La latenza dipende dall’hardware e dal cold start del modello.",
            "La latenza non misura direttamente il consumo di risorse.",
            "La consistenza non implica correttezza.",
            "Il benchmark valuta i dataset demo correnti.",
        ],
        "reasons": {
            "classification": "classificazione errata",
            "backend": "backend errato",
            "asset": "asset errato",
            "plan": "piano non valido",
            "execution": "errore di esecuzione",
            "result": "risultato non corrispondente",
            "error_code": "codice errore inatteso",
            "unknown": "criterio pass/fail non soddisfatto",
        },
    },
    "en": {
        "title": "# QueryX – Benchmark Report",
        "metadata": "Metadata",
        "summary": "Summary",
        "overall": "Overall metrics",
        "latencies": "Latencies",
        "backend_results": "Results by backend",
        "operation_results": "Results by operation type",
        "difficulty_results": "Results by difficulty",
        "temporal_consistency": "Temporal consistency",
        "semantic_robustness": "Semantic robustness",
        "ground_truth": "Ground truth",
        "failed_cases": "Failed cases",
        "limits": "Limitations",
        "reproducibility": "Reproducibility",
        "files": "Produced files",
        "appendix": "Case appendix",
        "metric": "Metric",
        "value": "Value",
        "model_label": "Model label",
        "timestamp": "UTC timestamp",
        "base_url": "Base URL",
        "cases_file": "Cases file",
        "logical_cases": "Logical cases",
        "executions": "Executions",
        "executed_queries": "Executed queries",
        "backend": "Backend",
        "cases": "Cases",
        "operation_type": "Operation type",
        "difficulty": "Difficulty",
        "verified_executions": "Verified executions",
        "median_ms": "Median (ms)",
        "p95_ms": "p95 (ms)",
        "case_id": "case_id",
        "repeat_count": "Repeat count",
        "classification_consistency": "Classification consistency",
        "backend_consistency": "Backend consistency",
        "plan_consistency": "Plan consistency",
        "result_consistency": "Result consistency",
        "full_consistency": "Full consistency",
        "equivalence_group": "Equivalence group",
        "operation_consistency": "Operation consistency",
        "group_pass_rate": "Group pass rate",
        "category": "Category",
        "observed_classification": "Observed classification",
        "observed_backend": "Observed backend",
        "error_code": "Error code",
        "plan_valid": "Plan valid",
        "executed": "Executed",
        "result_match": "Result match",
        "reason": "Reason",
        "expected_backend": "Expected backend",
        "ground_truth_present": "Ground truth present",
        "result_verified_cases": "Result verified cases",
        "result_verified_executions": "Result verified executions",
        "outcome": "Outcome",
        "metric_labels": {
            "pass_rate": "Pass rate",
            "classification_accuracy": "Classification accuracy",
            "backend_selection_accuracy": "Backend selection accuracy",
            "valid_plan_rate": "Valid plan rate",
            "execution_accuracy": "Execution accuracy",
            "result_verified_rate": "Result verified rate",
            "result_accuracy": "Result accuracy",
            "repeat_consistency_rate": "Repeat consistency rate",
            "semantic_consistency_rate": "Semantic consistency rate",
            "prudent_refusal_rate": "Prudent refusal rate",
            "structural_hallucination_rate": "Structural hallucination rate",
            "timeout_rate": "Timeout rate",
            "error_rate": "Error rate",
        },
        "yes": "yes",
        "no": "no",
        "na": "n/a",
        "pass": "pass",
        "fail": "fail",
        "summary_text": (
            "The following metrics derive exclusively from structured benchmark "
            "artifacts, without subjective assessments."
        ),
        "result_scope": (
            "Result accuracy applies only to executions with an `expected_result`."
        ),
        "latency_note": (
            "Latency is an observable indicator of computational cost, not a direct "
            "measurement of CPU, RAM, VRAM, or energy consumption."
        ),
        "repeat_note": (
            "A case may be consistent while failing in every repetition."
        ),
        "ground_truth_note": (
            "The result accuracy denominator includes verified executions only."
        ),
        "repeated_cases": "Repeated cases",
        "equivalence_groups": "Equivalence groups",
        "limits_items": [
            "Ground truth covers only a subset of cases.",
            "Temporal consistency is measured only on repeated cases.",
            "The linguistic quality of explanations is not evaluated semantically.",
            "Latency depends on hardware and model cold start.",
            "Latency does not directly measure resource consumption.",
            "Consistency does not imply correctness.",
            "The benchmark evaluates the current demo datasets.",
        ],
        "reasons": {
            "classification": "incorrect classification",
            "backend": "incorrect backend",
            "asset": "incorrect asset",
            "plan": "invalid plan",
            "execution": "execution error",
            "result": "result mismatch",
            "error_code": "unexpected error code",
            "unknown": "pass/fail criterion not satisfied",
        },
    },
}

RATE_METRICS = [
    "pass_rate",
    "classification_accuracy",
    "backend_selection_accuracy",
    "valid_plan_rate",
    "execution_accuracy",
    "result_verified_rate",
    "result_accuracy",
    "repeat_consistency_rate",
    "semantic_consistency_rate",
    "prudent_refusal_rate",
    "structural_hallucination_rate",
    "timeout_rate",
    "error_rate",
]

class ReportError(RuntimeError):
    pass


def parse_languages(value: str) -> list[str]:
    if value not in {"it", "en", "it,en"}:
        raise ReportError("supported report languages are: it, en, it,en")
    return value.split(",")


def _escape(value: Any, translation: dict[str, Any]) -> str:
    if value is None:
        rendered = translation["na"]
    elif isinstance(value, bool):
        rendered = translation["yes"] if value else translation["no"]
    elif isinstance(value, list):
        rendered = ", ".join(str(item) for item in value)
    else:
        rendered = str(value)
    return rendered.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _percent(value: Any, translation: dict[str, Any]) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return translation["na"]
    return f"{float(value) * 100:.2f}%"


def _number(value: Any, translation: dict[str, Any], *, decimals: int = 2) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return translation["na"]
    if isinstance(value, int):
        return str(value)
    return f"{float(value):.{decimals}f}"


def _table(
    headers: list[str], rows: list[list[Any]], translation: dict[str, Any]
) -> str:
    lines = [
        "| " + " | ".join(_escape(item, translation) for item in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend(
        "| " + " | ".join(_escape(item, translation) for item in row) + " |"
        for row in rows
    )
    return "\n".join(lines)


def _failed_reason(record: dict[str, Any], translation: dict[str, Any]) -> str:
    reasons = translation["reasons"]
    if record.get("classification") != record.get("expected_classification"):
        return reasons["classification"]
    if (
        record.get("expected_backend")
        and record.get("backend") != record.get("expected_backend")
    ):
        return reasons["backend"]
    expected_asset = record.get("expected_asset_name")
    if expected_asset and expected_asset not in (record.get("asset_names") or []):
        return reasons["asset"]
    if record.get("expected_classification") == "answerable" and not record.get(
        "plan_valid"
    ):
        return reasons["plan"]
    if record.get("error_code") is not None:
        if record.get("error_code") != record.get("expected_error_code"):
            return reasons["error_code"]
        return reasons["execution"]
    if record.get("requested_execute") and not record.get("executed"):
        return reasons["execution"]
    if record.get("result_match") is False:
        return reasons["result"]
    return reasons["unknown"]


def _artifact_stem(summary_path: Path, details_path: Path) -> str:
    summary_suffix = ".summary.json"
    if summary_path.name.endswith(summary_suffix):
        summary_stem = summary_path.name[: -len(summary_suffix)]
        if details_path.name == f"{summary_stem}.json":
            return summary_stem
    return details_path.stem


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"cannot read {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise ReportError(f"{label} must contain a JSON object: {path}")
    return payload


def _validate_artifacts(
    summary: dict[str, Any], details: dict[str, Any]
) -> list[dict[str, Any]]:
    if summary.get("model_label") != details.get("model_label"):
        raise ReportError("summary/details model_label mismatch")
    records = details.get("records")
    if not isinstance(records, list):
        raise ReportError("details records are missing or invalid")
    total_cases = summary.get("total_cases")
    if total_cases is not None and total_cases != len(records):
        raise ReportError("summary/details total_cases mismatch")
    summary_url = summary.get("base_url")
    details_url = details.get("base_url")
    if summary_url is not None and details_url is not None and summary_url != details_url:
        raise ReportError("summary/details base_url mismatch")
    return [record for record in records if isinstance(record, dict)]


def render_report(
    *,
    language: str,
    summary: dict[str, Any],
    details: dict[str, Any],
    artifact_names: list[str],
) -> str:
    t = TRANSLATIONS[language]
    records = [item for item in details.get("records", []) if isinstance(item, dict)]
    lines: list[str] = [t["title"], ""]

    def section(title: str) -> None:
        lines.extend([f"## {title}", ""])

    section(t["metadata"])
    metadata = [
        [t["model_label"], summary.get("model_label")],
        [t["timestamp"], summary.get("generated_at")],
        [t["base_url"], summary.get("base_url") or details.get("base_url")],
        [t["cases_file"], summary.get("cases_file") or details.get("cases_file")],
        [t["logical_cases"], summary.get("total_cases", len(records))],
        [t["executions"], summary.get("total_executions")],
        [t["executed_queries"], summary.get("executed_queries")],
    ]
    lines.extend([_table([t["metric"], t["value"]], metadata, t), ""])

    section(t["summary"])
    lines.extend([t["summary_text"], "", t["result_scope"], ""])
    lines.extend(
        [
            f"- {t['metric_labels'][key]}: {_percent(summary.get(key), t)}"
            for key in RATE_METRICS
        ]
    )
    lines.append("")

    section(t["overall"])
    lines.extend(
        [
            _table(
                [t["metric"], t["value"]],
                [
                    [t["metric_labels"][key], _percent(summary.get(key), t)]
                    for key in RATE_METRICS
                ],
                t,
            ),
            "",
        ]
    )

    section(t["latencies"])
    latency = summary.get("latency_ms")
    latency = latency if isinstance(latency, dict) else {}
    latency_rows = []
    for name in ("planning", "execution", "explanation", "total"):
        values = latency.get(name)
        values = values if isinstance(values, dict) else {}
        latency_rows.append(
            [
                name,
                _number(values.get("median"), t),
                _number(values.get("p95"), t),
            ]
        )
    lines.extend(
        [
            _table([t["metric"], t["median_ms"], t["p95_ms"]], latency_rows, t),
            "",
            t["latency_note"],
            "",
        ]
    )

    section(t["backend_results"])
    backend_breakdown = summary.get("by_backend")
    backend_breakdown = (
        backend_breakdown if isinstance(backend_breakdown, dict) else {}
    )
    present_backends = sorted(
        {
            str(record.get("expected_backend") or record.get("backend"))
            for record in records
            if record.get("expected_backend") or record.get("backend")
        }
    )
    backend_rows = []
    for backend in present_backends:
        values = backend_breakdown.get(backend)
        values = values if isinstance(values, dict) else {}
        backend_rows.append(
            [
                backend,
                values.get(
                    "total_cases",
                    sum(
                        (record.get("expected_backend") or record.get("backend"))
                        == backend
                        for record in records
                    ),
                ),
                _percent(values.get("pass_rate"), t),
                _percent(values.get("valid_plan_rate"), t),
                _percent(values.get("execution_accuracy"), t),
                values.get("result_verified_executions"),
                _percent(values.get("result_accuracy"), t),
                _percent(values.get("semantic_consistency_rate"), t),
                _percent(values.get("error_rate"), t),
            ]
        )
    lines.extend(
        [
            _table(
                [
                    t["backend"],
                    t["cases"],
                    t["metric_labels"]["pass_rate"],
                    t["metric_labels"]["valid_plan_rate"],
                    t["metric_labels"]["execution_accuracy"],
                    t["verified_executions"],
                    t["metric_labels"]["result_accuracy"],
                    t["metric_labels"]["semantic_consistency_rate"],
                    t["metric_labels"]["error_rate"],
                ],
                backend_rows,
                t,
            ),
            "",
        ]
    )

    section(t["operation_results"])
    operation_breakdown = summary.get("by_operation_type")
    operation_breakdown = (
        operation_breakdown if isinstance(operation_breakdown, dict) else {}
    )
    operation_rows = []
    for operation, values in sorted(operation_breakdown.items()):
        values = values if isinstance(values, dict) else {}
        operation_rows.append(
            [
                operation,
                values.get("total_cases"),
                _percent(values.get("pass_rate"), t),
                values.get("result_verified_executions"),
                _percent(values.get("result_accuracy"), t),
                _percent(values.get("error_rate"), t),
            ]
        )
    lines.extend(
        [
            _table(
                [
                    t["operation_type"],
                    t["cases"],
                    t["metric_labels"]["pass_rate"],
                    t["verified_executions"],
                    t["metric_labels"]["result_accuracy"],
                    t["metric_labels"]["error_rate"],
                ],
                operation_rows,
                t,
            ),
            "",
        ]
    )

    section(t["difficulty_results"])
    difficulty_breakdown = summary.get("by_difficulty")
    difficulty_breakdown = (
        difficulty_breakdown if isinstance(difficulty_breakdown, dict) else {}
    )
    difficulty_rows = []
    for difficulty, values in sorted(difficulty_breakdown.items()):
        values = values if isinstance(values, dict) else {}
        difficulty_rows.append(
            [
                difficulty,
                values.get("total_cases"),
                _percent(values.get("pass_rate"), t),
                _percent(values.get("result_accuracy"), t),
                _percent(values.get("error_rate"), t),
            ]
        )
    lines.extend(
        [
            _table(
                [
                    t["difficulty"],
                    t["cases"],
                    t["metric_labels"]["pass_rate"],
                    t["metric_labels"]["result_accuracy"],
                    t["metric_labels"]["error_rate"],
                ],
                difficulty_rows,
                t,
            ),
            "",
        ]
    )

    section(t["temporal_consistency"])
    repeated = [
        record for record in records if int(record.get("repeat_count") or 1) > 1
    ]
    lines.extend(
        [
            f"- {t['repeated_cases']}: {len(repeated)}",
            f"- {t['metric_labels']['repeat_consistency_rate']}: "
            f"{_percent(summary.get('repeat_consistency_rate'), t)}",
            "",
        ]
    )
    repeat_rows = [
        [
            record.get("case_id"),
            record.get("repeat_count"),
            record.get("repeat_consistency_classification"),
            record.get("repeat_consistency_backend"),
            record.get("repeat_consistency_plan_validity"),
            record.get("repeat_consistency_result"),
            record.get("full_repeat_consistency"),
        ]
        for record in repeated
    ]
    lines.extend(
        [
            _table(
                [
                    t["case_id"],
                    t["repeat_count"],
                    t["classification_consistency"],
                    t["backend_consistency"],
                    t["plan_consistency"],
                    t["result_consistency"],
                    t["full_consistency"],
                ],
                repeat_rows,
                t,
            ),
            "",
            t["repeat_note"],
            "",
        ]
    )

    section(t["semantic_robustness"])
    groups = summary.get("by_equivalence_group")
    groups = groups if isinstance(groups, dict) else {}
    lines.extend(
        [
            f"- {t['equivalence_groups']}: {len(groups)}",
            f"- {t['metric_labels']['semantic_consistency_rate']}: "
            f"{_percent(summary.get('semantic_consistency_rate'), t)}",
            "",
        ]
    )
    group_rows = []
    for group, values in sorted(groups.items()):
        values = values if isinstance(values, dict) else {}
        group_rows.append(
            [
                group,
                values.get("case_count"),
                values.get("classification_consistency"),
                values.get("backend_consistency"),
                values.get("operation_consistency"),
                values.get("result_consistency"),
                _percent(values.get("group_pass_rate"), t),
                values.get("full_semantic_consistency"),
            ]
        )
    lines.extend(
        [
            _table(
                [
                    t["equivalence_group"],
                    t["cases"],
                    t["classification_consistency"],
                    t["backend_consistency"],
                    t["operation_consistency"],
                    t["result_consistency"],
                    t["group_pass_rate"],
                    t["full_consistency"],
                ],
                group_rows,
                t,
            ),
            "",
        ]
    )

    section(t["ground_truth"])
    lines.extend(
        [
            f"- {t['result_verified_cases']}: "
            f"{_number(summary.get('result_verified_cases'), t)}",
            f"- {t['result_verified_executions']}: "
            f"{_number(summary.get('result_verified_executions'), t)}",
            f"- {t['metric_labels']['result_verified_rate']}: "
            f"{_percent(summary.get('result_verified_rate'), t)}",
            f"- {t['metric_labels']['result_accuracy']}: "
            f"{_percent(summary.get('result_accuracy'), t)}",
            "",
            t["ground_truth_note"],
            "",
        ]
    )
    for title, field in (
        (t["backend_results"], "result_accuracy_by_backend"),
        (t["operation_results"], "result_accuracy_by_operation_type"),
    ):
        lines.extend([f"### {title}", ""])
        breakdown = summary.get(field)
        breakdown = breakdown if isinstance(breakdown, dict) else {}
        rows = [
            [
                key,
                values.get("verified_executions") if isinstance(values, dict) else None,
                _percent(
                    values.get("result_accuracy")
                    if isinstance(values, dict)
                    else None,
                    t,
                ),
            ]
            for key, values in sorted(breakdown.items())
        ]
        lines.extend(
            [
                _table(
                    [
                        t["metric"],
                        t["verified_executions"],
                        t["metric_labels"]["result_accuracy"],
                    ],
                    rows,
                    t,
                ),
                "",
            ]
        )

    section(t["failed_cases"])
    failures = [record for record in records if not record.get("pass")]
    failure_rows = [
        [
            record.get("case_id"),
            record.get("category"),
            record.get("operation_type"),
            record.get("difficulty"),
            record.get("classification"),
            record.get("backend"),
            record.get("error_code"),
            record.get("plan_valid"),
            record.get("executed"),
            record.get("result_match"),
            _failed_reason(record, t),
        ]
        for record in failures
    ]
    lines.extend(
        [
            _table(
                [
                    t["case_id"],
                    t["category"],
                    t["operation_type"],
                    t["difficulty"],
                    t["observed_classification"],
                    t["observed_backend"],
                    t["error_code"],
                    t["plan_valid"],
                    t["executed"],
                    t["result_match"],
                    t["reason"],
                ],
                failure_rows,
                t,
            ),
            "",
        ]
    )

    section(t["limits"])
    lines.extend([f"- {item}" for item in t["limits_items"]])
    lines.append("")

    section(t["reproducibility"])
    lines.extend(
        [
            "```bash",
            "docker compose up --build -d",
            "make seed",
            "make ground-truth",
            "MODEL_LABEL=<MODEL_LABEL> make benchmark",
            "```",
            "",
        ]
    )

    section(t["files"])
    lines.extend([f"- `{name}`" for name in artifact_names])
    lines.append("")

    section(t["appendix"])
    appendix_rows = [
        [
            record.get("case_id"),
            record.get("category"),
            record.get("expected_backend"),
            record.get("operation_type"),
            record.get("difficulty"),
            record.get("equivalence_group"),
            record.get("repeat_count", 1),
            record.get("expected_result") is not None,
            t["pass"] if record.get("pass") else t["fail"],
        ]
        for record in records
    ]
    lines.extend(
        [
            _table(
                [
                    "id",
                    t["category"],
                    t["expected_backend"],
                    t["operation_type"],
                    t["difficulty"],
                    t["equivalence_group"],
                    t["repeat_count"],
                    t["ground_truth_present"],
                    t["outcome"],
                ],
                appendix_rows,
                t,
            ),
            "",
        ]
    )
    return "\n".join(lines)


def generate_reports(
    *,
    summary_path: Path,
    details_path: Path,
    output_dir: Path,
    languages: list[str],
) -> tuple[Path, ...]:
    summary = _load_json(summary_path, "summary")
    details = _load_json(details_path, "details")
    _validate_artifacts(summary, details)
    stem = _artifact_stem(summary_path, details_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_paths = tuple(
        output_dir / f"{stem}.report.{language}.md" for language in languages
    )
    csv_path = details_path.with_suffix(".csv")
    artifact_names = [
        details_path.name,
        csv_path.name,
        summary_path.name,
        *(path.name for path in report_paths),
    ]
    for language, path in zip(languages, report_paths):
        path.write_text(
            render_report(
                language=language,
                summary=summary,
                details=details,
                artifact_names=artifact_names,
            ),
            encoding="utf-8",
        )
    return report_paths


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate deterministic Markdown reports from QueryX artifacts"
    )
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--details", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--languages", default="it,en")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        paths = generate_reports(
            summary_path=args.summary,
            details_path=args.details,
            output_dir=args.output_dir,
            languages=parse_languages(args.languages),
        )
    except ReportError as exc:
        print(f"benchmark report failed: {exc}", file=sys.stderr)
        return 2
    print("\n".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
