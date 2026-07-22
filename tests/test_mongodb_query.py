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


def _profiles_events_collections() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    schemas = {
        "profiles": [
            ("_id", "object_id"),
            ("email", "str"),
            ("preferences.language", "str"),
            ("preferences.newsletter", "bool"),
            ("roles", "array"),
        ],
        "events": [
            ("_id", "object_id"),
            ("created_at", "datetime"),
            ("properties.amount", "float"),
            ("properties.currency", "str"),
            ("properties.device", "str"),
            ("properties.path", "str"),
            ("type", "str"),
            ("user_id", "int"),
            ("tags", "array"),
            ("items", "array"),
        ],
    }
    declared = [
        {"name": name, "indexes": [{"name": "_id_", "keys": [["_id", 1]]}]}
        for name in schemas
    ]
    inferred = [
        {
            "name": name,
            "sample_size": 2,
            "sample_scope": "limited_documents",
            "fields": [
                {
                    "path": path,
                    "types": [data_type],
                    "documents_present": 2,
                    "presence": 1.0,
                }
                for path, data_type in fields
            ],
        }
        for name, fields in schemas.items()
    ]
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


@pytest.fixture
def mongodb_profiles_env(
    tmp_path: Path,
) -> tuple[Settings, QueryService, dict[str, Any]]:
    settings = _settings(tmp_path)
    declared, inferred = _profiles_events_collections()
    _save_scan(settings, declared, inferred)
    service = QueryService(settings)
    assets = {
        asset.name: asset for asset in service.mongodb_catalog.list_ready_assets()
    }
    assert set(assets) == {"events", "profiles"}
    return settings, service, assets


def _plan(asset: Any, **updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sources": [{"alias": "o", "asset_id": asset.asset_id}],
        "projections": [{"source_alias": "o", "field": "status", "alias": "status"}],
        "filters": [{"source_alias": "o", "field": "total", "operator": "gt", "value": 10}],
        "order_by": [{"field": "status", "direction": "asc"}],
    }
    payload.update(updates)
    return payload


def _newsletter_plan(asset: Any, value: bool) -> dict[str, Any]:
    return {
        "sources": [{"alias": "profiles", "asset_id": asset.asset_id}],
        "projections": [
            {"source_alias": "profiles", "field": "email"},
            {
                "source_alias": "profiles",
                "field": "preferences.newsletter",
            },
            {
                "source_alias": "profiles",
                "field": "preferences.language",
            },
        ],
        "filters": [{
            "source_alias": "profiles",
            "field": "preferences.newsletter",
            "operator": "eq",
            "value": value,
        }],
        "aggregations": [],
        "group_by": [],
        "order_by": [],
    }


def _profiles_count_plan(asset: Any) -> dict[str, Any]:
    return {
        "sources": [{"alias": "profiles", "asset_id": asset.asset_id}],
        "projections": [],
        "filters": [],
        "aggregations": [{
            "function": "count",
            "source_alias": "profiles",
            "field": "_id",
            "alias": "profiles",
        }],
        "group_by": [],
        "order_by": [],
    }


def _language_plan(asset: Any, value: str) -> dict[str, Any]:
    return {
        "sources": [{"alias": "profiles", "asset_id": asset.asset_id}],
        "projections": [
            {"source_alias": "profiles", "field": "email"},
            {"source_alias": "profiles", "field": "preferences.language"},
        ],
        "filters": [{
            "source_alias": "profiles",
            "field": "preferences.language",
            "operator": "eq",
            "value": value,
        }],
        "aggregations": [],
        "group_by": [],
        "order_by": [],
    }


def _events_plan(asset: Any) -> dict[str, Any]:
    return {
        "sources": [{"alias": "events", "asset_id": asset.asset_id}],
        "projections": [
            {"source_alias": "events", "field": "type"},
            {"source_alias": "events", "field": "created_at"},
        ],
        "filters": [],
        "aggregations": [],
        "group_by": [],
        "order_by": [],
    }


def _events_count_plan(asset: Any, *, grouped: bool = False) -> dict[str, Any]:
    return {
        "sources": [{"alias": "events", "asset_id": asset.asset_id}],
        "projections": (
            [{"source_alias": "events", "field": "type"}] if grouped else []
        ),
        "filters": [],
        "aggregations": [{
            "function": "count",
            "source_alias": "events",
            "field": "_id",
            "alias": "events",
        }],
        "group_by": (
            [{"source_alias": "events", "field": "type"}] if grouped else []
        ),
        "order_by": [],
    }


def _events_amount_metric_plan(
    asset: Any, function: str, alias: str
) -> dict[str, Any]:
    return {
        "sources": [{"alias": "events", "asset_id": asset.asset_id}],
        "projections": [],
        "filters": [],
        "aggregations": [{
            "function": function,
            "source_alias": "events",
            "field": "properties.amount",
            "alias": alias,
        }],
        "group_by": [],
        "order_by": [],
    }


def _events_user_count_plan(asset: Any, value: int | str) -> dict[str, Any]:
    plan = _events_count_plan(asset)
    plan["filters"] = [{
        "source_alias": "events",
        "field": "user_id",
        "operator": "eq",
        "value": value,
    }]
    return plan


def _events_amount_rows_plan(asset: Any) -> dict[str, Any]:
    return {
        "sources": [{"alias": "events", "asset_id": asset.asset_id}],
        "projections": [
            {"source_alias": "events", "field": "type"},
            {"source_alias": "events", "field": "user_id"},
            {"source_alias": "events", "field": "properties.amount"},
        ],
        "filters": [{
            "source_alias": "events",
            "field": "properties.amount",
            "operator": "gt",
            "value": 100,
        }],
        "aggregations": [],
        "group_by": [],
        "order_by": [],
    }


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


def test_mongodb_declared_source_alias_is_used_exactly(
    mongodb_env: tuple[Settings, QueryService, Any],
) -> None:
    _, service, asset = mongodb_env
    payload = _plan(
        asset,
        sources=[{"alias": "profiles", "asset_id": asset.asset_id}],
        projections=[{
            "source_alias": "profiles", "field": "status", "alias": "status"
        }],
        filters=[],
        order_by=[],
    )

    validated = service.validator.validate(LogicalQueryPlan.model_validate(payload))
    assert set(validated.sources) == {"profiles"}
    assert service.mongodb_compiler.compile(validated).pipeline[0] == {
        "$project": {"_id": 0, "status": "$status"}
    }


@pytest.mark.parametrize(
    ("location", "expected_location"),
    [
        ("projection", "projection"),
        ("filter", "filter"),
        ("aggregation", "aggregation"),
        ("group_by", "group_by"),
        ("join", "join.right_alias"),
    ],
)
def test_mongodb_rejects_unknown_source_alias_without_single_source_fallback(
    mongodb_env: tuple[Settings, QueryService, Any],
    monkeypatch: pytest.MonkeyPatch,
    location: str,
    expected_location: str,
) -> None:
    _, service, asset = mongodb_env
    payload = _plan(asset, filters=[], order_by=[])
    if location == "projection":
        payload["projections"][0]["source_alias"] = "profiles"
    elif location == "filter":
        payload["filters"] = [{
            "source_alias": "profiles",
            "field": "status",
            "operator": "eq",
            "value": "paid",
        }]
    elif location == "aggregation":
        payload["projections"] = []
        payload["aggregations"] = [{
            "function": "count",
            "source_alias": "profiles",
            "field": "_id",
            "alias": "profiles",
        }]
    elif location == "group_by":
        payload["group_by"] = [{"source_alias": "profiles", "field": "status"}]
    else:
        payload["joins"] = [{
            "relationship_id": "unused",
            "left_alias": "o",
            "right_alias": "profiles",
        }]
    executed = False

    def unexpected_execute(compiled: Any) -> Any:
        nonlocal executed
        executed = True
        return (["status"], [], False, 1.0)

    monkeypatch.setattr(service.mongodb_executor, "execute", unexpected_execute)
    with pytest.raises(QueryValidationError) as captured:
        service.execute(payload)

    assert captured.value.code == "source_alias_not_found"
    assert captured.value.details["source_alias"] == "profiles"
    assert captured.value.details["location"] == expected_location
    assert executed is False


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

    multi_source = _plan(
        asset,
        sources=[
            {"alias": "a", "asset_id": asset.asset_id},
            {"alias": "b", "asset_id": asset.asset_id},
        ],
        projections=[{"source_alias": "a", "field": "status"}],
        filters=[],
        order_by=[],
    )
    with pytest.raises(QueryValidationError) as multiple:
        service.validate(multi_source)
    assert multiple.value.code == "mongodb_multi_source_not_supported"
    joined = _plan(
        asset,
        joins=[{
            "relationship_id": "forbidden",
            "left_alias": "o",
            "right_alias": "o",
        }],
    )
    with pytest.raises(QueryValidationError) as join:
        service.validate(joined)
    assert join.value.code == "mongodb_joins_not_supported"
    transformed = _plan(asset)
    transformed["projections"][0]["transform"] = "date_trunc_month"
    with pytest.raises(QueryValidationError) as transform:
        service.validate(transformed)
    assert transform.value.code == "mongodb_transform_not_supported"

    ne_plan = _plan(asset, filters=[{
        "source_alias": "o", "field": "status", "operator": "ne", "value": "cancelled"
    }])
    ne_validated = service.validator.validate(LogicalQueryPlan.model_validate(ne_plan))
    assert service.mongodb_compiler.compile(ne_validated).pipeline[0] == {
        "$match": {"status": {"$ne": "cancelled"}}
    }


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


@pytest.mark.parametrize(
    ("payload_factory", "nested_field", "output_name", "output_value"),
    [
        (_language_plan, "preferences.language", "language", "en"),
        (_newsletter_plan, "preferences.newsletter", "newsletter", True),
    ],
)
def test_mongodb_nested_projection_uses_flat_implicit_alias_and_real_value(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
    payload_factory: Any,
    nested_field: str,
    output_name: str,
    output_value: Any,
) -> None:
    settings, service, assets = mongodb_profiles_env
    payload = payload_factory(assets["profiles"], output_value)
    if nested_field == "preferences.newsletter":
        payload["projections"] = [{
            "source_alias": "profiles", "field": nested_field
        }]
    validated = service.validator.validate(LogicalQueryPlan.model_validate(payload))
    compiled = service.mongodb_compiler.compile(validated)

    assert compiled.pipeline[0] == {"$match": {nested_field: output_value}}
    expected_project = {"_id": 0}
    for projection in payload["projections"]:
        expected_project[projection["field"].rsplit(".", 1)[-1]] = (
            f"${projection['field']}"
        )
    assert compiled.pipeline[1] == {"$project": expected_project}
    assert compiled.pipeline[-1] == {"$limit": settings.query_default_limit + 1}
    assert output_name in [field.name for field in compiled.output_schema]

    document = {
        projection["field"].rsplit(".", 1)[-1]: (
            output_value if projection["field"] == nested_field else "a@example.test"
        )
        for projection in payload["projections"]
    }
    columns, rows, truncated, _ = MongoDBQueryExecutor(
        "mongodb://unused", 1, client_factory=lambda: _Client([document])
    ).execute(compiled)
    assert rows[0][columns.index(output_name)] == output_value
    assert truncated is False


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


class _SequencedNaturalClient(_NaturalClient):
    def __init__(
        self,
        *plans: dict[str, Any],
        explanation: str = "La query ha restituito il risultato richiesto.",
    ) -> None:
        super().__init__(plans[0])
        self.plans = list(plans)
        self.explanation = explanation
        self.planning_calls: list[list[dict[str, str]]] = []
        self.explanation_calls: list[list[dict[str, str]]] = []

    def chat_text(
        self, messages: list[dict[str, str]], json_schema: dict[str, Any] | None = None,
    ) -> OllamaTextResponse:
        if json_schema is None:
            self.explanation_calls.append(messages)
            return OllamaTextResponse(self.explanation, {})
        return super().chat_text(messages, json_schema)

    def chat_json(
        self, messages: list[dict[str, str]], json_schema: dict[str, Any]
    ) -> OllamaResponse:
        self.planning_calls.append(messages)
        return OllamaResponse(self.plans.pop(0), {})


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


def test_natural_language_retries_mongodb_source_alias_typo_once(
    mongodb_env: tuple[Settings, QueryService, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, service, asset = mongodb_env
    invalid = _plan(
        asset,
        sources=[{"alias": "profqiles", "asset_id": asset.asset_id}],
        projections=[{
            "source_alias": "profiles", "field": "status", "alias": "status"
        }],
        filters=[],
        order_by=[],
    )
    corrected = _plan(
        asset,
        sources=[{"alias": "profiles", "asset_id": asset.asset_id}],
        projections=[{
            "source_alias": "profiles", "field": "status", "alias": "status"
        }],
        filters=[],
        order_by=[],
    )
    client = _SequencedNaturalClient(invalid, corrected)
    executions = 0

    def execute(compiled: Any) -> Any:
        nonlocal executions
        executions += 1
        return (["status"], [["active"]], False, 1.0)

    monkeypatch.setattr(service.mongodb_executor, "execute", execute)
    response = NaturalLanguageQueryService(
        settings, client=client, query_service=service  # type: ignore[arg-type]
    ).translate(NaturalLanguageQueryRequest(
        question="Mostra gli ordini MongoDB", execute=True
    ))

    assert response.normalized_plan is not None
    assert response.normalized_plan.sources[0].alias == "profiles"
    assert response.normalized_plan.projections[0].source_alias == "profiles"
    assert executions == 1
    assert len(client.planning_calls) == 2
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "source_alias_not_found"
    assert feedback["declared_source_aliases"] == ["profqiles"]
    assert feedback["referenced_source_aliases"] == ["profiles"]


def test_natural_language_row_request_removes_unrequested_order_by(
    mongodb_env: tuple[Settings, QueryService, Any],
) -> None:
    settings, _, asset = mongodb_env
    invalid = _plan(asset, filters=[])
    corrected = _plan(asset, filters=[], order_by=[])
    client = _SequencedNaturalClient(invalid, corrected)

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question="Mostra gli ordini MongoDB")
    )

    assert response.normalized_plan is not None
    assert response.normalized_plan.order_by == []
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "row_order_by_mismatch"


def test_natural_language_mongodb_active_newsletter_uses_exact_nested_field(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mongodb_profiles_env
    invalid = _newsletter_plan(assets["profiles"], True)
    invalid["filters"] = [
        {
            "source_alias": "profiles",
            "field": "newsletter",
            "operator": "is_not_null",
        },
        {
            "source_alias": "profiles",
            "field": "newsletter",
            "operator": "eq",
            "value": True,
        },
    ]
    corrected = _newsletter_plan(assets["profiles"], True)
    client = _SequencedNaturalClient(invalid, corrected)

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Mostra i profili MongoDB con newsletter attiva"
        )
    )

    plan = response.normalized_plan
    assert plan is not None and len(plan.filters) == 1
    query_filter = plan.filters[0]
    assert (
        query_filter.field,
        query_filter.operator.value,
        query_filter.value,
    ) == ("preferences.newsletter", "eq", True)
    assert [item.field for item in plan.projections] == [
        "email", "preferences.newsletter", "preferences.language"
    ]
    assert plan.aggregations == [] and plan.group_by == [] and plan.order_by == []
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "field_not_found"
    assert "preferences.newsletter" in feedback["selected_assets"][0]["valid_fields"]


def test_natural_language_mongodb_newsletter_filter_is_not_duplicated(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mongodb_profiles_env
    invalid = _newsletter_plan(assets["profiles"], True)
    invalid["filters"].insert(0, {
        "source_alias": "profiles",
        "field": "preferences.newsletter",
        "operator": "is_not_null",
    })
    corrected = _newsletter_plan(assets["profiles"], True)
    client = _SequencedNaturalClient(invalid, corrected)

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Mostra i profili MongoDB con newsletter abilitata"
        )
    )

    assert response.normalized_plan is not None
    assert len(response.normalized_plan.filters) == 1
    assert response.normalized_plan.filters[0].operator.value == "eq"
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "mongodb_filter_mismatch"


def test_natural_language_mongodb_inactive_newsletter_maps_to_false(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mongodb_profiles_env
    client = _SequencedNaturalClient(_newsletter_plan(assets["profiles"], False))

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Mostra i profili MongoDB con newsletter disattiva"
        )
    )

    assert response.normalized_plan is not None
    assert response.normalized_plan.filters[0].value is False
    assert len(client.planning_calls) == 1


def test_mongodb_unobserved_short_newsletter_field_is_rejected(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    _, service, assets = mongodb_profiles_env
    invalid = _newsletter_plan(assets["profiles"], True)
    invalid["filters"][0]["field"] = "newsletter"

    with pytest.raises(QueryValidationError) as captured:
        service.validate(invalid)

    assert captured.value.code == "field_not_found"
    assert captured.value.details["field"] == "newsletter"


def test_natural_language_mongodb_profiles_count_discards_events_fragments(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, service, assets = mongodb_profiles_env
    invalid = _events_plan(assets["events"])
    invalid["filters"] = [
        {"source_alias": "events", "field": "type", "operator": "eq", "value": "order"},
        {
            "source_alias": "events",
            "field": "properties.device",
            "operator": "eq",
            "value": "mobile",
        },
    ]
    invalid["order_by"] = [{"field": "created_at", "direction": "desc"}]
    corrected = _profiles_count_plan(assets["profiles"])
    client = _SequencedNaturalClient(invalid, corrected)
    executions = 0

    def execute(compiled: Any) -> Any:
        nonlocal executions
        executions += 1
        return (["profiles"], [[2]], False, 1.0)

    monkeypatch.setattr(service.mongodb_executor, "execute", execute)
    response = NaturalLanguageQueryService(
        settings, client=client, query_service=service  # type: ignore[arg-type]
    ).translate(NaturalLanguageQueryRequest(
        question="Quanti profili ci sono nel database MongoDB?", execute=True
    ))

    plan = response.normalized_plan
    assert plan is not None and plan.sources[0].asset_id == assets["profiles"].asset_id
    assert plan.projections == [] and plan.filters == []
    assert plan.group_by == [] and plan.order_by == []
    assert [(item.function.value, item.field, item.alias) for item in plan.aggregations] == [
        ("count", "_id", "profiles")
    ]
    assert response.result is not None and response.result.rows == [[2]]
    assert executions == 1
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "semantic_asset_mismatch"

    catalog = json.loads(client.planning_calls[0][1]["content"])["catalog"]
    assert [asset["logical_name"] for asset in catalog["assets"]] == ["profiles"]


def test_natural_language_mongodb_count_rejects_unrequested_row_shape(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mongodb_profiles_env
    invalid = _profiles_count_plan(assets["profiles"])
    invalid["filters"] = [{
        "source_alias": "profiles",
        "field": "preferences.newsletter",
        "operator": "eq",
        "value": True,
    }]
    invalid["order_by"] = [{"field": "profiles", "direction": "desc"}]
    corrected = _profiles_count_plan(assets["profiles"])
    client = _SequencedNaturalClient(invalid, corrected)

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Quanti profili ci sono nel database MongoDB?"
        )
    )

    assert response.normalized_plan is not None
    assert response.normalized_plan.projections == []
    assert response.normalized_plan.filters == []
    assert response.normalized_plan.order_by == []
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "mongodb_count_intent_mismatch"


def test_natural_language_mongodb_requests_are_isolated(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, service, assets = mongodb_profiles_env
    client = _SequencedNaturalClient(
        _events_plan(assets["events"]),
        _profiles_count_plan(assets["profiles"]),
        _newsletter_plan(assets["profiles"], True),
    )
    compiled_calls: list[Any] = []

    def execute(compiled: Any) -> Any:
        compiled_calls.append(compiled)
        columns = [field.name for field in compiled.output_schema]
        if columns == ["profiles"]:
            return (columns, [[2]], False, 1.0)
        if "preferences.newsletter" in columns:
            return (columns, [["a@example.test", True, "it"]], False, 1.0)
        return (columns, [["login", "2025-01-01T00:00:00"]], False, 1.0)

    monkeypatch.setattr(service.mongodb_executor, "execute", execute)
    natural = NaturalLanguageQueryService(
        settings, client=client, query_service=service  # type: ignore[arg-type]
    )
    event_response = natural.translate(NaturalLanguageQueryRequest(
        question="Mostra gli eventi MongoDB", execute=True
    ))
    count_response = natural.translate(NaturalLanguageQueryRequest(
        question="Quanti profili ci sono nel database MongoDB?", execute=True
    ))
    newsletter_response = natural.translate(NaturalLanguageQueryRequest(
        question="Mostra i profili MongoDB con newsletter attiva", execute=True
    ))

    assert event_response.normalized_plan.sources[0].asset_id == assets["events"].asset_id
    assert count_response.normalized_plan.sources[0].asset_id == assets["profiles"].asset_id
    assert count_response.normalized_plan.filters == []
    assert count_response.normalized_plan.projections == []
    assert newsletter_response.normalized_plan.sources[0].asset_id == assets["profiles"].asset_id
    assert [item.field for item in newsletter_response.normalized_plan.filters] == [
        "preferences.newsletter"
    ]
    assert all(item.field not in {"type", "created_at"} for item in newsletter_response.normalized_plan.projections)
    assert len(client.planning_calls) == len(compiled_calls) == 3


def test_natural_language_mongodb_english_query_and_explanation_language(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, service, assets = mongodb_profiles_env
    client = _SequencedNaturalClient(
        _newsletter_plan(assets["profiles"], True),
        explanation="The matching profile has the newsletter enabled.",
    )
    monkeypatch.setattr(
        service.mongodb_executor,
        "execute",
        lambda compiled: (
            [field.name for field in compiled.output_schema],
            [["a@example.test", True, "en"]],
            False,
            1.0,
        ),
    )

    response = NaturalLanguageQueryService(
        settings, client=client, query_service=service  # type: ignore[arg-type]
    ).translate(NaturalLanguageQueryRequest(
        question="Show MongoDB profiles with newsletter enabled", execute=True
    ))

    assert response.answer == "The matching profile has the newsletter enabled."
    assert response.normalized_plan is not None
    assert response.normalized_plan.filters[0].value is True
    explanation_prompt = client.explanation_calls[0][0]["content"]
    assert "same language" in explanation_prompt


def test_natural_language_mongodb_english_language_filter_is_required_once(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, service, assets = mongodb_profiles_env
    invalid = _language_plan(assets["profiles"], "en")
    invalid["filters"] = []
    corrected = _language_plan(assets["profiles"], "en")
    client = _SequencedNaturalClient(
        invalid,
        corrected,
        explanation="I profili trovati usano la lingua inglese.",
    )
    executions = 0

    def execute(compiled: Any) -> Any:
        nonlocal executions
        executions += 1
        return (["email", "language"], [["a@example.test", "en"]], False, 1.0)

    monkeypatch.setattr(service.mongodb_executor, "execute", execute)
    response = NaturalLanguageQueryService(
        settings, client=client, query_service=service  # type: ignore[arg-type]
    ).translate(NaturalLanguageQueryRequest(
        question="Mostra i profili MongoDB con lingua inglese", execute=True
    ))

    plan = response.normalized_plan
    assert plan is not None and len(plan.filters) == 1
    assert (
        plan.filters[0].field,
        plan.filters[0].operator.value,
        plan.filters[0].value,
    ) == ("preferences.language", "eq", "en")
    assert [item.field for item in plan.projections] == [
        "email", "preferences.language"
    ]
    assert response.result is not None
    assert response.result.columns == ["email", "language"]
    assert response.result.rows == [["a@example.test", "en"]]
    assert response.answer == "I profili trovati usano la lingua inglese."
    assert executions == 1
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "missing_explicit_filter"


def test_natural_language_mongodb_italian_language_maps_to_it(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mongodb_profiles_env
    client = _SequencedNaturalClient(_language_plan(assets["profiles"], "it"))

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Mostra i profili MongoDB con lingua italiana"
        )
    )

    assert response.normalized_plan is not None
    assert response.normalized_plan.filters[0].value == "it"
    assert [field.name for field in response.output_schema] == ["email", "language"]


def test_natural_language_mongodb_events_count_per_type_requires_group_by(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mongodb_profiles_env
    scalar = _events_count_plan(assets["events"])
    grouped = _events_count_plan(assets["events"], grouped=True)
    client = _SequencedNaturalClient(scalar, grouped)

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Quanti eventi ci sono per tipo nel database MongoDB?"
        )
    )

    plan = response.normalized_plan
    assert plan is not None
    assert [item.field for item in plan.projections] == ["type"]
    assert [item.field for item in plan.group_by] == ["type"]
    assert [(item.function.value, item.field, item.alias) for item in plan.aggregations] == [
        ("count", "_id", "events")
    ]
    assert plan.filters == [] and plan.order_by == []
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "mongodb_count_intent_mismatch"


def test_natural_language_mongodb_total_count_is_distinct_from_grouped_count(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mongodb_profiles_env
    client = _SequencedNaturalClient(
        _events_count_plan(assets["events"]),
        _events_count_plan(assets["events"], grouped=True),
    )
    service = NaturalLanguageQueryService(settings, client=client)  # type: ignore[arg-type]

    total = service.translate(NaturalLanguageQueryRequest(
        question="Quanti eventi ci sono nel database MongoDB?"
    ))
    grouped = service.translate(NaturalLanguageQueryRequest(
        question="Quanti eventi ci sono per tipo nel database MongoDB?"
    ))

    assert total.normalized_plan is not None and total.normalized_plan.group_by == []
    assert total.normalized_plan.projections == []
    assert grouped.normalized_plan is not None
    assert [item.field for item in grouped.normalized_plan.group_by] == ["type"]
    assert len(client.planning_calls) == 2


@pytest.mark.parametrize(
    ("question", "function", "alias"),
    [
        ("Qual è l’importo totale degli eventi MongoDB?", "sum", "total"),
        ("Qual è l’importo medio degli eventi MongoDB?", "avg", "avg_amount"),
    ],
)
def test_natural_language_mongodb_event_amount_scalar_aggregation_has_no_order(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
    question: str,
    function: str,
    alias: str,
) -> None:
    settings, _, assets = mongodb_profiles_env
    invalid = _events_amount_metric_plan(assets["events"], function, alias)
    invalid["order_by"] = [{"field": alias, "direction": "desc"}]
    corrected = _events_amount_metric_plan(assets["events"], function, alias)
    client = _SequencedNaturalClient(invalid, corrected)

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question=question)
    )

    plan = response.normalized_plan
    assert plan is not None
    assert [(item.function.value, item.field, item.alias) for item in plan.aggregations] == [
        (function, "properties.amount", alias)
    ]
    assert plan.projections == [] and plan.filters == []
    assert plan.group_by == [] and plan.order_by == []
    assert [field.name for field in response.output_schema] == [alias]
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "mongodb_scalar_aggregation_mismatch"


def test_natural_language_mongodb_filtered_event_count_uses_numeric_literal(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, query_service, assets = mongodb_profiles_env
    invalid = _events_user_count_plan(assets["events"], "1")
    corrected = _events_user_count_plan(assets["events"], 1)
    client = _SequencedNaturalClient(invalid, corrected)
    executions = 0

    def execute(compiled: Any) -> Any:
        nonlocal executions
        executions += 1
        return (["events"], [[1]], False, 1.0)

    monkeypatch.setattr(query_service.mongodb_executor, "execute", execute)
    response = NaturalLanguageQueryService(
        settings, client=client, query_service=query_service  # type: ignore[arg-type]
    ).translate(NaturalLanguageQueryRequest(
        question="Quanti eventi MongoDB sono stati generati dall’utente 1?",
        execute=True,
    ))

    plan = response.normalized_plan
    assert plan is not None and len(plan.filters) == 1
    assert plan.filters[0].field == "user_id"
    assert plan.filters[0].operator.value == "eq"
    assert plan.filters[0].value == 1
    assert isinstance(plan.filters[0].value, int)
    assert plan.projections == [] and plan.group_by == [] and plan.order_by == []
    assert response.result is not None and response.result.rows == [[1]]
    assert executions == 1
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "missing_explicit_filter"


def test_natural_language_mongodb_amount_filter_remains_row_returning(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mongodb_profiles_env
    client = _SequencedNaturalClient(_events_amount_rows_plan(assets["events"]))

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Mostra gli eventi MongoDB con importo maggiore di 100"
        )
    )

    plan = response.normalized_plan
    assert plan is not None and plan.aggregations == [] and plan.group_by == []
    assert len(plan.filters) == 1
    assert (
        plan.filters[0].field,
        plan.filters[0].operator.value,
        plan.filters[0].value,
    ) == ("properties.amount", "gt", 100)
    assert [item.field for item in plan.projections] == [
        "type", "user_id", "properties.amount"
    ]


def test_natural_language_mongodb_filtered_count_adds_aggregation_source_alias(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mongodb_profiles_env
    invalid = _events_user_count_plan(assets["events"], 1)
    invalid["aggregations"][0].pop("source_alias")
    corrected = _events_user_count_plan(assets["events"], 1)
    client = _SequencedNaturalClient(invalid, corrected)

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Quanti eventi MongoDB sono stati generati dall’utente 1?"
        )
    )

    plan = response.normalized_plan
    assert plan is not None
    assert plan.aggregations[0].source_alias == plan.sources[0].alias == "events"
    assert plan.filters[0].field == "user_id"
    assert plan.filters[0].value == 1
    assert isinstance(plan.filters[0].value, int)
    assert plan.projections == [] and plan.group_by == [] and plan.order_by == []
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "mongodb_aggregation_source_alias_mismatch"


def test_natural_language_mongodb_filtered_count_validates_and_executes(
    mongodb_profiles_env: tuple[Settings, QueryService, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, query_service, assets = mongodb_profiles_env
    client = _SequencedNaturalClient(_events_user_count_plan(assets["events"], 1))
    monkeypatch.setattr(
        query_service.mongodb_executor,
        "execute",
        lambda compiled: (["events"], [[1]], False, 1.0),
    )

    response = NaturalLanguageQueryService(
        settings, client=client, query_service=query_service  # type: ignore[arg-type]
    ).translate(NaturalLanguageQueryRequest(
        question="Quanti eventi MongoDB sono stati generati dall’utente 1?",
        execute=True,
    ))

    assert response.normalized_plan is not None
    assert response.normalized_plan.aggregations[0].source_alias == "events"
    assert response.result is not None and response.result.rows == [[1]]
