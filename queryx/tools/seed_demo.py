from __future__ import annotations

import json
import os
import random
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from pymongo import ASCENDING, MongoClient, UpdateOne
from sqlalchemy import create_engine, text

from queryx.app.core.config import Settings


FIXED_SEED = 20260723
TARGET_CUSTOMERS = 10_000
TARGET_ORDERS = 100_000
TARGET_PROFILES = 10_000
TARGET_EVENTS = 100_000
DEFAULT_BATCH_SIZE = 2_000
_START = datetime(2024, 1, 1, 8, 0, 0)


class SeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeedTargets:
    mysql_customers: int = TARGET_CUSTOMERS
    mysql_orders: int = TARGET_ORDERS
    mongodb_profiles: int = TARGET_PROFILES
    mongodb_events: int = TARGET_EVENTS
    batch_size: int = DEFAULT_BATCH_SIZE


def _positive_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise SeedError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise SeedError(f"{name} must be a positive integer")
    return value


def load_targets() -> SeedTargets:
    return SeedTargets(
        mysql_customers=_positive_env(
            "QUERYX_SEED_MYSQL_CUSTOMERS", TARGET_CUSTOMERS
        ),
        mysql_orders=_positive_env("QUERYX_SEED_MYSQL_ORDERS", TARGET_ORDERS),
        mongodb_profiles=_positive_env(
            "QUERYX_SEED_MONGODB_PROFILES", TARGET_PROFILES
        ),
        mongodb_events=_positive_env(
            "QUERYX_SEED_MONGODB_EVENTS", TARGET_EVENTS
        ),
        batch_size=_positive_env("QUERYX_SEED_BATCH_SIZE", DEFAULT_BATCH_SIZE),
    )


def _batches(items: Iterable[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def _selected_missing(
    candidates: Iterable[dict[str, Any]],
    existing_keys: set[Any],
    needed: int,
    *,
    key: str,
    excluded_values: set[Any] | None = None,
    excluded_key: str | None = None,
) -> Iterator[dict[str, Any]]:
    if needed <= 0:
        return
    selected = 0
    for item in candidates:
        if item[key] in existing_keys:
            continue
        if (
            excluded_values is not None
            and excluded_key is not None
            and item.get(excluded_key) in excluded_values
        ):
            continue
        yield item
        selected += 1
        if selected == needed:
            return
    if selected != needed:
        raise SeedError(f"not enough deterministic candidates for {key}")


def select_missing(
    candidates: list[dict[str, Any]],
    existing_keys: set[Any],
    needed: int,
    *,
    key: str,
) -> list[dict[str, Any]]:
    return list(_selected_missing(candidates, existing_keys, needed, key=key))


def iter_customers(pool_size: int) -> Iterator[dict[str, Any]]:
    first_names = ("Alex", "Camille", "Diego", "Elena", "Fatima", "Hugo", "Iris", "Noah")
    last_names = ("Bianchi", "Costa", "Dubois", "Garcia", "Khan", "Martin", "Rossi", "Silva")
    for index in range(pool_size):
        yield {
            "id": 10_001 + index,
            "email": f"queryx.seed.customer.{index + 1:04d}@example.test",
            "name": (
                f"{first_names[index % len(first_names)]} "
                f"{last_names[(index * 3) % len(last_names)]}"
            ),
            "created_at": _START + timedelta(hours=index * 6),
        }


def generate_customers(pool_size: int = DEFAULT_BATCH_SIZE) -> list[dict[str, Any]]:
    return list(iter_customers(pool_size))


def iter_orders(
    customer_ids: list[int], pool_size: int
) -> Iterator[dict[str, Any]]:
    if not customer_ids:
        raise SeedError("orders require at least one customer")
    rng = random.Random(FIXED_SEED + 1)
    # These are the values already present in the demo schema and benchmark data.
    statuses = ("paid", "pending", "shipped", "cancelled", "refunded")
    status_weights = (45, 20, 20, 10, 5)
    note_values = (
        "priority customer",
        "gift order",
        "manual review",
        "delivery instructions available",
    )
    for index in range(pool_size):
        notes = None if rng.random() < 0.8 else rng.choice(note_values)
        yield {
            "id": 20_001 + index,
            "customer_id": rng.choice(customer_ids),
            "status": rng.choices(statuses, weights=status_weights, k=1)[0],
            "total": Decimal(rng.randint(1_000, 50_000)) / Decimal(100),
            "notes": notes,
            "created_at": _START + timedelta(minutes=index * 53),
        }


def generate_orders(
    customer_ids: list[int], pool_size: int = DEFAULT_BATCH_SIZE
) -> list[dict[str, Any]]:
    return list(iter_orders(customer_ids, pool_size))


def iter_profiles(pool_size: int) -> Iterator[dict[str, Any]]:
    rng = random.Random(FIXED_SEED + 2)
    languages = ("en", "it", "fr", "es")
    language_weights = (40, 30, 20, 10)
    roles = ("customer", "analyst", "editor", "support", "admin")
    for index in range(pool_size):
        newsletter_roll = rng.random()
        preferences: dict[str, Any] = {
            "language": rng.choices(languages, weights=language_weights, k=1)[0]
        }
        if newsletter_roll < 0.45:
            preferences["newsletter"] = True
        elif newsletter_roll < 0.80:
            preferences["newsletter"] = False
        yield {
            "_id": f"queryx-seed-profile-{index + 1:04d}",
            "email": f"queryx.seed.profile.{index + 1:04d}@example.test",
            "preferences": preferences,
            "roles": sorted(rng.sample(roles, k=rng.randint(1, 3))),
        }


def generate_profiles(pool_size: int = DEFAULT_BATCH_SIZE) -> list[dict[str, Any]]:
    return list(iter_profiles(pool_size))


def iter_events(
    pool_size: int, *, profile_count: int = TARGET_PROFILES
) -> Iterator[dict[str, Any]]:
    if profile_count <= 0:
        raise SeedError("events require at least one profile")
    rng = random.Random(FIXED_SEED + 3)
    event_types = ("purchase", "page_view", "login", "logout")
    event_weights = (25, 45, 20, 10)
    devices = ("desktop", "mobile", "tablet")
    paths = ("/", "/catalog", "/products", "/cart", "/account")
    currencies = ("EUR", "USD", "GBP")
    for index in range(pool_size):
        event_type = rng.choices(event_types, weights=event_weights, k=1)[0]
        properties: dict[str, Any] = {"device": rng.choice(devices)}
        event: dict[str, Any] = {
            "_id": f"queryx-seed-event-{index + 1:05d}",
            # The demo source models users by a deterministic 1-based profile ordinal.
            "user_id": rng.randint(1, profile_count),
            "type": event_type,
            "created_at": (
                _START.replace(tzinfo=timezone.utc) + timedelta(minutes=index * 17)
            ),
            "properties": properties,
        }
        if event_type == "purchase":
            properties.update(
                {
                    "amount": rng.randint(1_000, 50_000) / 100,
                    "currency": rng.choice(currencies),
                    "path": "/checkout/complete",
                }
            )
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
        yield event


def generate_events(
    pool_size: int = DEFAULT_BATCH_SIZE, *, profile_count: int = TARGET_PROFILES
) -> list[dict[str, Any]]:
    return list(iter_events(pool_size, profile_count=profile_count))


def _ensure_not_over_target(label: str, current: int, target: int) -> None:
    if current > target:
        raise SeedError(
            f"{label} already contains {current} records, above target {target}; "
            "no records were deleted"
        )


def _ensure_mysql_indexes(connection: Any) -> None:
    required = {
        "customers": {
            "email": "idx_queryx_seed_customers_email",
        },
        "orders": {
            "customer_id": "idx_queryx_seed_orders_customer_id",
            "status": "idx_queryx_seed_orders_status",
            "created_at": "idx_queryx_seed_orders_created_at",
        },
    }
    for table, columns in required.items():
        indexed_columns = {
            str(row[4])
            for row in connection.execute(text(f"SHOW INDEX FROM `{table}`"))
        }
        for column, name in columns.items():
            if column not in indexed_columns:
                connection.execute(
                    text(f"CREATE INDEX `{name}` ON `{table}` (`{column}`)")
                )
        connection.commit()


def seed_mysql(
    settings: Settings, targets: SeedTargets | None = None
) -> dict[str, int]:
    targets = targets or load_targets()
    engine = create_engine(settings.mysql_url, pool_pre_ping=True)
    connection = engine.connect()
    try:
        if connection.scalar(text("SELECT GET_LOCK('queryx_demo_seed', 30)")) != 1:
            raise SeedError("could not acquire MySQL seed lock")
        columns = {
            str(row[0])
            for row in connection.execute(text("SHOW COLUMNS FROM orders"))
        }
        _ensure_mysql_indexes(connection)

        existing_customers = list(
            connection.execute(text("SELECT id, email FROM customers ORDER BY id"))
        )
        _ensure_not_over_target(
            "customers", len(existing_customers), targets.mysql_customers
        )
        missing_customers = targets.mysql_customers - len(existing_customers)
        customer_candidates = _selected_missing(
            iter_customers(targets.mysql_customers * 2),
            {int(row[0]) for row in existing_customers},
            missing_customers,
            key="id",
            excluded_values={str(row[1]) for row in existing_customers},
            excluded_key="email",
        )
        customer_insert = text(
            "INSERT IGNORE INTO customers (id, email, name, created_at) "
            "VALUES (:id, :email, :name, :created_at)"
        )
        for batch in _batches(customer_candidates, targets.batch_size):
            connection.execute(customer_insert, batch)
            connection.commit()

        customer_ids = [
            int(row[0])
            for row in connection.execute(text("SELECT id FROM customers ORDER BY id"))
        ]
        existing_order_ids = {
            int(row[0]) for row in connection.execute(text("SELECT id FROM orders"))
        }
        _ensure_not_over_target(
            "orders", len(existing_order_ids), targets.mysql_orders
        )
        missing_orders = targets.mysql_orders - len(existing_order_ids)
        order_candidates = _selected_missing(
            iter_orders(customer_ids, targets.mysql_orders * 2),
            existing_order_ids,
            missing_orders,
            key="id",
        )
        if "notes" in columns:
            order_insert = text(
                "INSERT IGNORE INTO orders "
                "(id, customer_id, status, total, notes, created_at) "
                "VALUES (:id, :customer_id, :status, :total, :notes, :created_at)"
            )
        else:
            order_insert = text(
                "INSERT IGNORE INTO orders "
                "(id, customer_id, status, total, created_at) "
                "VALUES (:id, :customer_id, :status, :total, :created_at)"
            )
        for batch in _batches(order_candidates, targets.batch_size):
            connection.execute(order_insert, batch)
            connection.commit()

        counts = {
            "customers": int(connection.scalar(text("SELECT COUNT(*) FROM customers"))),
            "orders": int(connection.scalar(text("SELECT COUNT(*) FROM orders"))),
        }
        expected = {
            "customers": targets.mysql_customers,
            "orders": targets.mysql_orders,
        }
        if counts != expected:
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
    candidates: Iterable[dict[str, Any]],
    target: int,
    label: str,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    current = int(collection.count_documents({}))
    _ensure_not_over_target(label, current, target)
    existing_ids = set(collection.distinct("_id"))
    existing_emails = set(collection.distinct("email")) if label == "profiles" else None
    selected = _selected_missing(
        candidates,
        existing_ids,
        target - current,
        key="_id",
        excluded_values=existing_emails,
        excluded_key="email" if label == "profiles" else None,
    )
    for batch in _batches(selected, batch_size):
        collection.bulk_write(
            [
                UpdateOne(
                    {"_id": document["_id"]},
                    {"$setOnInsert": document},
                    upsert=True,
                )
                for document in batch
            ],
            ordered=True,
        )
    final = int(collection.count_documents({}))
    if final != target:
        raise SeedError(f"unexpected MongoDB {label} count: {final}")
    return final


def _ensure_mongodb_indexes(database: Any) -> None:
    index_specs = {
        "profiles": (
            ("email", "idx_queryx_seed_profiles_email", True),
            ("preferences.language", "idx_queryx_seed_profiles_language", False),
            ("preferences.newsletter", "idx_queryx_seed_profiles_newsletter", False),
        ),
        "events": (
            ("user_id", "idx_queryx_seed_events_user_id", False),
            ("type", "idx_queryx_seed_events_type", False),
            ("created_at", "idx_queryx_seed_events_created_at", False),
            ("properties.amount", "idx_queryx_seed_events_amount", False),
        ),
    }
    for collection_name, specs in index_specs.items():
        collection = database[collection_name]
        existing = {
            tuple(info.get("key", []))
            for info in collection.index_information().values()
        }
        for field, name, unique in specs:
            keys = ((field, ASCENDING),)
            if keys not in existing:
                collection.create_index(
                    [(field, ASCENDING)], name=name, unique=unique
                )


def seed_mongodb(
    settings: Settings, targets: SeedTargets | None = None
) -> dict[str, int]:
    targets = targets or load_targets()
    client = MongoClient(settings.mongodb_url)
    try:
        database = client[settings.mongodb_database]
        _ensure_mongodb_indexes(database)
        return {
            "profiles": _seed_mongo_collection(
                database["profiles"],
                iter_profiles(targets.mongodb_profiles * 2),
                targets.mongodb_profiles,
                "profiles",
                batch_size=targets.batch_size,
            ),
            "events": _seed_mongo_collection(
                database["events"],
                iter_events(
                    targets.mongodb_events * 2,
                    profile_count=targets.mongodb_profiles,
                ),
                targets.mongodb_events,
                "events",
                batch_size=targets.batch_size,
            ),
        }
    finally:
        client.close()


def main() -> int:
    settings = Settings()
    targets = load_targets()
    counts = {
        "mysql": seed_mysql(settings, targets),
        "mongodb": seed_mongodb(settings, targets),
    }
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
