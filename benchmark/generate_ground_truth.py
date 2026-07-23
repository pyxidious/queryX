#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb
from pymongo import MongoClient
from sqlalchemy import create_engine, text

from queryx.app.core.config import Settings


def _mysql(settings: Settings) -> dict[str, Any]:
    engine = create_engine(settings.mysql_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET SESSION TRANSACTION READ ONLY"))
            status = connection.execute(text(
                "SELECT status, COUNT(*) AS orders FROM orders "
                "GROUP BY status ORDER BY status"
            )).fetchall()
            paid = connection.scalar(text(
                "SELECT COUNT(*) FROM orders WHERE status = 'paid'"
            ))
            pending = connection.scalar(text(
                "SELECT COUNT(*) FROM orders WHERE status = 'pending'"
            ))
            total_sum, total_avg = connection.execute(text(
                "SELECT SUM(total), AVG(total) FROM orders"
            )).one()
        return {
            "orders_by_status": [[str(name), int(count)] for name, count in status],
            "paid_count": int(paid),
            "pending_count": int(pending),
            "total_sum": float(total_sum),
            "total_avg": float(total_avg),
        }
    finally:
        engine.dispose()


def _mongodb(settings: Settings) -> dict[str, Any]:
    client = MongoClient(settings.mongodb_url, serverSelectionTimeoutMS=5000)
    try:
        database = client[settings.mongodb_database]
        database.command("ping")
        by_type = list(database.events.aggregate([
            {"$group": {"_id": "$type", "events": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ]))
        amount = list(database.events.aggregate([
            {"$group": {"_id": None, "total": {"$sum": "$properties.amount"}}}
        ]))
        return {
            "profiles_count": database.profiles.count_documents({}),
            "events_by_type": [[item["_id"], item["events"]] for item in by_type],
            "amount_sum": float(amount[0]["total"]),
        }
    finally:
        client.close()


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _duckdb(settings: Settings) -> dict[str, Any]:
    connection = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        tables = connection.execute("SHOW ALL TABLES").fetchall()
        orders = next(row[2] for row in tables if "order_status" in row[3])
        items = next(row[2] for row in tables if "price" in row[3])
        products = next(row[2] for row in tables if "product_category_name" in row[3])
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
            "orders_by_status": [[name, count] for name, count in status],
            "price_sum": float(price_sum),
            "revenue_by_category": [[name, value] for name, value in revenue],
        }
    finally:
        connection.close()


def generate(settings: Settings) -> dict[str, Any]:
    mysql = _mysql(settings)
    mongodb = _mongodb(settings)
    duck = _duckdb(settings)
    return {
        "duckdb_orders_by_status": {"rows": duck["orders_by_status"], "unordered": True},
        "duckdb_status_sorted": {"rows": sorted(duck["orders_by_status"], key=lambda row: (-row[1], row[0]))},
        "duckdb_items_price_sum": {"rows": [[duck["price_sum"]]]},
        "duckdb_revenue_by_category": {"rows": duck["revenue_by_category"]},
        "duckdb_revenue_top_categories": {"rows": duck["revenue_by_category"]},
        "robust_duckdb_revenue": {"rows": duck["revenue_by_category"]},
        "mysql_orders_by_status": {"rows": mysql["orders_by_status"], "unordered": True},
        "mysql_count_paid": {"rows": [[mysql["paid_count"]]]},
        "mysql_average_total": {"rows": [[mysql["total_avg"]]]},
        "mysql_sum_total": {"rows": [[mysql["total_sum"]]]},
        "mysql_count_pending": {"rows": [[mysql["pending_count"]]]},
        "rephrase_mysql_average": {"rows": [[mysql["total_avg"]]]},
        "robust_mysql_paid": {"rows": [[mysql["paid_count"]]]},
        "mongodb_count_profiles": {"rows": [[mongodb["profiles_count"]]]},
        "rephrase_mongodb_profiles": {"rows": [[mongodb["profiles_count"]]]},
        "robust_mongodb_profiles": {"rows": [[mongodb["profiles_count"]]]},
        "mongodb_events_by_type": {"rows": mongodb["events_by_type"], "unordered": True},
        "mongodb_events_by_type_variant": {"rows": mongodb["events_by_type"], "unordered": True},
        "robust_mongodb_events_type": {"rows": mongodb["events_by_type"], "unordered": True},
        "mongodb_sum_amount": {"rows": [[mongodb["amount_sum"]]]},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate read-only demo ground truth")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = generate(Settings(_env_file=".env"))
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
