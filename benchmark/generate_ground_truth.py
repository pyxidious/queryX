#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import duckdb
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from queryx.app.core.config import Settings


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
DEFAULT_CASES_PATH = PACKAGE_DIR / "cases.json"


class GroundTruthError(RuntimeError):
    """An expected operational failure with a concise user-facing message."""


@dataclass(frozen=True)
class UpdateReport:
    processed: int
    changed: int
    unchanged: int


_SIX_DECIMALS = Decimal("0.000001")
_FIVE_DECIMALS = Decimal("0.00001")


def _numeric_error(case_id: str, path: str, reason: str) -> GroundTruthError:
    return GroundTruthError(
        f"Invalid numeric ground truth for case_id={case_id} at {path}: {reason}"
    )


def _normalized_decimal(value: Decimal, *, case_id: str, path: str) -> int | float:
    if not value.is_finite():
        raise _numeric_error(case_id, path, "NaN and Infinity are not supported")
    try:
        quantized = value.quantize(_SIX_DECIMALS, rounding=ROUND_HALF_UP)
        shorter = quantized.quantize(_FIVE_DECIMALS, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise _numeric_error(case_id, path, "value cannot be quantized") from exc
    # A value one 1e-6 quantum away from a shorter decimal boundary is binary
    # accumulation noise at the benchmark's existing tolerance. Canonicalizing
    # that boundary makes equivalent DuckDB sums byte-for-byte stable.
    if shorter != 0 and abs(quantized - shorter) <= _SIX_DECIMALS:
        quantized = shorter
    if quantized == quantized.to_integral_value():
        return int(quantized)
    normalized = float(quantized)
    if not math.isfinite(normalized):
        raise _numeric_error(case_id, path, "normalized value is not finite")
    return normalized


def normalize_numeric(
    value: Any, *, case_id: str, path: str = "expected_result"
) -> Any:
    """Recursively canonicalize JSON numbers without changing other values."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return _normalized_decimal(value, case_id=case_id, path=path)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _numeric_error(case_id, path, "NaN and Infinity are not supported")
        return _normalized_decimal(Decimal(str(value)), case_id=case_id, path=path)
    if isinstance(value, list):
        return [
            normalize_numeric(item, case_id=case_id, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, tuple):
        return tuple(
            normalize_numeric(item, case_id=case_id, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    if isinstance(value, dict):
        return {
            key: normalize_numeric(
                item, case_id=case_id, path=f"{path}.{key}"
            )
            for key, item in value.items()
        }
    return value


def _configured_duckdb_path(settings: Settings) -> Path:
    configured = Path(settings.duckdb_path).expanduser()
    return configured if configured.is_absolute() else (Path.cwd() / configured).resolve()


def _check_cases_path(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise GroundTruthError(f"Benchmark cases not found: {path}")
    if not os.access(path.parent, os.W_OK):
        raise GroundTruthError(
            f"Benchmark directory is not writable: {path.parent}\n"
            "Check the benchmark bind-mount permissions."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GroundTruthError(f"Cannot read benchmark cases: {path}") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise GroundTruthError(f"Benchmark cases must contain a JSON array: {path}")
    return payload


def _mysql(settings: Settings) -> dict[str, Any]:
    engine = create_engine(settings.mysql_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            tables = {
                str(row[0])
                for row in connection.execute(text("SHOW TABLES")).fetchall()
            }
            missing = {"customers", "orders"} - tables
            if missing:
                raise GroundTruthError(
                    "MySQL is missing required demo tables: "
                    + ", ".join(sorted(missing))
                )
            connection.execute(text("SET SESSION TRANSACTION READ ONLY"))
            status = connection.execute(
                text(
                    "SELECT status, COUNT(*) AS orders FROM orders "
                    "GROUP BY status ORDER BY status"
                )
            ).fetchall()
            paid = connection.scalar(
                text("SELECT COUNT(*) FROM orders WHERE status = 'paid'")
            )
            pending = connection.scalar(
                text("SELECT COUNT(*) FROM orders WHERE status = 'pending'")
            )
            total_sum, total_avg = connection.execute(
                text("SELECT SUM(total), AVG(total) FROM orders")
            ).one()
        return {
            "orders_by_status": [[str(name), int(count)] for name, count in status],
            "paid_count": int(paid),
            "pending_count": int(pending),
            "total_sum": float(total_sum),
            "total_avg": float(total_avg),
        }
    except GroundTruthError:
        raise
    except SQLAlchemyError as exc:
        raise GroundTruthError(
            "MySQL is unavailable or cannot be queried. "
            "Start the Compose stack and verify the mysql service health."
        ) from exc
    finally:
        engine.dispose()


def _mongodb(settings: Settings) -> dict[str, Any]:
    client = MongoClient(settings.mongodb_url, serverSelectionTimeoutMS=5000)
    try:
        database = client[settings.mongodb_database]
        database.command("ping")
        collections = set(database.list_collection_names())
        missing = {"profiles", "events"} - collections
        if missing:
            raise GroundTruthError(
                "MongoDB is missing required demo collections: "
                + ", ".join(sorted(missing))
            )
        by_type = list(
            database.events.aggregate(
                [
                    {"$group": {"_id": "$type", "events": {"$sum": 1}}},
                    {"$sort": {"_id": 1}},
                ]
            )
        )
        amount = list(
            database.events.aggregate(
                [{"$group": {"_id": None, "total": {"$sum": "$properties.amount"}}}]
            )
        )
        if not amount:
            raise GroundTruthError(
                "MongoDB events contain no values for properties.amount."
            )
        return {
            "profiles_count": int(database.profiles.count_documents({})),
            "events_by_type": [
                [str(item["_id"]), int(item["events"])] for item in by_type
            ],
            "amount_sum": float(amount[0]["total"]),
        }
    except GroundTruthError:
        raise
    except PyMongoError as exc:
        raise GroundTruthError(
            "MongoDB is unavailable or cannot be queried. "
            "Start the Compose stack and verify the mongodb service health."
        ) from exc
    finally:
        client.close()


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _duckdb(settings: Settings) -> dict[str, Any]:
    path = _configured_duckdb_path(settings)
    if not path.is_file():
        raise GroundTruthError(
            f"DuckDB database not found at configured path: {path}\n"
            "Run the ingestion/discovery setup before regenerating ground truth."
        )
    try:
        connection = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:
        raise GroundTruthError(f"DuckDB database cannot be opened: {path}") from exc
    try:
        tables = connection.execute("SHOW ALL TABLES").fetchall()

        def table_with(field: str) -> str:
            match = next((row[2] for row in tables if field in row[3]), None)
            if match is None:
                raise GroundTruthError(
                    f"DuckDB is missing a serving table containing field: {field}\n"
                    "Run the ingestion/processing setup before regenerating ground truth."
                )
            return str(match)

        orders = table_with("order_status")
        items = table_with("price")
        products = table_with("product_category_name")
        schema = _quoted(settings.duckdb_schema)
        status = connection.execute(
            f"SELECT order_status, COUNT(order_id) AS orders "
            f"FROM {schema}.{_quoted(orders)} GROUP BY order_status ORDER BY order_status"
        ).fetchall()
        price_sum = connection.execute(
            f"SELECT SUM(price) FROM {schema}.{_quoted(items)}"
        ).fetchone()[0]
        revenue = connection.execute(
            f"SELECT p.product_category_name AS category, SUM(i.price) AS revenue "
            f"FROM {schema}.{_quoted(items)} AS i "
            f"JOIN {schema}.{_quoted(products)} AS p ON p.product_id = i.product_id "
            "GROUP BY p.product_category_name "
            "ORDER BY revenue DESC, category ASC NULLS LAST"
        ).fetchall()
        return {
            "orders_by_status": [[name, int(count)] for name, count in status],
            "price_sum": float(price_sum),
            "revenue_by_category": [[name, float(value)] for name, value in revenue],
        }
    except GroundTruthError:
        raise
    except duckdb.Error as exc:
        raise GroundTruthError(
            "DuckDB ground-truth queries failed. "
            "Verify that the required demo assets are processed and ready."
        ) from exc
    finally:
        connection.close()


def generate(settings: Settings) -> dict[str, Any]:
    mysql = _mysql(settings)
    print("MySQL: connected; customers and orders are available.", file=sys.stderr)
    mongodb = _mongodb(settings)
    print("MongoDB: connected; profiles and events are available.", file=sys.stderr)
    duck = _duckdb(settings)
    print(
        f"DuckDB: opened {_configured_duckdb_path(settings)}; demo assets are available.",
        file=sys.stderr,
    )
    return {
        "duckdb_orders_by_status": {
            "rows": duck["orders_by_status"],
            "unordered": True,
        },
        "duckdb_status_sorted": {
            "rows": sorted(
                duck["orders_by_status"], key=lambda row: (-row[1], row[0])
            )
        },
        "duckdb_items_price_sum": {"rows": [[duck["price_sum"]]]},
        "duckdb_revenue_by_category": {"rows": duck["revenue_by_category"]},
        "duckdb_revenue_top_categories": {"rows": duck["revenue_by_category"]},
        "robust_duckdb_revenue": {"rows": duck["revenue_by_category"]},
        "mysql_orders_by_status": {
            "rows": mysql["orders_by_status"],
            "unordered": True,
        },
        "mysql_count_paid": {"rows": [[mysql["paid_count"]]]},
        "mysql_average_total": {"rows": [[mysql["total_avg"]]]},
        "mysql_sum_total": {"rows": [[mysql["total_sum"]]]},
        "mysql_count_pending": {"rows": [[mysql["pending_count"]]]},
        "rephrase_mysql_average": {"rows": [[mysql["total_avg"]]]},
        "robust_mysql_paid": {"rows": [[mysql["paid_count"]]]},
        "mongodb_count_profiles": {"rows": [[mongodb["profiles_count"]]]},
        "rephrase_mongodb_profiles": {"rows": [[mongodb["profiles_count"]]]},
        "robust_mongodb_profiles": {"rows": [[mongodb["profiles_count"]]]},
        "mongodb_events_by_type": {
            "rows": mongodb["events_by_type"],
            "unordered": True,
        },
        "mongodb_events_by_type_variant": {
            "rows": mongodb["events_by_type"],
            "unordered": True,
        },
        "robust_mongodb_events_type": {
            "rows": mongodb["events_by_type"],
            "unordered": True,
        },
        "mongodb_sum_amount": {"rows": [[mongodb["amount_sum"]]]},
    }


def _updated_expected_result(
    current: dict[str, Any], generated: dict[str, Any]
) -> dict[str, Any]:
    updated = dict(current)
    generated_rows = generated["rows"]
    if "rows_prefix" in current:
        prefix_size = len(current["rows_prefix"])
        updated["rows_prefix"] = generated_rows[:prefix_size]
        updated["row_count"] = len(generated_rows)
        updated.pop("rows", None)
    else:
        updated["rows"] = generated_rows
    if "unordered" in generated:
        updated["unordered"] = generated["unordered"]
    return updated


def update_cases(
    cases: list[dict[str, Any]], ground_truth: dict[str, Any]
) -> UpdateReport:
    processed = 0
    changed = 0
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if case_id not in ground_truth:
            continue
        current = case.get("expected_result")
        if not isinstance(current, dict):
            raise GroundTruthError(
                f"Ground-truth case has no expected_result structure: {case_id}"
            )
        normalized_current = normalize_numeric(current, case_id=str(case_id))
        candidate = _updated_expected_result(current, ground_truth[case_id])
        normalized_candidate = normalize_numeric(
            candidate, case_id=str(case_id)
        )
        case["expected_result"] = normalized_candidate
        if normalized_candidate != normalized_current:
            changed += 1
        seen.add(str(case_id))
        processed += 1
    missing = set(ground_truth) - seen
    if missing:
        raise GroundTruthError(
            "Ground-truth case IDs are missing from cases.json: "
            + ", ".join(sorted(missing))
        )
    return UpdateReport(
        processed=processed,
        changed=changed,
        unchanged=processed - changed,
    )


def _write_json(path: Path, payload: Any) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rendered = (
            json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )
        original_stat = path.stat() if path.exists() else None
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        if original_stat is not None:
            os.chmod(temporary_path, stat.S_IMODE(original_stat.st_mode))
            try:
                os.chown(temporary_path, original_stat.st_uid, original_stat.st_gid)
            except PermissionError:
                pass
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as exc:
        raise GroundTruthError(f"Cannot write ground-truth file: {path}") from exc
    except ValueError as exc:
        raise GroundTruthError(
            f"Cannot serialize non-standard JSON ground truth: {path}"
        ) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate read-only demo ground truth")
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="cases.json to update (default: package-local cases.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional standalone JSON file containing generated ground truth",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="show a traceback for operational failures",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        cases_path = args.cases.expanduser().resolve()
        cases = _check_cases_path(cases_path)
        settings = Settings(_env_file=PROJECT_DIR / ".env")
        ground_truth = generate(settings)
        report = update_cases(cases, ground_truth)
        _write_json(cases_path, cases)
        if args.output is not None:
            normalized_output = {
                case_id: normalize_numeric(payload, case_id=case_id)
                for case_id, payload in ground_truth.items()
            }
            _write_json(args.output.expanduser().resolve(), normalized_output)
        print(f"Ground-truth cases processed: {report.processed}")
        print(f"Ground-truth values changed: {report.changed}")
        print(f"Ground-truth values unchanged: {report.unchanged}")
        print(f"Output: {cases_path}")
        if args.output is not None:
            print(f"Wrote standalone ground truth to {args.output.expanduser().resolve()}.")
        return 0
    except GroundTruthError as exc:
        if args.debug:
            raise
        print(f"Ground-truth generation failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if args.debug:
            raise
        print(
            "Ground-truth generation failed unexpectedly "
            f"({type(exc).__name__}). Re-run with --debug for details.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
