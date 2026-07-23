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
    "case_id", "repeat_index", "repeat_count", "category", "operation_type",
    "difficulty", "equivalence_group", "question", "classification", "backend", "asset_ids",
    "asset_names", "plan_valid", "executed", "result_match", "error_code",
    "warnings", "planning_ms", "execution_ms", "explanation_ms", "total_ms",
    "explanation_present", "limitation_explained", "clarification_requested",
    "hallucinated_asset", "hallucinated_field", "hallucinated_backend",
    "unsupported_operation", "forced_answer_on_missing_data", "pass",
]

ALLOWED_OPERATION_TYPES = {
    "count", "filter", "projection", "aggregation", "group_by", "sort",
    "top_k", "temporal_grouping", "multi_asset", "uncertainty",
    "unsupported_analysis",
}
ALLOWED_DIFFICULTIES = {"easy", "medium", "hard"}
ALLOWED_UNCERTAINTY_TYPES = {
    "ambiguous_metric", "incomplete_request", "missing_data",
    "unsupported_combination", "vague_concept", "none",
}
ALLOWED_FILTERS = {
    "eq", "neq", "ne", "gt", "gte", "lt", "lte", "in", "not_in",
    "is_null", "is_not_null", "between",
}
ALLOWED_AGGREGATIONS = {"count", "count_distinct", "sum", "avg", "min", "max"}
ALLOWED_TRANSFORMS = {None, "date_trunc_month"}


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
        if key in {"rows", "rows_prefix", "unordered"}:
            continue
        if key not in actual or not structural_equal(actual[key], expected_value, tolerance):
            return False
    if "rows_prefix" in expected:
        actual_rows = actual.get("rows")
        prefix = expected["rows_prefix"]
        return (
            isinstance(actual_rows, list)
            and isinstance(prefix, list)
            and len(actual_rows) >= len(prefix)
            and structural_equal(actual_rows[:len(prefix)], prefix, tolerance)
        )
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


def normalize_case(case: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(case)
    for required in ("id", "category", "question", "execute", "expected_classification"):
        if required not in normalized:
            raise ValueError(f"benchmark case is missing required field: {required}")
    normalized.setdefault("operation_type", "uncertainty")
    normalized.setdefault("difficulty", "medium")
    normalized.setdefault("uncertainty_type", "none")
    normalized.setdefault("repeat_count", 1)
    if normalized["operation_type"] not in ALLOWED_OPERATION_TYPES:
        raise ValueError(f"invalid operation_type in case {normalized['id']}")
    if normalized["difficulty"] not in ALLOWED_DIFFICULTIES:
        raise ValueError(f"invalid difficulty in case {normalized['id']}")
    if normalized["uncertainty_type"] not in ALLOWED_UNCERTAINTY_TYPES:
        raise ValueError(f"invalid uncertainty_type in case {normalized['id']}")
    if (
        isinstance(normalized["repeat_count"], bool)
        or not isinstance(normalized["repeat_count"], int)
        or normalized["repeat_count"] < 1
    ):
        raise ValueError(f"repeat_count must be a positive integer in case {normalized['id']}")
    return normalized


def _metric_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for record in records:
        repetitions = record.get("repetitions")
        if isinstance(repetitions, list) and repetitions:
            expanded.extend(item for item in repetitions if isinstance(item, dict))
        else:
            expanded.append(record)
    return expanded


def _consistency(values: list[Any]) -> bool:
    return bool(values) and all(value == values[0] for value in values[1:])


def _breakdown(
    records: list[dict[str, Any]], field: str
) -> dict[str, dict[str, Any]]:
    values = sorted({str(record.get(field) or "unknown") for record in records})
    return {
        value: summarize_records(
            [record for record in records if str(record.get(field) or "unknown") == value],
            include_breakdowns=False,
        )
        for value in values
    }


def _equivalence_summary(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups = sorted({str(record["equivalence_group"]) for record in records if record.get("equivalence_group")})
    result: dict[str, dict[str, Any]] = {}
    for group in groups:
        members = [record for record in records if record.get("equivalence_group") == group]
        classification = _consistency([record.get("classification") for record in members])
        backend = _consistency([record.get("backend") for record in members])
        asset = _consistency([record.get("asset_names") for record in members])
        operation = _consistency([record.get("observed_operation") for record in members])
        execution = _consistency([record.get("executed") for record in members])
        comparable_results = [record for record in members if record.get("expected_result") is not None]
        result_consistency = (
            _consistency([record.get("result_match") for record in comparable_results])
            if comparable_results else None
        )
        full = all((classification, backend, asset, operation, execution)) and (
            result_consistency is not False
        )
        result[group] = {
            "case_count": len(members),
            "classification_consistency": classification,
            "backend_consistency": backend,
            "asset_consistency": asset,
            "operation_consistency": operation,
            "execution_consistency": execution,
            "result_consistency": result_consistency,
            "group_pass_rate": _rate(sum(bool(item.get("pass")) for item in members), len(members)),
            "full_semantic_consistency": full,
        }
    return result


def _result_accuracy_breakdown(
    records: list[dict[str, Any]], field: str
) -> dict[str, dict[str, Any]]:
    verified = [record for record in records if record.get("expected_result") is not None]
    values = sorted({str(record.get(field) or "unknown") for record in verified})
    return {
        value: {
            "verified_executions": len(items),
            "result_accuracy": _rate(
                sum(record.get("result_match") is True for record in items), len(items)
            ),
        }
        for value in values
        for items in [[
            record for record in verified
            if str(record.get(field) or "unknown") == value
        ]]
    }


def summarize_records(
    records: list[dict[str, Any]], *, include_breakdowns: bool = True,
    include_categories: bool | None = None,
) -> dict[str, Any]:
    if include_categories is not None:
        include_breakdowns = include_categories
    total = len(records)
    metric_records = _metric_records(records)
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
        "total_executions": len(metric_records),
        "executed_queries": sum(bool(record.get("executed")) for record in metric_records),
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
            "planning": _latency_summary(metric_records, "planning_ms"),
            "execution": _latency_summary(metric_records, "execution_ms"),
            "explanation": _latency_summary(metric_records, "explanation_ms"),
            "total": _latency_summary(metric_records, "total_ms"),
        },
    }
    repeated = [record for record in records if int(record.get("repeat_count", 1)) > 1]
    repeat_flags = [bool(record.get("full_repeat_consistency")) for record in repeated]
    summary["repeat_consistency_rate"] = _rate(sum(repeat_flags), len(repeat_flags))
    equivalence = _equivalence_summary(records)
    summary["semantic_consistency_rate"] = _rate(
        sum(bool(group["full_semantic_consistency"]) for group in equivalence.values()),
        len(equivalence),
    )
    hallucinations = [
        bool(record.get("hallucinated_asset"))
        or bool(record.get("hallucinated_field"))
        or bool(record.get("hallucinated_backend"))
        or bool(record.get("unsupported_operation"))
        for record in metric_records
    ]
    summary["structural_hallucination_rate"] = _rate(sum(hallucinations), len(hallucinations))
    missing = [
        record for record in metric_records
        if record.get("expected_classification") == "unanswerable"
    ]
    summary["forced_answer_rate"] = _rate(
        sum(bool(record.get("forced_answer_on_missing_data")) for record in missing),
        len(missing),
    )
    summary["prudent_refusal_rate"] = _rate(
        sum(
            record.get("classification") == "unanswerable" and not record.get("plan_valid")
            for record in missing
        ),
        len(missing),
    )
    summary["timeout_rate"] = _rate(
        sum("timeout" in str(record.get("error_code") or "") for record in metric_records),
        len(metric_records),
    )
    summary["error_rate"] = _rate(
        sum(record.get("error_code") is not None for record in metric_records), len(metric_records)
    )
    verified_cases = [record for record in records if record.get("expected_result") is not None]
    verified_executions = [
        record for record in metric_records if record.get("expected_result") is not None
    ]
    summary["result_verified_cases"] = len(verified_cases)
    summary["result_verified_executions"] = len(verified_executions)
    summary["result_verified_rate"] = _rate(len(verified_cases), total)
    summary["result_accuracy"] = _rate(
        sum(record.get("result_match") is True for record in verified_executions),
        len(verified_executions),
    )
    summary["result_accuracy_by_backend"] = _result_accuracy_breakdown(
        metric_records, "expected_backend"
    )
    summary["result_accuracy_by_operation_type"] = _result_accuracy_breakdown(
        metric_records, "operation_type"
    )
    if include_breakdowns:
        summary["by_category"] = _breakdown(records, "category")
        summary["by_backend"] = _breakdown(records, "expected_backend")
        summary["by_operation_type"] = _breakdown(records, "operation_type")
        summary["by_difficulty"] = _breakdown(records, "difficulty")
        summary["by_uncertainty_type"] = _breakdown(records, "uncertainty_type")
        summary["by_equivalence_group"] = equivalence
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


def _asset_catalog(base_url: str, timeout: float) -> tuple[dict[str, dict[str, Any]], str | None]:
    try:
        status, payload = _request_json("GET", f"{base_url}/assets", payload=None, timeout=timeout)
        if status >= 400:
            return {}, _error_code(payload) or f"http_{status}"
        assets: dict[str, dict[str, Any]] = {}
        for asset in payload.get("assets", []):
            if not isinstance(asset, dict) or not isinstance(asset.get("id"), str):
                continue
            kind = str(asset.get("asset_kind", ""))
            backend = {
                "mysql_table": "mysql",
                "mongodb_collection": "mongodb",
            }.get(kind, "duckdb")
            fields: set[str] = set()
            schemas: list[Any] = [asset.get("fields"), asset.get("observed_schema")]
            for version in asset.get("versions", []):
                if isinstance(version, dict):
                    technical = version.get("technical_metadata", {})
                    schemas.extend([
                        version.get("fields"), version.get("observed_schema"),
                        technical.get("fields") if isinstance(technical, dict) else None,
                        technical.get("observed_schema") if isinstance(technical, dict) else None,
                    ])
                    for binding in version.get("storage_bindings", []):
                        if isinstance(binding, dict) and isinstance(binding.get("metadata"), dict):
                            schemas.append(binding["metadata"].get("serving_schema"))
            for schema in schemas:
                if isinstance(schema, list):
                    fields.update(
                        str(field["name"])
                        for field in schema
                        if isinstance(field, dict) and field.get("name")
                    )
                elif isinstance(schema, dict):
                    fields.update(str(name) for name in schema)
            assets[asset["id"]] = {
                "name": str(asset.get("name", asset["id"])),
                "backend": backend,
                "fields": sorted(fields),
            }
        return assets, None
    except Exception as exc:  # catalog lookup is best effort; cases still run
        return {}, f"catalog_lookup_failed:{type(exc).__name__}"


def _plan_identity(
    plan: Any, assets: dict[str, dict[str, Any]]
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


def _observed_operation(plan: Any) -> str | None:
    if not isinstance(plan, dict):
        return None
    if len(plan.get("sources", [])) > 1 or plan.get("joins"):
        return "multi_asset"
    if any(item.get("transform") == "date_trunc_month" for item in plan.get("group_by", []) if isinstance(item, dict)):
        return "temporal_grouping"
    if plan.get("group_by"):
        return "group_by"
    if plan.get("aggregations"):
        functions = {
            item.get("function") for item in plan["aggregations"] if isinstance(item, dict)
        }
        return "count" if functions and functions <= {"count", "count_distinct"} else "aggregation"
    if plan.get("filters"):
        return "filter"
    if plan.get("order_by"):
        return "sort"
    if plan.get("projections"):
        return "projection"
    return None


def _structural_flags(
    plan: Any,
    response: dict[str, Any],
    assets: dict[str, dict[str, Any]],
) -> dict[str, bool]:
    if not isinstance(plan, dict):
        return {
            "hallucinated_asset": False,
            "hallucinated_field": False,
            "hallucinated_backend": False,
            "unsupported_operation": False,
        }
    sources = {
        source.get("alias"): source.get("asset_id")
        for source in plan.get("sources", [])
        if isinstance(source, dict)
    }
    hallucinated_asset = any(
        not isinstance(asset_id, str) or asset_id not in assets
        for asset_id in sources.values()
    )
    hallucinated_field = False
    for section in ("projections", "filters", "aggregations", "group_by"):
        for item in plan.get(section, []):
            if not isinstance(item, dict) or not item.get("field"):
                continue
            asset = assets.get(str(sources.get(item.get("source_alias"))), {})
            known_fields = set(asset.get("fields", []))
            if known_fields and item["field"] not in known_fields:
                hallucinated_field = True
    known_backends = {
        assets[asset_id]["backend"]
        for asset_id in sources.values()
        if isinstance(asset_id, str) and asset_id in assets
    }
    declared_backend = response.get("backend")
    hallucinated_backend = bool(
        declared_backend and known_backends and declared_backend not in known_backends
    )
    unsupported_operation = any(
        item.get("operator") not in ALLOWED_FILTERS
        for item in plan.get("filters", []) if isinstance(item, dict)
    ) or any(
        item.get("function") not in ALLOWED_AGGREGATIONS
        for item in plan.get("aggregations", []) if isinstance(item, dict)
    ) or any(
        item.get("transform") not in ALLOWED_TRANSFORMS
        for section in ("projections", "group_by")
        for item in plan.get(section, []) if isinstance(item, dict)
    )
    return {
        "hallucinated_asset": hallucinated_asset,
        "hallucinated_field": hallucinated_field,
        "hallucinated_backend": hallucinated_backend,
        "unsupported_operation": unsupported_operation,
    }


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
    assets: dict[str, dict[str, Any]],
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
        ordered_rows=bool(case.get("ordered_rows", False)) or (
            isinstance(case.get("expected_result"), dict)
            and case["expected_result"].get("unordered") is False
        ),
        tolerance=float(case.get("numeric_tolerance", DEFAULT_TOLERANCE)),
    )
    structural_flags = _structural_flags(plan, response, assets)
    classification = response.get("classification")
    plan_valid = isinstance(plan, dict)
    record: dict[str, Any] = {
        "case_id": case["id"],
        "category": case["category"],
        "operation_type": case.get("operation_type", "uncertainty"),
        "difficulty": case.get("difficulty", "medium"),
        "uncertainty_type": case.get("uncertainty_type", "none"),
        "equivalence_group": case.get("equivalence_group"),
        "question": case["question"],
        "classification": classification,
        "backend": backend,
        "asset_ids": asset_ids,
        "asset_names": asset_names,
        "plan_valid": plan_valid,
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
        "expected_result": case.get("expected_result"),
        "result": result if isinstance(result, dict) else None,
        "observed_operation": _observed_operation(plan),
        "explanation_present": bool(str(response.get("answer") or "").strip()),
        "limitation_explained": bool(
            str(response.get("reason") or response.get("answer") or "").strip()
        ) if case.get("expected_classification") in {"ambiguous", "unanswerable"} else None,
        "clarification_requested": bool(
            str(response.get("clarification_question") or "").strip()
        ) if case.get("expected_classification") == "ambiguous" else None,
        **structural_flags,
    }
    record["forced_answer_on_missing_data"] = bool(
        case.get("expected_classification") == "unanswerable"
        and classification == "answerable"
        and plan_valid
    )
    record["pass"] = _case_pass(case, record)
    return record


def aggregate_repetitions(
    case: dict[str, Any], repetitions: list[dict[str, Any]]
) -> dict[str, Any]:
    first = dict(repetitions[0])
    classification = _consistency([item.get("classification") for item in repetitions])
    backend = _consistency([item.get("backend") for item in repetitions])
    asset = _consistency([item.get("asset_names") for item in repetitions])
    plan_validity = _consistency([item.get("plan_valid") for item in repetitions])
    outcome = _consistency([item.get("pass") for item in repetitions])
    result_consistency: bool | None = None
    if case.get("expected_result") is not None:
        baseline_result = repetitions[0].get("result")
        baseline = (
            {
                key: baseline_result[key]
                for key in ("columns", "rows", "row_count", "truncated")
                if isinstance(baseline_result, dict) and key in baseline_result
            }
            if isinstance(baseline_result, dict) else None
        )
        result_consistency = all(
            item.get("result_match") is True for item in repetitions
        ) and all(
            compare_results(
                item.get("result"), baseline,
                ordered_rows=bool(case.get("ordered_rows", False)) or (
                    isinstance(case.get("expected_result"), dict)
                    and case["expected_result"].get("unordered") is False
                ),
                tolerance=float(case.get("numeric_tolerance", DEFAULT_TOLERANCE)),
            ) is True
            for item in repetitions[1:]
        )
    full = all((classification, backend, asset, plan_validity, outcome)) and (
        result_consistency is not False
    )
    first.update({
        "repeat_count": len(repetitions),
        "repetitions": repetitions,
        "repeat_consistency_classification": classification,
        "repeat_consistency_backend": backend,
        "repeat_consistency_asset": asset,
        "repeat_consistency_plan_validity": plan_validity,
        "repeat_consistency_result": result_consistency,
        "repeat_consistency_outcome": outcome,
        "full_repeat_consistency": full,
        "pass": all(bool(item.get("pass")) for item in repetitions),
    })
    return first


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
    csv_records = _metric_records(records)
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in csv_records:
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
        raw_cases = json.loads(args.cases.read_text(encoding="utf-8"))
        if not isinstance(raw_cases, list) or not raw_cases:
            raise ValueError("cases file must contain a non-empty JSON array")
        cases = [normalize_case(case) for case in raw_cases]
        if len({case["id"] for case in cases}) != len(cases):
            raise ValueError("benchmark case ids must be unique")
        base_url = args.base_url.rstrip("/")
        assets, catalog_warning = _asset_catalog(base_url, args.timeout)
        records: list[dict[str, Any]] = []
        for case in cases:
            repetitions: list[dict[str, Any]] = []
            for repeat_index in range(1, case["repeat_count"] + 1):
                record = run_case(
                    case,
                    base_url=base_url,
                    timeout=args.timeout,
                    assets=assets,
                    catalog_warning=catalog_warning,
                )
                record["repeat_index"] = repeat_index
                record["repeat_count"] = case["repeat_count"]
                repetitions.append(record)
            records.append(aggregate_repetitions(case, repetitions))
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
