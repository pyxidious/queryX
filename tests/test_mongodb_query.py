from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pymongo.errors import ExecutionTimeout, ServerSelectionTimeoutError

from queryx.app.catalog.bootstrap import backfill_virtual_assets
from queryx.app.catalog.models import ScanRun, SourceScanResult
from queryx.app.catalog.service import CatalogService
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.core.config import Settings
from queryx.app.ingestion.storage import IngestionStorage
from queryx.app.llm.ollama_client import OllamaResponse, OllamaTextResponse
from queryx.app.query.executor import QueryExecutionError
from queryx.app.query.models import LogicalQueryPlan, NaturalLanguageQueryRequest
from queryx.app.query.mongodb_executor import MongoDBQueryExecutor
from queryx.app.query.natural_language import NaturalLanguageQueryService
from queryx.app.query.service import QueryService
from queryx.app.query.validation import QueryValidationError
from queryx.app.sources.registry import SourceRegistry


def _settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    return Settings(
        catalog_db_path=data / "catalog.sqlite3",
        data_raw_dir=data / "raw",
        data_staging_dir=data / "staging",
        data_normalized_dir=data / "normalized",
        duckdb_path=data / "queryx.duckdb",
        duckdb_lock_path=data / "queryx.duckdb.lock",
        mysql_enabled=False,
        mongodb_enabled=True,
        mongodb_url="mongodb://secret:secret@mongo.invalid:27017/demo_mongo",
        mongodb_host="mongo.invalid",
        mongodb_database="demo_mongo",
        mongodb_query_timeout_seconds=2,
        query_default_limit=2,
        query_max_limit=5,
    )


def _collections(*, changed: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    declared = [{
        "name": "orders",
        "indexes": [
            {"name": "_id_", "keys": [["_id", 1]], "unique": True},
            {"name": "status_1", "keys": [["status", 1]], "unique": False},
        ],
    }]
    fields = [
        {"path": "_id", "types": ["object_id"], "documents_present": 2, "presence": 1.0},
        {"path": "status", "types": ["str"], "documents_present": 2, "presence": 1.0},
        {"path": "total", "types": ["float"], "documents_present": 2, "presence": 1.0},
        {"path": "customer.name", "types": ["str"], "documents_present": 1, "presence": 0.5},
    ]
    if changed:
        fields.append(
            {"path": "notes", "types": ["str", "null"], "documents_present": 2, "presence": 1.0}
        )
    inferred = [{
        "name": "orders",
        "sample_size": 2,
        "sample_scope": "limited_documents",
        "fields": fields,
    }]
    return declared, inferred


def _save_scan(
    settings: Settings,
    declared: list[dict[str, Any]],
    inferred: list[dict[str, Any]],
    *,
    direct: bool = False,
) -> None:
    now = datetime.now(timezone.utc)
    run = ScanRun(
        started_at=now,
        finished_at=now,
        duration_ms=1,
        status="completed",
        sources_succeeded=1,
        sources_failed=0,
        results=[SourceScanResult(
            source_id=settings.mongodb_source_id,
            database_type="mongodb",
            scan_status="completed",
            started_at=now,
            finished_at=now,
            duration_ms=1,
            fingerprint="mongo-source-fingerprint",
            declared_metadata={"collections": declared},
            inferred_metadata={"collections": inferred},
        )],
    )
    if direct:
        CatalogStorage(settings.catalog_db_path).save_scan_run(run)
        return
    catalog = CatalogService(CatalogStorage(settings.catalog_db_path))
    catalog.upsert_sources(SourceRegistry(settings).list_sources())
    catalog.save_run(run)


@pytest.fixture
def mongodb_env(tmp_path: Path) -> tuple[Settings, QueryService, Any]:
    settings = _settings(tmp_path)
    declared, inferred = _collections()
    _save_scan(settings, declared, inferred)
    service = QueryService(settings)
    assets = service.mongodb_catalog.list_ready_assets()
    assert len(assets) == 1
    return settings, service, assets[0]


def _plan(asset: Any, **updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sources": [{"alias": "o", "asset_id": asset.asset_id}],
        "projections": [{"source_alias": "o", "field": "status", "alias": "status"}],
        "filters": [{"source_alias": "o", "field": "total", "operator": "gt", "value": 10}],
        "order_by": [{"field": "status", "direction": "asc"}],
    }
    payload.update(updates)
    return payload


def test_mongodb_collection_promotion_idempotence_schema_change_and_stale(
    mongodb_env: tuple[Settings, QueryService, Any],
) -> None:
    settings, _, asset = mongodb_env
    storage = IngestionStorage(settings.catalog_db_path)
    promoted = storage.get_asset(asset.asset_id)
    assert promoted is not None
    assert str(promoted.asset_kind) == "mongodb_collection"
    assert promoted.versions[0].storage_bindings == []
    metadata = promoted.versions[0].technical_metadata
    assert metadata["source_id"] == settings.mongodb_source_id
    assert metadata["database"] == "demo_mongo"
    assert metadata["collection"] == "orders"
    assert metadata["indexes"] and metadata["schema_fingerprint"]
    fields = {field["name"]: field for field in metadata["fields"]}
    assert fields["customer.name"]["nullable"] is True
    original_version = promoted.versions[0].id

    declared, inferred = _collections()
    _save_scan(settings, declared, inferred)
    unchanged = storage.get_asset(asset.asset_id)
    assert unchanged is not None
    assert [version.id for version in unchanged.versions] == [original_version]

    changed_declared, changed_inferred = _collections(changed=True)
    _save_scan(settings, changed_declared, changed_inferred)
    changed = storage.get_asset(asset.asset_id)
    assert changed is not None and len(changed.versions) == 2
    assert changed.versions[0].status == "ready" and changed.versions[1].status == "stale"

    _save_scan(settings, [], [])
    stale = storage.get_asset(asset.asset_id)
    assert stale is not None and all(version.status == "stale" for version in stale.versions)
    with pytest.raises(QueryValidationError) as captured:
        QueryService(settings).validate(_plan(asset))
    assert captured.value.code == "mongodb_source_not_ready"


def test_mongodb_historical_scan_backfill_is_idempotent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    declared, inferred = _collections()
    _save_scan(settings, declared, inferred, direct=True)
    assert IngestionStorage(settings.catalog_db_path).list_assets() == []
    backfill_virtual_assets(settings)
    first = IngestionStorage(settings.catalog_db_path).list_assets()
    backfill_virtual_assets(settings)
    second = IngestionStorage(settings.catalog_db_path).list_assets()
    assert len(first) == len(second) == 1
    assert first[0].id == second[0].id
    assert len(second[0].versions) == 1


def test_mongodb_projection_filter_order_limit_and_nested_observation(
    mongodb_env: tuple[Settings, QueryService, Any],
) -> None:
    settings, service, asset = mongodb_env
    validated = service.validator.validate(LogicalQueryPlan.model_validate(_plan(asset)))
    compiled = service.mongodb_compiler.compile(validated)
    assert compiled.database == "demo_mongo" and compiled.collection == "orders"
    assert compiled.pipeline == [
        {"$match": {"total": {"$gt": 10}}},
        {"$project": {"_id": 0, "status": "$status"}},
        {"$sort": {"status": 1}},
        {"$limit": settings.query_default_limit + 1},
    ]
    assert settings.mongodb_url not in json.dumps(compiled.pipeline)
    nested = _plan(
        asset,
        projections=[{"source_alias": "o", "field": "customer.name", "alias": "customer"}],
        order_by=[],
    )
    assert service.validate(nested).output_schema[0].name == "customer"
    nested["projections"][0]["field"] = "customer.address"
    with pytest.raises(QueryValidationError) as missing:
        service.validate(nested)
    assert missing.value.code == "field_not_found"


def test_mongodb_aggregation_group_by_count_distinct_and_not_in(
    mongodb_env: tuple[Settings, QueryService, Any],
) -> None:
    _, service, asset = mongodb_env
    payload = _plan(
        asset,
        projections=[{"source_alias": "o", "field": "status", "alias": "status"}],
        filters=[{
            "source_alias": "o", "field": "status", "operator": "not_in",
            "value": ["cancelled", "failed"],
        }],
        aggregations=[{
            "function": "avg", "source_alias": "o", "field": "total", "alias": "average"
        }, {
            "function": "count_distinct", "source_alias": "o", "field": "_id", "alias": "orders"
        }],
        group_by=[{"source_alias": "o", "field": "status"}],
        order_by=[{"field": "average", "direction": "desc"}],
        limit=5,
    )
    validated = service.validator.validate(LogicalQueryPlan.model_validate(payload))
    pipeline = service.mongodb_compiler.compile(validated).pipeline
    assert pipeline[0] == {"$match": {"status": {"$nin": ["cancelled", "failed"]}}}
    assert "$group" in pipeline[1] and "$project" in pipeline[2]
    assert pipeline[1]["$group"]["average"] == {"$avg": "$total"}
    assert pipeline[2]["$project"]["orders"] == {"$size": "$__distinct_1"}
    assert pipeline[-2] == {"$sort": {"average": -1}}
    assert pipeline[-1] == {"$limit": 6}


def test_mongodb_rejects_arbitrary_operators_pipeline_values_and_scope(
    mongodb_env: tuple[Settings, QueryService, Any],
) -> None:
    settings, service, asset = mongodb_env
    for payload in (
        {"pipeline": [{"$where": "evil"}]},
        _plan(asset, filters=[{
            "source_alias": "o", "field": "status", "operator": "$where", "value": "evil"
        }]),
    ):
        with pytest.raises(QueryValidationError) as invalid:
            service.validate(payload)
        assert invalid.value.code == "invalid_logical_query_plan"
    with pytest.raises(QueryValidationError) as value:
        service.validate(_plan(asset, filters=[{
            "source_alias": "o", "field": "status", "operator": "eq",
            "value": {"$ne": "paid"},
        }]))
    assert value.value.code == "invalid_mongodb_filter_value"
    with pytest.raises(QueryValidationError) as between:
        service.validate(_plan(asset, filters=[{
            "source_alias": "o", "field": "total", "operator": "between", "value": [1, 2]
        }]))
    assert between.value.code == "mongodb_operator_not_supported"
    with pytest.raises(QueryValidationError) as maximum:
        service.validate(_plan(asset, limit=settings.query_max_limit + 1))
    assert maximum.value.code == "query_limit_exceeded"


def test_mongodb_routing_and_audit_do_not_store_rows_or_filter_values(
    mongodb_env: tuple[Settings, QueryService, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, service, asset = mongodb_env
    monkeypatch.setattr(
        service.mongodb_executor,
        "execute",
        lambda compiled: (["status"], [["paid"]], False, 2.5),
    )
    result = service.execute(_plan(asset))
    assert result.rows == [["paid"]]
    with sqlite3.connect(settings.catalog_db_path) as connection:
        backend, source_ids, plan_json = connection.execute(
            "SELECT backend, source_ids_json, normalized_plan_json FROM query_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert backend == "mongodb"
    assert json.loads(source_ids) == [settings.mongodb_source_id]
    assert "10" not in plan_json and "<redacted>" in plan_json
    assert "paid" not in plan_json and "rows" not in json.loads(plan_json)


class _Collection:
    def __init__(self, result: Any) -> None:
        self.result = result

    def aggregate(self, pipeline: Any, maxTimeMS: int) -> Any:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Database:
    def __init__(self, collection: _Collection) -> None:
        self.collection = collection

    def __getitem__(self, name: str) -> _Collection:
        return self.collection


class _Client:
    def __init__(self, result: Any) -> None:
        self.database = _Database(_Collection(result))
        self.closed = False

    def __getitem__(self, name: str) -> _Database:
        return self.database

    def close(self) -> None:
        self.closed = True


def test_mongodb_executor_connection_timeout_and_close(
    mongodb_env: tuple[Settings, QueryService, Any],
) -> None:
    _, service, asset = mongodb_env
    validated = service.validator.validate(LogicalQueryPlan.model_validate(_plan(asset)))
    compiled = service.mongodb_compiler.compile(validated)
    connection_client = _Client(ServerSelectionTimeoutError("offline"))
    with pytest.raises(QueryExecutionError) as connection:
        MongoDBQueryExecutor(
            "mongodb://unused", 1, client_factory=lambda: connection_client
        ).execute(compiled)
    assert connection.value.code == "mongodb_connection_failed"
    assert connection_client.closed is True
    timeout_client = _Client(ExecutionTimeout("timeout"))
    with pytest.raises(QueryExecutionError) as timeout:
        MongoDBQueryExecutor(
            "mongodb://unused", 1, client_factory=lambda: timeout_client
        ).execute(compiled)
    assert timeout.value.code == "query_timeout" and timeout_client.closed is True


class _NaturalClient:
    def __init__(self, plan: dict[str, Any]) -> None:
        self.plan = plan
        self.planning_messages: list[dict[str, str]] = []

    def chat_text(
        self, messages: list[dict[str, str]], json_schema: dict[str, Any] | None = None,
    ) -> OllamaTextResponse:
        return OllamaTextResponse(
            json.dumps({"classification": "answerable", "reason": "Catalog data is sufficient."}),
            {},
        )

    def chat_json(
        self, messages: list[dict[str, str]], json_schema: dict[str, Any]
    ) -> OllamaResponse:
        self.planning_messages = messages
        return OllamaResponse(self.plan, {})


def test_natural_language_context_selects_only_mongodb_asset(
    mongodb_env: tuple[Settings, QueryService, Any],
) -> None:
    settings, _, asset = mongodb_env
    plan = _plan(asset, filters=[], order_by=[])
    client = _NaturalClient(plan)
    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question="Mostra gli ordini MongoDB per stato")
    )
    payload = json.loads(client.planning_messages[1]["content"])
    blocks = payload["catalog"]["assets"]
    assert response.normalized_plan is not None
    assert response.normalized_plan.sources[0].asset_id == asset.asset_id
    assert blocks and all(block["backend"] == "mongodb" for block in blocks)
    assert blocks[0]["source_id"] == settings.mongodb_source_id
    assert {field["name"] for field in blocks[0]["fields"]} >= {
        "_id", "status", "total", "customer.name"
    }
    prompt = json.dumps(payload)
    assert settings.mongodb_url not in prompt
    assert settings.mongodb_database not in prompt
