from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import UploadFile

from queryx.app.api import routes as api_routes
from queryx.app.core.config import Settings
from queryx.app.ingestion.service import IngestionService
from queryx.app.main import create_app
from queryx.app.processing.service import ProcessingService
from queryx.app.query.compiler import DuckDBQueryCompiler
from queryx.app.query.executor import QueryExecutionError
from queryx.app.query.models import AssetRelationshipCreate, LogicalQueryPlan
from queryx.app.query.service import QueryService, RelationshipService
from queryx.app.query.validation import QueryValidationError


@pytest.fixture
def query_env(tmp_path: Path) -> tuple[Settings, dict[str, Any]]:
    data = tmp_path / "data"
    settings = Settings(
        catalog_db_path=data / "catalog.sqlite3",
        data_raw_dir=data / "raw",
        data_staging_dir=data / "staging",
        data_normalized_dir=data / "normalized",
        duckdb_path=data / "queryx.duckdb",
        duckdb_lock_path=data / "queryx.duckdb.lock",
        query_default_limit=2,
        query_max_limit=5,
        query_timeout_seconds=2,
        ingestion_inspection_rows=20,
        parquet_batch_rows=2,
        mysql_enabled=False,
        mongodb_enabled=False,
    )
    datasets = {
        "orders": (
            "order_id,customer_id,order_status,order_purchase_timestamp\n"
            "o1,c1,delivered,2018-01-01 10:00:00\n"
            "o2,c2,shipped,2018-01-15 11:00:00\n"
            "o3,c1,delivered,2018-02-02 12:00:00\n"
        ),
        "customers": 'customer_id,"state""code"\nc1,SP\nc2,RJ\n',
        "order_items": "order_id,product_id,price\no1,p1,10.5\no2,p2,20.0\no3,p1,5.0\n",
        "products": "product_id,product_category_name\np1,books\np2,toys\n",
    }
    assets: dict[str, Any] = {}
    ingestion = IngestionService(settings)
    processing = ProcessingService(settings)
    for name, content in datasets.items():
        stream = tempfile.SpooledTemporaryFile()
        stream.write(content.encode())
        stream.seek(0)
        uploaded = asyncio.run(
            ingestion.ingest_upload(UploadFile(stream, filename=f"{name}.csv"), logical_name=name)
        )
        assert uploaded.asset_id and uploaded.asset_version_id
        processing.prepare(uploaded.asset_id, uploaded.asset_version_id)
        assets[name] = uploaded
    return settings, assets


def _relationship(
    service: RelationshipService, assets: dict[str, Any],
    left: str, left_field: str, right: str, right_field: str,
) -> Any:
    return service.create(AssetRelationshipCreate(
        left_asset_id=assets[left].asset_id,
        left_field=left_field,
        right_asset_id=assets[right].asset_id,
        right_field=right_field,
        relationship_type="many_to_one",
    ))


def _status_plan(assets: dict[str, Any], **updates: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "sources": [{"alias": "o", "asset_id": assets["orders"].asset_id}],
        "projections": [{"source_alias": "o", "field": "order_status", "alias": "status"}],
        "aggregations": [{"function": "count", "source_alias": "o", "field": "order_id", "alias": "orders"}],
        "group_by": [{"source_alias": "o", "field": "order_status"}],
        "order_by": [{"field": "orders", "direction": "desc"}],
    }
    plan.update(updates)
    return plan


def test_relationship_creation_validation_duplicate_and_disable(query_env: tuple[Settings, dict[str, Any]]) -> None:
    settings, assets = query_env
    service = RelationshipService(settings)
    relationship = _relationship(service, assets, "customers", "customer_id", "orders", "customer_id")
    assert relationship.status == "active"
    assert service.get(relationship.id) == relationship
    with pytest.raises(QueryValidationError) as duplicate:
        _relationship(service, assets, "customers", "customer_id", "orders", "customer_id")
    assert duplicate.value.code == "relationship_duplicate"
    disabled = service.disable(relationship.id)
    assert disabled and disabled.status == "disabled"
    assert len(service.list()) == 1


@pytest.mark.parametrize(
    ("asset_key", "field", "code"),
    [("missing", "customer_id", "asset_not_found"), ("orders", "missing_field", "field_not_found")],
)
def test_relationship_rejects_missing_asset_or_observed_field(
    query_env: tuple[Settings, dict[str, Any]], asset_key: str, field: str, code: str,
) -> None:
    settings, assets = query_env
    asset_id = "does-not-exist" if asset_key == "missing" else assets[asset_key].asset_id
    with pytest.raises(QueryValidationError) as captured:
        RelationshipService(settings).create(AssetRelationshipCreate(
            left_asset_id=asset_id, left_field=field,
            right_asset_id=assets["customers"].asset_id, right_field="customer_id",
            relationship_type="many_to_one",
        ))
    assert captured.value.code == code


def test_single_source_default_limit_and_maximum(query_env: tuple[Settings, dict[str, Any]]) -> None:
    settings, assets = query_env
    service = QueryService(settings)
    validated = service.validate(_status_plan(assets))
    assert validated.normalized_plan.limit == 2
    with pytest.raises(QueryValidationError) as captured:
        service.validate(_status_plan(assets, limit=6))
    assert captured.value.code == "query_limit_exceeded"


def test_duckdb_rejects_undeclared_source_alias(
    query_env: tuple[Settings, dict[str, Any]],
) -> None:
    settings, assets = query_env
    payload = _status_plan(assets)
    payload["projections"][0]["source_alias"] = "orders"

    with pytest.raises(QueryValidationError) as captured:
        QueryService(settings).validate(payload)

    assert captured.value.code == "source_alias_not_found"
    assert captured.value.details == {
        "source_alias": "orders", "location": "projection"
    }


def test_declared_join_and_disabled_relationship(query_env: tuple[Settings, dict[str, Any]]) -> None:
    settings, assets = query_env
    relationships = RelationshipService(settings)
    relationship = _relationship(
        relationships, assets, "order_items", "product_id", "products", "product_id"
    )
    plan = _revenue_plan(assets, relationship.id)
    assert QueryService(settings).validate(plan).output_schema[-1].name == "revenue"
    relationships.disable(relationship.id)
    with pytest.raises(QueryValidationError) as captured:
        QueryService(settings).validate(plan)
    assert captured.value.code == "relationship_disabled"


def test_join_without_relationship_and_unknown_field_are_rejected(
    query_env: tuple[Settings, dict[str, Any]],
) -> None:
    settings, assets = query_env
    service = QueryService(settings)
    with pytest.raises(QueryValidationError) as missing:
        service.validate(_revenue_plan(assets, "missing"))
    assert missing.value.code == "relationship_not_found"
    invalid = _status_plan(assets)
    invalid["projections"][0]["field"] = "not_cataloged"
    invalid["group_by"][0]["field"] = "not_cataloged"
    with pytest.raises(QueryValidationError) as field:
        service.validate(invalid)
    assert field.value.code == "field_not_found"


def test_aggregation_and_group_by_remain_strict(query_env: tuple[Settings, dict[str, Any]]) -> None:
    settings, assets = query_env
    service = QueryService(settings)
    invalid_aggregation = _status_plan(assets)
    invalid_aggregation["aggregations"] = [
        {"function": "sum", "source_alias": "o", "field": "order_status", "alias": "bad"}
    ]
    invalid_aggregation["order_by"] = []
    with pytest.raises(QueryValidationError) as aggregation:
        service.validate(invalid_aggregation)
    assert aggregation.value.code == "aggregation_type_mismatch"
    invalid_group = _status_plan(assets, group_by=[])
    with pytest.raises(QueryValidationError) as group:
        service.validate(invalid_group)
    assert group.value.code == "invalid_group_by"


def test_compiler_quotes_identifiers_and_parameterizes_values(
    query_env: tuple[Settings, dict[str, Any]],
) -> None:
    settings, assets = query_env
    payload = _status_plan(assets, filters=[{
        "source_alias": "o", "field": "order_status", "operator": "eq",
        "value": "delivered' OR 1=1 --",
    }])
    validated = QueryService(settings).validator.validate(LogicalQueryPlan.model_validate(payload))
    compiled = DuckDBQueryCompiler(settings.duckdb_schema).compile(validated)
    assert '"o"."order_status"' in compiled.sql
    assert "delivered' OR 1=1 --" not in compiled.sql
    assert compiled.parameters[0] == "delivered' OR 1=1 --"
    assert compiled.sql.startswith("SELECT ") and " LIMIT ?" in compiled.sql


def test_special_cataloged_identifier_executes_safely(query_env: tuple[Settings, dict[str, Any]]) -> None:
    settings, assets = query_env
    result = QueryService(settings).execute({
        "sources": [{"alias": "c", "asset_id": assets["customers"].asset_id}],
        "projections": [{"source_alias": "c", "field": 'state"code', "alias": "state"}],
        "order_by": [{"field": "state", "direction": "asc"}],
        "limit": 5,
    })
    assert result.rows == [["RJ"], ["SP"]]


def test_orders_by_status_and_month_execute(query_env: tuple[Settings, dict[str, Any]]) -> None:
    settings, assets = query_env
    service = QueryService(settings)
    by_status = service.execute(_status_plan(assets, limit=5))
    assert by_status.columns == ["status", "orders"]
    assert by_status.rows[0] == ["delivered", 2]
    by_month = service.execute({
        "sources": [{"alias": "o", "asset_id": assets["orders"].asset_id}],
        "projections": [{
            "source_alias": "o", "field": "order_purchase_timestamp",
            "transform": "date_trunc_month", "alias": "month",
        }],
        "aggregations": [{"function": "count", "alias": "orders"}],
        "group_by": [{
            "source_alias": "o", "field": "order_purchase_timestamp",
            "transform": "date_trunc_month",
        }],
        "order_by": [{"field": "month", "direction": "asc"}],
        "limit": 5,
    })
    assert by_month.rows == [["2018-01-01", 2], ["2018-02-01", 1]]


def test_revenue_by_category_join_executes(query_env: tuple[Settings, dict[str, Any]]) -> None:
    settings, assets = query_env
    relationship = _relationship(
        RelationshipService(settings), assets, "order_items", "product_id", "products", "product_id"
    )
    result = QueryService(settings).execute(_revenue_plan(assets, relationship.id))
    assert result.columns == ["category", "revenue"]
    assert result.rows == [["toys", 20.0], ["books", 15.5]]


def test_query_run_audit_contains_no_rows(query_env: tuple[Settings, dict[str, Any]]) -> None:
    settings, assets = query_env
    service = QueryService(settings)
    service.execute(_status_plan(assets))
    with sqlite3.connect(settings.catalog_db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(query_runs)")}
        row = connection.execute(
            "SELECT status, rows_returned, normalized_plan_json FROM query_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert "rows_json" not in columns and row[0] == "completed" and row[1] == 2
    assert "rows" not in json.loads(row[2])


def test_timeout_is_structured_and_audited(
    query_env: tuple[Settings, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, assets = query_env
    service = QueryService(settings)
    monkeypatch.setattr(
        service.executor, "execute",
        lambda compiled: (_ for _ in ()).throw(QueryExecutionError("query_timeout", "Query execution timed out", 408)),
    )
    with pytest.raises(QueryExecutionError) as captured:
        service.execute(_status_plan(assets))
    assert captured.value.code == "query_timeout"
    with sqlite3.connect(settings.catalog_db_path) as connection:
        status, error_json = connection.execute(
            "SELECT status, error_json FROM query_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert status == "failed" and "query_timeout" in error_json


def test_api_validate_execute_relationships_and_rejects_sql(
    query_env: tuple[Settings, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, assets = query_env
    monkeypatch.setattr(api_routes, "get_settings", lambda: settings)

    async def exercise() -> tuple[httpx.Response, ...]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            relationship = await client.post("/relationships", json={
                "left_asset_id": assets["customers"].asset_id, "left_field": "customer_id",
                "right_asset_id": assets["orders"].asset_id, "right_field": "customer_id",
                "relationship_type": "one_to_many",
            })
            relationship_id = relationship.json()["id"]
            return (
                relationship,
                await client.get("/relationships"),
                await client.get(f"/relationships/{relationship_id}"),
                await client.post("/query/validate", json=_status_plan(assets)),
                await client.post("/query/execute", json=_status_plan(assets)),
                await client.post("/query/execute", json={"sql": "DROP TABLE data_assets"}),
                await client.delete(f"/relationships/{relationship_id}"),
            )

    created, listed, fetched, validated, executed, arbitrary, disabled = asyncio.run(exercise())
    assert created.status_code == 201
    assert listed.status_code == fetched.status_code == 200
    assert validated.status_code == executed.status_code == 200
    assert arbitrary.status_code == 422
    assert disabled.json()["status"] == "disabled"


def test_query_and_relationship_ui_are_csrf_protected(
    query_env: tuple[Settings, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, assets = query_env
    import queryx.app.main as main
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    async def exercise() -> tuple[httpx.Response, ...]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            page = await client.get("/ui/query")
            token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)  # type: ignore[union-attr]
            validated = await client.post(
                "/ui/query/validate", data={"csrf_token": token, "plan_json": json.dumps(_status_plan(assets))}
            )
            relationships = await client.get("/ui/relationships")
            forbidden = await client.post(
                "/ui/query/execute", data={"plan_json": json.dumps(_status_plan(assets))}
            )
            return page, validated, relationships, forbidden

    page, validated, relationships, forbidden = asyncio.run(exercise())
    assert page.status_code == validated.status_code == relationships.status_code == 200
    assert "Logical Query Plan" in page.text and "Piano valido" in validated.text
    assert "Crea relazione" in relationships.text
    assert forbidden.status_code == 403


def _revenue_plan(assets: dict[str, Any], relationship_id: str) -> dict[str, Any]:
    return {
        "sources": [
            {"alias": "oi", "asset_id": assets["order_items"].asset_id},
            {"alias": "p", "asset_id": assets["products"].asset_id},
        ],
        "joins": [{
            "relationship_id": relationship_id, "left_alias": "oi", "right_alias": "p"
        }],
        "projections": [{
            "source_alias": "p", "field": "product_category_name", "alias": "category"
        }],
        "aggregations": [{
            "function": "sum", "source_alias": "oi", "field": "price", "alias": "revenue"
        }],
        "group_by": [{"source_alias": "p", "field": "product_category_name"}],
        "order_by": [{"field": "revenue", "direction": "desc"}],
        "limit": 5,
    }
