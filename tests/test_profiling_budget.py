from __future__ import annotations

from queryx.app.catalog.models import ProfilingBudget
from queryx.app.connectors.mongodb import MongoDBConnector


class _FakeCollection:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    def find(self, query: dict, limit: int) -> list[dict]:
        return self.documents[:limit]


def _connector(budget: ProfilingBudget) -> MongoDBConnector:
    connector = MongoDBConnector.__new__(MongoDBConnector)
    connector.sample_size = 100
    connector.profiling_budget = budget
    return connector


def test_profiling_budget_does_not_exceed_per_entity_or_total_limits() -> None:
    connector = _connector(
        ProfilingBudget(
            enabled=True,
            max_records_per_entity=3,
            max_total_records=5,
            max_entities=10,
            max_seconds_per_entity=10,
        )
    )
    metrics = {
        "enabled": True,
        "entities": [],
        "total_records_sampled": 0,
        "entities_not_profiled": [],
        "limits_reached": [],
        "timeouts": [],
    }

    first = connector._sample_documents(_FakeCollection([{"a": index} for index in range(10)]), "a", metrics)
    second = connector._sample_documents(_FakeCollection([{"b": index} for index in range(10)]), "b", metrics)

    assert len(first) == 3
    assert len(second) == 2
    assert metrics["total_records_sampled"] == 5


def test_profiling_can_be_disabled() -> None:
    connector = _connector(ProfilingBudget(enabled=False))
    metrics = {
        "enabled": False,
        "entities": [],
        "total_records_sampled": 0,
        "entities_not_profiled": [],
        "limits_reached": [],
        "timeouts": [],
    }

    documents = connector._sample_documents(_FakeCollection([{"a": 1}]), "events", metrics)

    assert documents == []
    assert metrics["entities_not_profiled"] == ["events"]
