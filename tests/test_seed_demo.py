from __future__ import annotations

from copy import deepcopy

from queryx.tools.seed_demo import (
    FIXED_SEED,
    _seed_mongo_collection,
    generate_events,
    generate_orders,
    generate_profiles,
)


class _MemoryCollection:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = {document["_id"]: deepcopy(document) for document in documents}

    def count_documents(self, _filter: dict[str, object]) -> int:
        return len(self.documents)

    def distinct(self, field: str) -> list[object]:
        return [
            document[field]
            for document in self.documents.values()
            if field in document
        ]

    def update_one(
        self,
        selector: dict[str, object],
        update: dict[str, dict[str, object]],
        *,
        upsert: bool,
    ) -> None:
        assert upsert is True
        identifier = selector["_id"]
        if identifier not in self.documents:
            self.documents[identifier] = deepcopy(update["$setOnInsert"])


def test_demo_generation_is_deterministic_and_semantically_consistent() -> None:
    assert FIXED_SEED == 20260723
    assert generate_orders([1, 2], 40) == generate_orders([1, 2], 40)
    assert generate_profiles(40) == generate_profiles(40)
    assert generate_events(80) == generate_events(80)

    orders = generate_orders([1, 2], 100)
    assert {order["status"] for order in orders} == {
        "paid", "pending", "shipped", "cancelled", "refunded"
    }
    assert all(10 <= order["total"] <= 500 for order in orders)
    assert sum(order["notes"] is None for order in orders) >= 70

    profiles = generate_profiles(100)
    assert {profile["preferences"]["language"] for profile in profiles} == {
        "en", "it", "fr", "es"
    }
    newsletter_states = {
        profile["preferences"].get("newsletter", "absent") for profile in profiles
    }
    assert newsletter_states == {True, False, "absent"}
    assert all(profile["roles"] for profile in profiles)

    events = generate_events(200)
    assert {event["type"] for event in events} == {
        "purchase", "page_view", "login", "logout"
    }
    assert all(
        ("amount" in event["properties"]) == (event["type"] == "purchase")
        for event in events
    )
    assert all("items" in event for event in events if event["type"] == "purchase")


def test_mongodb_seed_is_idempotent_and_preserves_existing_documents() -> None:
    legacy = {"_id": "legacy-profile", "email": "legacy@example.test"}
    collection = _MemoryCollection([legacy])
    candidates = generate_profiles(20)

    assert _seed_mongo_collection(collection, candidates, 10, "profiles") == 10
    first_seed = deepcopy(collection.documents)
    assert collection.documents["legacy-profile"] == legacy

    assert _seed_mongo_collection(collection, candidates, 10, "profiles") == 10
    assert collection.documents == first_seed
