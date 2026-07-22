#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from numbers import Number
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT_SECONDS = 330.0
DEFAULT_TOLERANCE = 1e-6
CSV_FIELDS = [
    "case_id", "question", "classification", "backend", "asset_ids",
    "asset_names", "plan_valid", "executed", "result_match", "error_code",
    "warnings", "planning_ms", "execution_ms", "explanation_ms", "total_ms",
    "pass",
]


def _numeric_pair(actual: Any, expected: Any) -> tuple[float, float] | None:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return None
    if isinstance(actual, Number) and isinstance(expected, Number):
        return float(actual), float(expected)
    if isinstance(actual, Number) and isinstance(expected, str):
        try:
            return float(actual), float(expected)
        except ValueError:
            return None
    if isinstance(expected, Number) and isinstance(actual, str):
        try:
            return float(actual), float(expected)
        except ValueError:
            return None
    return None


def structural_equal(actual: Any, expected: Any, tolerance: float) -> bool:
    numeric = _numeric_pair(actual, expected)
    if numeric is not None:
        return math.isclose(*numeric, rel_tol=tolerance, abs_tol=tolerance)
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and structural_equal(actual[key], value, tolerance)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            structural_equal(left, right, tolerance)
            for left, right in zip(actual, expected)
        )
    return actual == expected


def compare_results(
    actual: dict[str, Any] | None,
    expected: dict[str, Any] | None,
    *,
    ordered_rows: bool = False,
    tolerance: float = DEFAULT_TOLERANCE,
) -> bool | None:
    if expected is None:
        return None
    if not isinstance(actual, dict):
        return False
    for key, expected_value in expected.items():
        if key == "rows":
            continue
        if key not in actual or not structural_equal(actual[key], expected_value, tolerance):
            return False
    if "rows" not in expected:
        return True
    actual_rows = actual.get("rows")
    expected_rows = expected["rows"]
    if not isinstance(actual_rows, list) or not isinstance(expected_rows, list):
        return False
    if ordered_rows:
        return structural_equal(actual_rows, expected_rows, tolerance)
    if len(actual_rows) != len(expected_rows):
        return False
    unmatched = list(actual_rows)
    for expected_row in expected_rows:
        match_index = next(
            (
                index for index, actual_row in enumerate(unmatched)
                if structural_equal(actual_row, expected_row, tolerance)
            ),
            None,
        )
        if match_index is None:
            return False
        unmatched.pop(match_index)
    return True


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _latency_summary(records: list[dict[str, Any]], field: str) -> dict[str, float | None]:
    values = [float(record[field]) for record in records if record.get(field) is not None]
    return {
        "median": statistics.median(values) if values else None,
        "p95": _percentile(values, 0.95),
    }


def summarize_records(
    records: list[dict[str, Any]], *, include_categories: bool = True
) -> dict[str, Any]:
    total = len(records)
    classification_records = [r for r in records if r.get("expected_classification")]
    backend_records = [r for r in records if r.get("expected_backend")]
    plan_records = [
        r for r in records
        if r.get("expected_classification") == "answerable"
        and not r.get("expected_error_code")
    ]
    execution_records = [r for r in plan_records if r.get("requested_execute")]
    summary: dict[str, Any] = {
        "total_cases": total,
        "passed_cases": sum(bool(r.get("pass")) for r in records),
        "pass_rate": _rate(sum(bool(r.get("pass")) for r in records), total),
        "classification_accuracy": _rate(
            sum(r.get("classification") == r.get("expected_classification") for r in classification_records),
            len(classification_records),
        ),
        "backend_selection_accuracy": _rate(
            sum(r.get("backend") == r.get("expected_backend") for r in backend_records),
            len(backend_records),
        ),
        "valid_plan_rate": _rate(
            sum(bool(r.get("plan_valid")) for r in plan_records), len(plan_records)
        ),
        "execution_accuracy": _rate(
            sum(
                bool(r.get("executed")) and r.get("result_match") is not False
                for r in execution_records
            ),
            len(execution_records),
        ),
        "latency_ms": {
            "planning": _latency_summary(records, "planning_ms"),
            "execution": _latency_summary(records, "execution_ms"),
            "total": _latency_summary(records, "total_ms"),
        },
    }
    if include_categories:
        categories = sorted({str(r.get("category")) for r in records})
        summary["by_category"] = {
            category: summarize_records(
                [r for r in records if str(r.get("category")) == category],
                include_categories=False,
            )
            for category in categories
        }
    return summary


def _request_json(
    method: str, url: str, *, payload: dict[str, Any] | None, timeout: float
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": {"code": "http_error", "message": raw}}


def _error_code(payload: dict[str, Any]) -> str | None:
    candidates = [payload.get("error"), payload.get("detail")]
    detail = payload.get("detail")
    if isinstance(detail, dict):
        candidates.append(detail.get("error"))
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("code"), str):
            return candidate["code"]
    return None


def _asset_catalog(base_url: str, timeout: float) -> tuple[dict[str, dict[str, str]], str | None]:
    try:
        status, payload = _request_json("GET", f"{base_url}/assets", payload=None, timeout=timeout)
        if status >= 400:
            return {}, _error_code(payload) or f"http_{status}"
        assets: dict[str, dict[str, str]] = {}
        for asset in payload.get("assets", []):
            if not isinstance(asset, dict) or not isinstance(asset.get("id"), str):
                continue
            kind = str(asset.get("asset_kind", ""))
            backend = {
                "mysql_table": "mysql",
                "mongodb_collection": "mongodb",
            }.get(kind, "duckdb")
            assets[asset["id"]] = {
                "name": str(asset.get("name", asset["id"])),
                "backend": backend,
            }
        return assets, None
    except Exception as exc:  # catalog lookup is best effort; cases still run
        return {}, f"catalog_lookup_failed:{type(exc).__name__}"


def _plan_identity(
    plan: Any, assets: dict[str, dict[str, str]]
) -> tuple[list[str], list[str], str | None]:
    if not isinstance(plan, dict):
        return [], [], None
    asset_ids = [
        source.get("asset_id")
        for source in plan.get("sources", [])
        if isinstance(source, dict) and isinstance(source.get("asset_id"), str)
    ]
    asset_names = [assets.get(asset_id, {}).get("name", asset_id) for asset_id in asset_ids]
    backends = {
        assets.get(asset_id, {}).get("backend")
        or ("mysql" if asset_id.startswith("mysql_") else "mongodb" if asset_id.startswith("mongodb_") else None)
        for asset_id in asset_ids
    }
    backends.discard(None)
    backend = next(iter(backends)) if len(backends) == 1 else "mixed" if backends else None
    return asset_ids, asset_names, backend


def _case_pass(case: dict[str, Any], record: dict[str, Any]) -> bool:
    expected_error = case.get("expected_error_code")
    if expected_error is not None:
        return record.get("error_code") == expected_error
    if record.get("classification") != case.get("expected_classification"):
        return False
    if case.get("expected_backend") and record.get("backend") != case["expected_backend"]:
        return False
    if case.get("expected_asset_name") and case["expected_asset_name"] not in record.get("asset_names", []):
        return False
    if case.get("expected_classification") == "answerable" and not record.get("plan_valid"):
        return False
    if case.get("expected_classification") != "answerable" and record.get("plan_valid"):
        return False
    if bool(case.get("execute")) != bool(record.get("executed")):
        return False
    if record.get("result_match") is False:
        return False
    return record.get("error_code") is None


def run_case(
    case: dict[str, Any],
    *,
    base_url: str,
    timeout: float,
    assets: dict[str, dict[str, str]],
    catalog_warning: str | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    response: dict[str, Any] = {}
    status: int | None = None
    transport_error: str | None = None
    try:
        status, response = _request_json(
            "POST",
            f"{base_url}/query/natural-language",
            payload={"question": case["question"], "execute": bool(case.get("execute"))},
            timeout=timeout,
        )
    except Exception as exc:
        transport_error = f"request_failed:{type(exc).__name__}"
    total_ms = (time.perf_counter() - started) * 1000
    plan = response.get("normalized_plan") if isinstance(response, dict) else None
    asset_ids, asset_names, backend = _plan_identity(plan, assets)
    result = response.get("result") if isinstance(response, dict) else None
    warnings = list(response.get("warnings", [])) if isinstance(response.get("warnings"), list) else []
    explanation_warning = response.get("explanation_warning")
    if isinstance(explanation_warning, dict) and explanation_warning.get("code"):
        warnings.append(explanation_warning["code"])
    if catalog_warning:
        warnings.append(catalog_warning)
    error_code = transport_error or _error_code(response)
    if status is not None and status >= 400 and error_code is None:
        error_code = f"http_{status}"
    result_match = compare_results(
        result if isinstance(result, dict) else None,
        case.get("expected_result"),
        ordered_rows=bool(case.get("ordered_rows", False)),
        tolerance=float(case.get("numeric_tolerance", DEFAULT_TOLERANCE)),
    )
    record: dict[str, Any] = {
        "case_id": case["id"],
        "category": case["category"],
        "question": case["question"],
        "classification": response.get("classification"),
        "backend": backend,
        "asset_ids": asset_ids,
        "asset_names": asset_names,
        "plan_valid": isinstance(plan, dict),
        "executed": isinstance(result, dict),
        "result_match": result_match,
        "error_code": error_code,
        "warnings": warnings,
        "planning_ms": response.get("planning_time_ms"),
        "execution_ms": response.get("execution_time_ms"),
        "explanation_ms": response.get("explanation_time_ms"),
        "total_ms": total_ms,
        "expected_classification": case.get("expected_classification"),
        "expected_backend": case.get("expected_backend"),
        "expected_asset_name": case.get("expected_asset_name"),
        "expected_error_code": case.get("expected_error_code"),
        "requested_execute": bool(case.get("execute")),
    }
    record["pass"] = _case_pass(case, record)
    return record


def _write_outputs(
    output_dir: Path,
    records: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    model_label: str,
    base_url: str,
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", model_label).strip("-") or "model"
    stem = f"benchmark-{label}-{timestamp}"
    details_path = output_dir / f"{stem}.json"
    csv_path = output_dir / f"{stem}.csv"
    summary_path = output_dir / f"{stem}.summary.json"
    details_path.write_text(
        json.dumps({"model_label": model_label, "base_url": base_url, "records": records}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow({
                field: json.dumps(record.get(field), ensure_ascii=False)
                if isinstance(record.get(field), (list, dict, bool)) or record.get(field) is None
                else record.get(field)
                for field in CSV_FIELDS
            })
    summary_path.write_text(
        json.dumps(
            {
                "model_label": model_label,
                "base_url": base_url,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                **summary,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return details_path, csv_path, summary_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the QueryX natural-language benchmark")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"))
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("results"))
    parser.add_argument("--model-label", default="configured-model")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
        if not isinstance(cases, list) or not cases:
            raise ValueError("cases file must contain a non-empty JSON array")
        base_url = args.base_url.rstrip("/")
        assets, catalog_warning = _asset_catalog(base_url, args.timeout)
        records = [
            run_case(
                case,
                base_url=base_url,
                timeout=args.timeout,
                assets=assets,
                catalog_warning=catalog_warning,
            )
            for case in cases
        ]
        paths = _write_outputs(
            args.output_dir,
            records,
            summarize_records(records),
            model_label=args.model_label,
            base_url=base_url,
        )
    except Exception as exc:
        print(f"benchmark runner failed: {exc}", file=sys.stderr)
        return 2
    print("\n".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
