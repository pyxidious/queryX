from __future__ import annotations

from copy import deepcopy

import queryx.tools.seed_demo as seed_demo
from queryx.tools.seed_demo import (
    DEFAULT_BATCH_SIZE,
    FIXED_SEED,
    TARGET_CUSTOMERS,
    TARGET_EVENTS,
    TARGET_ORDERS,
    TARGET_PROFILES,
    SeedTargets,
    _batches,
    _seed_mongo_collection,
    generate_customers,
    generate_events,
    generate_orders,
    generate_profiles,
    load_targets,
)


class _MemoryCollection:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = {document["_id"]: deepcopy(document) for document in documents}
        self.batch_sizes: list[int] = []

    def count_documents(self, _filter: dict[str, object]) -> int:
        return len(self.documents)

    def distinct(self, field: str) -> list[object]:
        return [
            document[field]
            for document in self.documents.values()
            if field in document
        ]

    def bulk_write(self, operations: list[object], *, ordered: bool) -> None:
        assert ordered is True
        self.batch_sizes.append(len(operations))
        for operation in operations:
            assert operation._upsert is True
            identifier = operation._filter["_id"]
            if identifier not in self.documents:
                self.documents[identifier] = deepcopy(
                    operation._doc["$setOnInsert"]
                )


def test_demo_generation_is_deterministic_and_semantically_consistent() -> None:
    assert FIXED_SEED == 20260723
    assert (TARGET_CUSTOMERS, TARGET_ORDERS, TARGET_PROFILES, TARGET_EVENTS) == (
        10_000,
        100_000,
        10_000,
        100_000,
    )
    assert DEFAULT_BATCH_SIZE == 2_000
    assert generate_customers(20) == generate_customers(20)
    assert generate_orders([1, 2], 40) == generate_orders([1, 2], 40)
    assert generate_profiles(40) == generate_profiles(40)
    assert generate_events(80, profile_count=20) == generate_events(
        80, profile_count=20
    )

    orders = generate_orders([1, 2], 100)
    assert {order["status"] for order in orders} == {
        "paid", "pending", "shipped", "cancelled", "refunded"
    }
    assert all(10 <= order["total"] <= 500 for order in orders)
    assert all(order["customer_id"] in {1, 2} for order in orders)
    assert sum(order["notes"] is None for order in orders) >= 70
    assert len({customer["id"] for customer in generate_customers(100)}) == 100
    assert len({customer["email"] for customer in generate_customers(100)}) == 100
    assert len({order["id"] for order in orders}) == 100

    profiles = generate_profiles(100)
    assert {profile["preferences"]["language"] for profile in profiles} == {
        "en", "it", "fr", "es"
    }
    newsletter_states = {
        profile["preferences"].get("newsletter", "absent") for profile in profiles
    }
    assert newsletter_states == {True, False, "absent"}
    assert all(profile["roles"] for profile in profiles)

    events = generate_events(200, profile_count=100)
    assert {event["type"] for event in events} == {
        "purchase", "page_view", "login", "logout"
    }
    assert all(
        ("amount" in event["properties"]) == (event["type"] == "purchase")
        for event in events
    )
    assert all(1 <= event["user_id"] <= 100 for event in events)
    assert all(
        event["properties"]["amount"] > 0
        for event in events
        if event["type"] == "purchase"
    )
    assert all("items" in event for event in events if event["type"] == "purchase")
    assert [len(batch) for batch in _batches(generate_customers(7), 3)] == [3, 3, 1]


def test_mongodb_seed_is_idempotent_and_preserves_existing_documents() -> None:
    legacy = {"_id": "legacy-profile", "email": "legacy@example.test"}
    collection = _MemoryCollection([legacy])
    candidates = generate_profiles(20)

    assert _seed_mongo_collection(
        collection, iter(candidates), 10, "profiles", batch_size=3
    ) == 10
    first_seed = deepcopy(collection.documents)
    assert collection.documents["legacy-profile"] == legacy
    assert collection.batch_sizes == [3, 3, 3]

    assert _seed_mongo_collection(
        collection, iter(candidates), 10, "profiles", batch_size=3
    ) == 10
    assert collection.documents == first_seed
    assert collection.batch_sizes == [3, 3, 3]


def test_targets_are_configurable_and_main_prints_compact_counts(
    monkeypatch, capsys
) -> None:
    values = {
        "QUERYX_SEED_MYSQL_CUSTOMERS": "5",
        "QUERYX_SEED_MYSQL_ORDERS": "11",
        "QUERYX_SEED_MONGODB_PROFILES": "7",
        "QUERYX_SEED_MONGODB_EVENTS": "13",
        "QUERYX_SEED_BATCH_SIZE": "4",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    assert load_targets() == SeedTargets(5, 11, 7, 13, 4)

    targets = SeedTargets()
    monkeypatch.setattr(seed_demo, "Settings", lambda: object())
    monkeypatch.setattr(seed_demo, "load_targets", lambda: targets)
    monkeypatch.setattr(
        seed_demo,
        "seed_mysql",
        lambda settings, configured: {"customers": 10_000, "orders": 100_000},
    )
    monkeypatch.setattr(
        seed_demo,
        "seed_mongodb",
        lambda settings, configured: {"profiles": 10_000, "events": 100_000},
    )

    assert seed_demo.main() == 0
    assert capsys.readouterr().out.strip() == (
        '{"mongodb": {"events": 100000, "profiles": 10000}, '
        '"mysql": {"customers": 10000, "orders": 100000}}'
    )
