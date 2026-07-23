from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from pymongo import MongoClient
from sqlalchemy import create_engine, text

from queryx.app.core.config import Settings


FIXED_SEED = 20260723
TARGET_CUSTOMERS = 100
TARGET_ORDERS = 500
TARGET_PROFILES = 100
TARGET_EVENTS = 500
_POOL_SIZE = 2_000
_START = datetime(2024, 1, 1, 8, 0, 0)


class SeedError(RuntimeError):
    pass


def select_missing(
    candidates: list[dict[str, Any]],
    existing_keys: set[Any],
    needed: int,
    *,
    key: str,
) -> list[dict[str, Any]]:
    if needed <= 0:
        return []
    selected = [item for item in candidates if item[key] not in existing_keys][:needed]
    if len(selected) != needed:
        raise SeedError(f"not enough deterministic candidates for {key}")
    return selected


def generate_customers(pool_size: int = _POOL_SIZE) -> list[dict[str, Any]]:
    first_names = ("Alex", "Camille", "Diego", "Elena", "Fatima", "Hugo", "Iris", "Noah")
    last_names = ("Bianchi", "Costa", "Dubois", "Garcia", "Khan", "Martin", "Rossi", "Silva")
    return [
        {
            "id": 10_001 + index,
            "email": f"queryx.seed.customer.{index + 1:04d}@example.test",
            "name": f"{first_names[index % len(first_names)]} "
            f"{last_names[(index * 3) % len(last_names)]}",
            "created_at": _START + timedelta(hours=index * 6),
        }
        for index in range(pool_size)
    ]


def generate_orders(
    customer_ids: list[int], pool_size: int = _POOL_SIZE
) -> list[dict[str, Any]]:
    if not customer_ids:
        raise SeedError("orders require at least one customer")
    rng = random.Random(FIXED_SEED + 1)
    statuses = ("paid", "pending", "shipped", "cancelled", "refunded")
    status_weights = (45, 20, 20, 10, 5)
    note_values = (
        "priority customer",
        "gift order",
        "manual review",
        "delivery instructions available",
    )
    orders: list[dict[str, Any]] = []
    for index in range(pool_size):
        notes = None if rng.random() < 0.8 else rng.choice(note_values)
        orders.append({
            "id": 20_001 + index,
            "customer_id": rng.choice(customer_ids),
            "status": rng.choices(statuses, weights=status_weights, k=1)[0],
            "total": Decimal(rng.randint(1_000, 50_000)) / Decimal(100),
            "notes": notes,
            "created_at": _START + timedelta(minutes=index * 53),
        })
    return orders


def generate_profiles(pool_size: int = _POOL_SIZE) -> list[dict[str, Any]]:
    rng = random.Random(FIXED_SEED + 2)
    languages = ("en", "it", "fr", "es")
    language_weights = (40, 30, 20, 10)
    roles = ("customer", "analyst", "editor", "support", "admin")
    profiles: list[dict[str, Any]] = []
    for index in range(pool_size):
        newsletter_roll = rng.random()
        preferences: dict[str, Any] = {
            "language": rng.choices(languages, weights=language_weights, k=1)[0]
        }
        if newsletter_roll < 0.45:
            preferences["newsletter"] = True
        elif newsletter_roll < 0.80:
            preferences["newsletter"] = False
        profiles.append({
            "_id": f"queryx-seed-profile-{index + 1:04d}",
            "email": f"queryx.seed.profile.{index + 1:04d}@example.test",
            "preferences": preferences,
            "roles": sorted(rng.sample(roles, k=rng.randint(1, 3))),
        })
    return profiles


def generate_events(pool_size: int = _POOL_SIZE) -> list[dict[str, Any]]:
    rng = random.Random(FIXED_SEED + 3)
    event_types = ("purchase", "page_view", "login", "logout")
    event_weights = (25, 45, 20, 10)
    devices = ("desktop", "mobile", "tablet")
    paths = ("/", "/catalog", "/products", "/cart", "/account")
    currencies = ("EUR", "USD", "GBP")
    events: list[dict[str, Any]] = []
    for index in range(pool_size):
        event_type = rng.choices(event_types, weights=event_weights, k=1)[0]
        properties: dict[str, Any] = {"device": rng.choice(devices)}
        event: dict[str, Any] = {
            "_id": f"queryx-seed-event-{index + 1:05d}",
            "user_id": rng.randint(1, TARGET_PROFILES),
            "type": event_type,
            "created_at": (_START.replace(tzinfo=timezone.utc) + timedelta(minutes=index * 17)),
            "properties": properties,
        }
        if event_type == "purchase":
            properties.update({
                "amount": rng.randint(1_000, 50_000) / 100,
                "currency": rng.choice(currencies),
                "path": "/checkout/complete",
            })
            event["items"] = [
                {
                    "sku": f"DEMO-{rng.randint(1, 80):03d}",
                    "quantity": rng.randint(1, 4),
                }
                for _ in range(rng.randint(1, 3))
            ]
            event["tags"] = ["checkout", "seed"]
        elif event_type == "page_view":
            properties["path"] = rng.choice(paths)
            event["tags"] = ["web", rng.choice(("anonymous", "authenticated"))]
        else:
            properties["path"] = "/account/session"
            event["tags"] = ["authentication"]
        events.append(event)
    return events


def _ensure_not_over_target(label: str, current: int, target: int) -> None:
    if current > target:
        raise SeedError(
            f"{label} already contains {current} records, above target {target}; "
            "no records were deleted"
        )


def seed_mysql(settings: Settings) -> dict[str, int]:
    engine = create_engine(settings.mysql_url, pool_pre_ping=True)
    connection = engine.connect()
    try:
        if connection.scalar(text("SELECT GET_LOCK('queryx_demo_seed', 30)")) != 1:
            raise SeedError("could not acquire MySQL seed lock")
        columns = {
            row[0]
            for row in connection.execute(text("SHOW COLUMNS FROM orders"))
        }
        if "notes" not in columns:
            connection.execute(text("ALTER TABLE orders ADD COLUMN notes TEXT NULL"))
            connection.commit()

        existing_customers = list(
            connection.execute(text("SELECT id, email FROM customers ORDER BY id"))
        )
        _ensure_not_over_target("customers", len(existing_customers), TARGET_CUSTOMERS)
        customer_candidates = [
            item for item in generate_customers()
            if item["email"] not in {str(row[1]) for row in existing_customers}
        ]
        customers = select_missing(
            customer_candidates,
            {int(row[0]) for row in existing_customers},
            TARGET_CUSTOMERS - len(existing_customers),
            key="id",
        )
        if customers:
            connection.execute(text(
                "INSERT IGNORE INTO customers (id, email, name, created_at) "
                "VALUES (:id, :email, :name, :created_at)"
            ), customers)
            connection.commit()

        customer_ids = [
            int(row[0])
            for row in connection.execute(text("SELECT id FROM customers ORDER BY id"))
        ]
        existing_order_ids = {
            int(row[0]) for row in connection.execute(text("SELECT id FROM orders"))
        }
        _ensure_not_over_target("orders", len(existing_order_ids), TARGET_ORDERS)
        orders = select_missing(
            generate_orders(customer_ids),
            existing_order_ids,
            TARGET_ORDERS - len(existing_order_ids),
            key="id",
        )
        if orders:
            connection.execute(text(
                "INSERT IGNORE INTO orders "
                "(id, customer_id, status, total, notes, created_at) "
                "VALUES (:id, :customer_id, :status, :total, :notes, :created_at)"
            ), orders)
            connection.commit()

        counts = {
            "customers": int(connection.scalar(text("SELECT COUNT(*) FROM customers"))),
            "orders": int(connection.scalar(text("SELECT COUNT(*) FROM orders"))),
        }
        if counts != {"customers": TARGET_CUSTOMERS, "orders": TARGET_ORDERS}:
            raise SeedError(f"unexpected MySQL counts: {counts}")
        return counts
    finally:
        try:
            connection.execute(text("SELECT RELEASE_LOCK('queryx_demo_seed')"))
            connection.commit()
        finally:
            connection.close()
            engine.dispose()


def _seed_mongo_collection(
    collection: Any,
    candidates: list[dict[str, Any]],
    target: int,
    label: str,
) -> int:
    current = int(collection.count_documents({}))
    _ensure_not_over_target(label, current, target)
    existing_ids = set(collection.distinct("_id"))
    existing_emails = set(collection.distinct("email")) if label == "profiles" else set()
    eligible = [
        item for item in candidates
        if item.get("email") not in existing_emails
    ]
    selected = select_missing(
        eligible, existing_ids, target - current, key="_id"
    )
    for document in selected:
        collection.update_one(
            {"_id": document["_id"]}, {"$setOnInsert": document}, upsert=True
        )
    final = int(collection.count_documents({}))
    if final != target:
        raise SeedError(f"unexpected MongoDB {label} count: {final}")
    return final


def seed_mongodb(settings: Settings) -> dict[str, int]:
    client = MongoClient(settings.mongodb_url)
    try:
        database = client[settings.mongodb_database]
        return {
            "profiles": _seed_mongo_collection(
                database["profiles"], generate_profiles(), TARGET_PROFILES, "profiles"
            ),
            "events": _seed_mongo_collection(
                database["events"], generate_events(), TARGET_EVENTS, "events"
            ),
        }
    finally:
        client.close()


def main() -> int:
    settings = Settings()
    counts = {
        "mysql": seed_mysql(settings),
        "mongodb": seed_mongodb(settings),
    }
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
