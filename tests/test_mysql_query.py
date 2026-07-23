from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import UploadFile
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from queryx.app.api import routes as api_routes
from queryx.app.catalog.bootstrap import backfill_mysql_assets
from queryx.app.catalog.models import ScanRun, SourceScanResult
from queryx.app.catalog.service import CatalogService
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.core.config import Settings
from queryx.app.ingestion.service import IngestionService
from queryx.app.ingestion.storage import IngestionStorage
from queryx.app.llm.ollama_client import (
    OllamaInvalidResponseError,
    OllamaResponse,
    OllamaTextResponse,
)
from queryx.app.main import create_app
from queryx.app.processing.service import ProcessingService
from queryx.app.query.mysql_executor import MySQLQueryExecutor
from queryx.app.query.models import LogicalQueryPlan, NaturalLanguageQueryRequest
from queryx.app.query.natural_language import NaturalLanguageQueryService
from queryx.app.query.service import QueryService
from queryx.app.query.validation import QueryValidationError
from queryx.app.sources.registry import SourceRegistry


def _save_mysql_scan(
    settings: Settings,
    tables: list[dict[str, Any]],
    *,
    scan_status: str = "completed",
) -> None:
    now = datetime.now(timezone.utc)
    catalog = CatalogService(CatalogStorage(settings.catalog_db_path))
    catalog.upsert_sources(SourceRegistry(settings).list_sources())
    result = SourceScanResult(
        source_id=settings.mysql_source_id,
        database_type="mysql",
        scan_status=scan_status,
        started_at=now,
        finished_at=now,
        duration_ms=1,
        fingerprint="mysql-fingerprint" if scan_status == "completed" else None,
        declared_metadata={"tables": tables} if scan_status == "completed" else {},
        error=None if scan_status == "completed" else {"code": "source_unavailable"},
    )
    catalog.save_run(
        ScanRun(
            started_at=now,
            finished_at=now,
            duration_ms=1,
            status=scan_status,
            sources_succeeded=int(scan_status == "completed"),
            sources_failed=int(scan_status != "completed"),
            results=[result],
        )
    )


def _mysql_settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    return Settings(
        catalog_db_path=data / "catalog.sqlite3",
        data_raw_dir=data / "raw",
        data_staging_dir=data / "staging",
        data_normalized_dir=data / "normalized",
        duckdb_path=data / "queryx.duckdb",
        duckdb_lock_path=data / "queryx.duckdb.lock",
        mysql_url="mysql+pymysql://queryx:secret@db.invalid:3306/demo_schema",
        mysql_host="db.invalid",
        mysql_database="demo_schema",
        mysql_user="do-not-expose-user",
        mysql_password="do-not-expose-password",
        mysql_enabled=True,
        mongodb_enabled=False,
        query_default_limit=2,
        query_max_limit=5,
        mysql_query_timeout_seconds=3,
    )


@pytest.fixture
def mysql_query_env(tmp_path: Path) -> tuple[Settings, QueryService, dict[str, Any]]:
    settings = _mysql_settings(tmp_path)
    tables = [
                {
                    "name": "orders",
                    "columns": [
                        {"name": "id", "type": "INTEGER", "nullable": False},
                        {"name": "customer_id", "type": "INTEGER", "nullable": False},
                        {"name": "status", "type": "VARCHAR(40)", "nullable": False},
                        {"name": "total", "type": "DECIMAL(10, 2)", "nullable": False},
                        {"name": "created_at", "type": "DATETIME", "nullable": False},
                        {"name": "notes", "type": "TEXT", "nullable": True},
                        {"name": "select", "type": "VARCHAR(40)", "nullable": True},
                    ],
                    "primary_key": {"columns": ["id"]},
                    "foreign_keys": [
                        {"columns": ["customer_id"], "referenced_table": "customers"}
                    ],
                    "indexes": [{"name": "idx_orders_status", "columns": ["status"]}],
                },
                {
                    "name": "customers",
                    "columns": [
                        {"name": "id", "type": "INTEGER", "nullable": False},
                        {"name": "name", "type": "VARCHAR(120)", "nullable": False},
                    ],
                },
            ]
    _save_mysql_scan(settings, tables)
    service = QueryService(settings)
    assets = {asset.name: asset for asset in service.mysql_catalog.list_ready_assets()}
    return settings, service, assets


def _mysql_plan(assets: dict[str, Any], **updates: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "sources": [{"alias": "o", "asset_id": assets["orders"].asset_id}],
        "projections": [{"source_alias": "o", "field": "status", "alias": "status"}],
        "aggregations": [
            {"function": "count", "source_alias": "o", "field": "id", "alias": "orders"}
        ],
        "group_by": [{"source_alias": "o", "field": "status"}],
        "order_by": [{"field": "orders", "direction": "desc"}],
    }
    plan.update(updates)
    return plan


def test_mysql_scan_promotes_virtual_assets_and_assets_api(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _, assets = mysql_query_env
    storage = IngestionStorage(settings.catalog_db_path)
    catalog_assets = {asset.name: asset for asset in storage.list_assets()}

    assert set(catalog_assets) == {"customers", "orders"}
    orders = catalog_assets["orders"]
    assert orders.id == assets["orders"].asset_id
    assert str(orders.asset_kind) == "mysql_table"
    assert orders.versions[0].status == "ready"
    assert orders.versions[0].storage_bindings == []
    metadata = orders.versions[0].technical_metadata
    assert metadata["source_id"] == settings.mysql_source_id
    assert metadata["database"] == metadata["schema"] == "demo_schema"
    assert metadata["table"] == "orders"
    assert metadata["primary_key"] == {"columns": ["id"]}
    assert metadata["foreign_keys"]
    assert metadata["indexes"]
    assert metadata["schema_fingerprint"] == orders.versions[0].schema_fingerprint

    monkeypatch.setattr(
        api_routes,
        "_ingestion_service",
        lambda settings_arg=None: IngestionService(settings_arg or settings),
    )
    import queryx.app.main as main
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    async def exercise() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            return await client.get("/assets")

    response = asyncio.run(exercise())
    assert response.status_code == 200
    returned = {asset["name"]: asset for asset in response.json()["assets"]}
    assert returned["customers"]["asset_kind"] == "mysql_table"
    assert returned["orders"]["id"] == assets["orders"].asset_id


def test_mysql_backfill_promotes_historical_completed_scan_idempotently(
    tmp_path: Path,
) -> None:
    settings = _mysql_settings(tmp_path)
    now = datetime.now(timezone.utc)
    storage = CatalogStorage(settings.catalog_db_path)
    storage.save_scan_run(
        ScanRun(
            started_at=now,
            finished_at=now,
            duration_ms=1,
            status="completed",
            sources_succeeded=1,
            sources_failed=0,
            results=[SourceScanResult(
                source_id=settings.mysql_source_id,
                database_type="mysql",
                scan_status="completed",
                started_at=now,
                finished_at=now,
                duration_ms=1,
                fingerprint="historical",
                declared_metadata={
                    "tables": [{
                        "name": "customers",
                        "columns": [{"name": "id", "type": "INTEGER", "nullable": False}],
                    }]
                },
            )],
        )
    )

    assert IngestionStorage(settings.catalog_db_path).list_assets() == []
    backfill_mysql_assets(settings)
    first = IngestionStorage(settings.catalog_db_path).list_assets()
    backfill_mysql_assets(settings)
    second = IngestionStorage(settings.catalog_db_path).list_assets()
    assert len(first) == len(second) == 1
    assert first[0].id == second[0].id
    assert [version.id for version in second[0].versions] == [first[0].versions[0].id]


def test_mysql_backfill_ignores_failed_latest_scan(tmp_path: Path) -> None:
    settings = _mysql_settings(tmp_path)
    now = datetime.now(timezone.utc)
    storage = CatalogStorage(settings.catalog_db_path)
    storage.save_scan_run(
        ScanRun(
            started_at=now,
            finished_at=now,
            duration_ms=1,
            status="failed",
            sources_succeeded=0,
            sources_failed=1,
            results=[SourceScanResult(
                source_id=settings.mysql_source_id,
                database_type="mysql",
                scan_status="failed",
                started_at=now,
                finished_at=now,
                duration_ms=1,
                error={"code": "source_unavailable"},
            )],
        )
    )
    backfill_mysql_assets(settings)
    assert IngestionStorage(settings.catalog_db_path).list_assets() == []


def test_mysql_promotion_is_idempotent_and_schema_change_creates_version(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mysql_query_env
    catalog = CatalogStorage(settings.catalog_db_path)
    original_tables = catalog.get_latest_successful_source_result(
        settings.mysql_source_id
    ).declared_metadata["tables"]
    storage = IngestionStorage(settings.catalog_db_path)
    original = storage.get_asset(assets["orders"].asset_id)
    assert original is not None
    original_version_id = original.versions[0].id

    _save_mysql_scan(settings, original_tables)
    unchanged = storage.get_asset(original.id)
    assert unchanged is not None
    assert [version.id for version in unchanged.versions] == [original_version_id]

    changed_tables = json.loads(json.dumps(original_tables))
    changed_tables[0]["columns"].append(
        {"name": "created_at", "type": "DATETIME", "nullable": True}
    )
    _save_mysql_scan(settings, changed_tables)
    changed = storage.get_asset(original.id)
    assert changed is not None
    assert len(changed.versions) == 2
    assert changed.versions[0].id != original_version_id
    assert changed.versions[0].status == "ready"
    assert changed.versions[1].status == "stale"
    assert changed.versions[0].technical_metadata["fields"][-1]["name"] == "created_at"

    query_service = QueryService(settings)
    current = query_service.mysql_catalog.resolve(original.id)
    assert current is not None
    resolved = query_service.validator.validate(
        LogicalQueryPlan.model_validate(_mysql_plan({"orders": current}))
    )
    assert resolved.sources["o"].asset_id == original.id
    assert resolved.sources["o"].asset_version_id == changed.versions[0].id


def test_mysql_removed_table_becomes_stale_and_not_queryable(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mysql_query_env
    catalog = CatalogStorage(settings.catalog_db_path)
    tables = catalog.get_latest_successful_source_result(
        settings.mysql_source_id
    ).declared_metadata["tables"]
    _save_mysql_scan(settings, [table for table in tables if table["name"] == "customers"])

    removed = IngestionStorage(settings.catalog_db_path).get_asset(assets["orders"].asset_id)
    assert removed is not None
    assert all(version.status == "stale" for version in removed.versions)
    with pytest.raises(QueryValidationError) as captured:
        QueryService(settings).validate(_mysql_plan(assets))
    assert captured.value.code == "mysql_source_not_ready"


def test_mysql_single_source_validation_projection_and_limits(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, service, assets = mysql_query_env
    validated = service.validate(_mysql_plan(assets))
    assert validated.normalized_plan.limit == settings.query_default_limit
    assert [field.name for field in validated.output_schema] == ["status", "orders"]
    with pytest.raises(QueryValidationError) as captured:
        service.validate(_mysql_plan(assets, limit=settings.query_max_limit + 1))
    assert captured.value.code == "query_limit_exceeded"


def test_mysql_rejects_undeclared_source_alias(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    _, service, assets = mysql_query_env
    payload = _mysql_plan(assets)
    payload["filters"] = [{
        "source_alias": "orders",
        "field": "status",
        "operator": "eq",
        "value": "paid",
    }]

    with pytest.raises(QueryValidationError) as captured:
        service.validate(payload)

    assert captured.value.code == "source_alias_not_found"
    assert captured.value.details["location"] == "filter"


def test_mysql_compiler_quotes_and_parameterizes_filters_and_aggregation(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    _, service, assets = mysql_query_env
    payload = _mysql_plan(
        assets,
        projections=[{"source_alias": "o", "field": "select", "alias": "category"}],
        group_by=[{"source_alias": "o", "field": "select"}],
        filters=[{
            "source_alias": "o",
            "field": "status",
            "operator": "eq",
            "value": "paid' OR 1=1 --",
        }],
        aggregations=[{
            "function": "sum", "source_alias": "o", "field": "total", "alias": "revenue"
        }],
        order_by=[{"field": "revenue", "direction": "desc"}],
        limit=5,
    )
    validated = service.validator.validate(LogicalQueryPlan.model_validate(payload))
    compiled = service.mysql_compiler.compile(validated)
    assert compiled.sql.startswith("SELECT ")
    assert "FROM `demo_schema`.`orders` AS `o`" in compiled.sql
    assert "`o`.`select`" in compiled.sql
    assert "SUM(`o`.`total`) AS `revenue`" in compiled.sql
    assert "paid' OR 1=1 --" not in compiled.sql
    assert compiled.parameters["p0"] == "paid' OR 1=1 --"
    assert compiled.parameters["result_limit"] == 6
    assert compiled.sql.endswith("LIMIT :result_limit")


def test_mysql_scope_cross_backend_and_arbitrary_sql_are_rejected(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, service, assets = mysql_query_env
    stream = tempfile.SpooledTemporaryFile()
    stream.write(b"id,name\n1,local\n")
    stream.seek(0)
    uploaded = asyncio.run(
        IngestionService(settings).ingest_upload(
            UploadFile(stream, filename="local.csv"), logical_name="local"
        )
    )
    ProcessingService(settings).prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")
    cross_backend = {
        "sources": [
            {"alias": "l", "asset_id": uploaded.asset_id},
            {"alias": "m", "asset_id": assets["orders"].asset_id},
        ],
        "projections": [{"source_alias": "l", "field": "name"}],
    }
    with pytest.raises(QueryValidationError) as federation:
        QueryService(settings).validate(cross_backend)
    assert federation.value.code == "federation_not_supported"
    with pytest.raises(QueryValidationError) as arbitrary:
        service.validate({"sql": "SELECT * FROM orders"})
    assert arbitrary.value.code == "invalid_logical_query_plan"


def test_mysql_multi_source_join_transform_and_unknown_field_are_rejected(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    _, service, assets = mysql_query_env
    multi = {
        "sources": [
            {"alias": "o", "asset_id": assets["orders"].asset_id},
            {"alias": "c", "asset_id": assets["customers"].asset_id},
        ],
        "projections": [{"source_alias": "o", "field": "status"}],
    }
    with pytest.raises(QueryValidationError) as multiple:
        service.validate(multi)
    assert multiple.value.code == "mysql_multi_source_not_supported"
    joined = _mysql_plan(assets)
    joined["joins"] = [{
        "relationship_id": "not-used",
        "left_alias": "o",
        "right_alias": "o",
    }]
    with pytest.raises(QueryValidationError) as join:
        service.validate(joined)
    assert join.value.code == "mysql_joins_not_supported"
    transformed = _mysql_plan(assets)
    transformed["projections"][0]["transform"] = "date_trunc_month"
    transformed["group_by"][0]["transform"] = "date_trunc_month"
    with pytest.raises(QueryValidationError) as transform:
        service.validate(transformed)
    assert transform.value.code == "mysql_transform_not_supported"
    missing = _mysql_plan(assets)
    missing["projections"][0]["field"] = "missing"
    missing["group_by"][0]["field"] = "missing"
    with pytest.raises(QueryValidationError) as field:
        service.validate(missing)
    assert field.value.code == "field_not_found"


def test_mysql_asset_from_stale_source_is_not_queryable(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mysql_query_env
    _save_mysql_scan(settings, [], scan_status="failed")
    with pytest.raises(QueryValidationError) as captured:
        QueryService(settings).validate(_mysql_plan(assets))
    assert captured.value.code == "mysql_source_not_ready"


def test_mysql_execute_routes_and_audits_without_filter_values(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, service, assets = mysql_query_env
    monkeypatch.setattr(
        service.mysql_executor,
        "execute",
        lambda compiled: (["status", "orders"], [["paid", 2], ["pending", 1]], False, 4.5),
    )
    payload = _mysql_plan(assets, filters=[{
        "source_alias": "o", "field": "status", "operator": "neq", "value": "secret"
    }])
    result = service.execute(payload)
    assert result.columns == ["status", "orders"] and result.row_count == 2
    with sqlite3.connect(settings.catalog_db_path) as connection:
        backend, source_ids, plan_json = connection.execute(
            "SELECT backend, source_ids_json, normalized_plan_json FROM query_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert backend == "mysql"
    assert json.loads(source_ids) == [settings.mysql_source_id]
    assert "secret" not in plan_json and "<redacted>" in plan_json
    assert "rows" not in json.loads(plan_json)


class _FailingEngine:
    def connect(self) -> Any:
        raise SQLAlchemyError("offline")


class _TimeoutConnection:
    def __enter__(self) -> _TimeoutConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: Any, parameters: Any = None) -> Any:
        if str(statement).startswith("SELECT"):
            raise DBAPIError(str(statement), parameters, TimeoutError("timeout"), False)
        return object()


class _TimeoutEngine:
    def connect(self) -> _TimeoutConnection:
        return _TimeoutConnection()


def test_mysql_executor_connection_error_and_timeout(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    _, service, assets = mysql_query_env
    validated = service.validator.validate(
        LogicalQueryPlan.model_validate(_mysql_plan(assets))
    )
    compiled = service.mysql_compiler.compile(validated)
    from queryx.app.query.executor import QueryExecutionError

    with pytest.raises(QueryExecutionError) as connection:
        MySQLQueryExecutor("mysql+pymysql://unused", 1, engine=_FailingEngine()).execute(compiled)  # type: ignore[arg-type]
    assert connection.value.code == "mysql_connection_failed"
    with pytest.raises(QueryExecutionError) as timeout:
        MySQLQueryExecutor("mysql+pymysql://unused", 1, engine=_TimeoutEngine()).execute(compiled)  # type: ignore[arg-type]
    assert timeout.value.code == "query_timeout"


def test_mysql_api_execute_uses_existing_result_contract(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, service, assets = mysql_query_env
    monkeypatch.setattr(
        service.mysql_executor,
        "execute",
        lambda compiled: (["status", "orders"], [["paid", 2]], False, 2.0),
    )
    monkeypatch.setattr(api_routes, "_query_service", lambda settings=None: service)
    import queryx.app.main as main
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    async def exercise() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            return await client.post("/query/execute", json=_mysql_plan(assets))

    response = asyncio.run(exercise())
    assert response.status_code == 200
    assert response.json()["rows"] == [["paid", 2]]
    assert "sql" not in response.json()


class _NaturalClient:
    def __init__(self, plan: dict[str, Any]) -> None:
        self.plan = plan
        self.prompts: list[str] = []

    def chat_text(
        self, messages: list[dict[str, str]], json_schema: dict[str, Any] | None = None,
    ) -> OllamaTextResponse:
        self.prompts.extend(message["content"] for message in messages)
        return OllamaTextResponse(
            json.dumps({"classification": "answerable", "reason": "I dati sono disponibili."}),
            {},
        )

    def chat_json(
        self, messages: list[dict[str, str]], json_schema: dict[str, Any]
    ) -> OllamaResponse:
        self.prompts.extend(message["content"] for message in messages)
        return OllamaResponse(self.plan, {})


class _SequencedNaturalClient(_NaturalClient):
    def __init__(
        self,
        *plans: dict[str, Any],
        classification: dict[str, Any] | None = None,
        explanation: str = "La query ha restituito il risultato richiesto.",
    ) -> None:
        super().__init__(plans[0])
        self.plans = list(plans)
        self.classification = classification or {
            "classification": "answerable",
            "reason": "I dati sono disponibili.",
        }
        self.explanation = explanation
        self.planning_calls: list[list[dict[str, str]]] = []

    def chat_text(
        self, messages: list[dict[str, str]], json_schema: dict[str, Any] | None = None,
    ) -> OllamaTextResponse:
        self.prompts.extend(message["content"] for message in messages)
        if json_schema is None:
            return OllamaTextResponse(self.explanation, {})
        return OllamaTextResponse(json.dumps(self.classification), {})

    def chat_json(
        self, messages: list[dict[str, str]], json_schema: dict[str, Any]
    ) -> OllamaResponse:
        self.planning_calls.append(messages)
        return OllamaResponse(self.plans.pop(0), {})


class _InvalidJsonThenPlanClient(_SequencedNaturalClient):
    def __init__(
        self,
        plan: dict[str, Any],
        classification: dict[str, Any] | None = None,
        explanation: str = "La query ha restituito il risultato richiesto.",
    ) -> None:
        super().__init__(
            plan,
            classification=classification,
            explanation=explanation,
        )
        self.invalid_json_raised = False

    def chat_json(
        self, messages: list[dict[str, str]], json_schema: dict[str, Any]
    ) -> OllamaResponse:
        self.planning_calls.append(messages)
        if not self.invalid_json_raised:
            self.invalid_json_raised = True
            raise OllamaInvalidResponseError("Ollama returned invalid JSON")
        return OllamaResponse(self.plans.pop(0), {})


def _mysql_records_plan(asset: Any) -> dict[str, Any]:
    return {
        "sources": [{"alias": "o", "asset_id": asset.asset_id}],
        "projections": [
            {"source_alias": "o", "field": field}
            for field in ("id", "customer_id", "status", "total", "created_at")
        ],
        "filters": [
            {"source_alias": "o", "field": "total", "operator": "gt", "value": 100}
        ],
    }


def _mysql_metric_plan(asset: Any, function: str, alias: str) -> dict[str, Any]:
    return {
        "sources": [{"alias": "o", "asset_id": asset.asset_id}],
        "aggregations": [{
            "function": function,
            "source_alias": "o",
            "field": "total",
            "alias": alias,
        }],
    }


def _mysql_paid_count_plan(asset: Any) -> dict[str, Any]:
    return {
        "sources": [{"alias": "o", "asset_id": asset.asset_id}],
        "filters": [{
            "source_alias": "o", "field": "status", "operator": "eq", "value": "paid"
        }],
        "aggregations": [{
            "function": "count", "source_alias": "o", "field": "id", "alias": "orders"
        }],
    }


def _mysql_row_plan(
    asset: Any,
    fields: tuple[str, ...],
    filter_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "sources": [{"alias": "o", "asset_id": asset.asset_id}],
        "projections": [
            {"source_alias": "o", "field": field} for field in fields
        ],
        "filters": (
            [{"source_alias": "o", **filter_item}]
            if filter_item is not None
            else []
        ),
    }


def _add_duckdb_orders(settings: Settings) -> None:
    stream = tempfile.SpooledTemporaryFile()
    stream.write(b"order_id,order_status,order_purchase_timestamp\na1,paid,2024-01-01 10:00:00\n")
    stream.seek(0)
    uploaded = asyncio.run(
        IngestionService(settings).ingest_upload(
            UploadFile(stream, filename="file_orders.csv"), logical_name="orders"
        )
    )
    ProcessingService(settings).prepare(
        uploaded.asset_id or "", uploaded.asset_version_id or ""
    )



def test_explanation_uses_deterministic_summary_when_llm_context_is_sampled(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings, _, assets = mysql_query_env
    client = _SequencedNaturalClient(
        _mysql_plan(assets),
        explanation="This explanation must never be requested.",
    )
    service = NaturalLanguageQueryService(settings, client=client)  # type: ignore[arg-type]
    rows = [[f"2024-{month:02d}-01", month] for month in range(1, 26)]

    class _CompleteResult:
        row_count = 25
        truncated = False

        @staticmethod
        def model_dump(mode: str = "json") -> dict[str, Any]:
            assert mode == "json"
            return {
                "columns": ["month", "orders"],
                "rows": rows,
                "row_count": 25,
                "truncated": False,
            }

    with caplog.at_level("INFO", logger="queryx.app.query.natural_language"):
        answer, warning = service._explain(
            "Quanti ordini ci sono per mese?", _CompleteResult()
        )

    assert warning is None
    assert answer == (
        "La query ha restituito 25 righe con le colonne `month`, `orders`. "
        "Consulta la tabella dei risultati per i valori completi."
    )
    assert client.prompts == []
    assert "15 result rows were omitted from the LLM context" in caplog.text


def test_explanation_prompt_defines_row_count_as_output_rows(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mysql_query_env
    client = _SequencedNaturalClient(
        _mysql_plan(assets),
        explanation="La query ha restituito due gruppi.",
    )
    service = NaturalLanguageQueryService(settings, client=client)  # type: ignore[arg-type]

    class _SmallGroupedResult:
        row_count = 2
        truncated = False

        @staticmethod
        def model_dump(mode: str = "json") -> dict[str, Any]:
            assert mode == "json"
            return {
                "columns": ["status", "orders"],
                "rows": [["paid", 12], ["pending", 5]],
                "row_count": 2,
                "truncated": False,
            }

    answer, warning = service._explain(
        "Quanti ordini ci sono per stato?", _SmallGroupedResult()
    )

    assert warning is None
    assert answer == "La query ha restituito due gruppi."
    system_prompt = client.prompts[-2]
    payload = json.loads(client.prompts[-1])
    assert payload["result_shape"] == "tabular"
    assert payload["row_count"] == 2
    assert payload["rows_omitted_from_prompt"] == 0
    assert "row_count always means the number of output rows" in system_prompt
    assert "never the total of a measure" in system_prompt


def test_explanation_marks_only_real_result_truncation(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mysql_query_env
    client = _SequencedNaturalClient(
        _mysql_plan(assets),
        explanation="La query ha restituito le righe disponibili.",
    )
    service = NaturalLanguageQueryService(settings, client=client)  # type: ignore[arg-type]

    class _TruncatedResult:
        row_count = 10
        truncated = True

        @staticmethod
        def model_dump(mode: str = "json") -> dict[str, Any]:
            assert mode == "json"
            return {
                "columns": ["id"],
                "rows": [[index] for index in range(10)],
                "row_count": 10,
                "truncated": True,
            }

    answer, warning = service._explain(
        "Mostra gli ordini disponibili", _TruncatedResult()
    )

    assert warning is None
    assert answer == (
        "La query ha restituito le righe disponibili. "
        "Il risultato mostrato è troncato."
    )
    payload = json.loads(client.prompts[-1])
    assert payload["rows_omitted_from_prompt"] == 0
    assert payload["result_truncated"] is True


def test_natural_language_mysql_hint_excludes_same_named_duckdb_schema(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mysql_query_env
    _add_duckdb_orders(settings)
    client = _SequencedNaturalClient(_mysql_records_plan(assets["orders"]))
    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Mostra gli ordini MySQL con totale maggiore di 100"
        )
    )

    catalog = json.loads(client.planning_calls[0][1]["content"])["catalog"]
    assert all(asset["backend"] == "mysql" for asset in catalog["assets"])
    mysql_orders = [
        asset for asset in catalog["assets"] if asset["logical_name"] == "orders"
    ]
    assert len(mysql_orders) == 1
    block = mysql_orders[0]
    assert block["backend"] == "mysql"
    assert block["source_id"] == settings.mysql_source_id
    assert block["source_name"] == settings.mysql_source_name
    assert {field["name"] for field in block["fields"]} >= {
        "id", "customer_id", "status", "total", "created_at", "notes"
    }
    assert "order_id" not in {field["name"] for field in block["fields"]}
    assert [item.field for item in response.normalized_plan.projections] == [
        "id", "customer_id", "status", "total", "created_at"
    ]


def test_italian_mysql_orders_count_by_status_is_catalog_disambiguated(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mysql_query_env
    _add_duckdb_orders(settings)
    expected = _mysql_plan(assets)
    client = _SequencedNaturalClient(
        expected,
        classification={
            "classification": "ambiguous",
            "reason": "Il termine stato potrebbe essere ambiguo.",
            "clarification_question": "Quale stato intendi?",
        },
    )

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Quanti ordini ci sono per stato nel database MySQL?"
        )
    )

    assert response.classification == "answerable"
    plan = response.normalized_plan
    assert plan is not None
    assert plan.sources[0].asset_id == assets["orders"].asset_id
    assert [(item.field, item.alias) for item in plan.projections] == [
        ("status", "status")
    ]
    assert [
        (item.function.value, item.field, item.alias) for item in plan.aggregations
    ] == [("count", "id", "orders")]
    assert [item.field for item in plan.group_by] == ["status"]
    serialized = json.dumps(plan.model_dump(mode="json"))
    assert "order_id" not in serialized and "order_status" not in serialized

    prompt_payload = json.loads(client.planning_calls[0][1]["content"])
    catalog_assets = prompt_payload["catalog"]["assets"]
    assert [asset["logical_name"] for asset in catalog_assets] == ["orders"]
    hint = catalog_assets[0]["semantic_field_hints"][0]
    assert hint["field"] == "status" and "stato" in hint["terms"]
    example = prompt_payload["catalog_scoped_resolution_examples"][0]["plan"]
    assert example["sources"][0]["asset_id"] == assets["orders"].asset_id
    assert example["group_by"] == [{"source_alias": "o", "field": "status"}]


@pytest.mark.parametrize(
    ("question", "function", "alias"),
    [
        (
            "Qual è il totale medio degli ordini nel database MySQL?",
            "avg",
            "average_total",
        ),
        (
            "Qual è il valore totale degli ordini nel database MySQL?",
            "sum",
            "total_value",
        ),
    ],
)
def test_mysql_total_metric_is_resolved_from_selected_asset_schema(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    question: str,
    function: str,
    alias: str,
) -> None:
    settings, _, assets = mysql_query_env
    expected = _mysql_metric_plan(assets["orders"], function, alias)
    client = _SequencedNaturalClient(
        expected,
        classification={
            "classification": "ambiguous",
            "reason": "Il termine totale potrebbe essere ambiguo.",
            "clarification_question": "Quale totale intendi?",
        },
    )

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question=question)
    )

    assert response.classification == "answerable"
    assert response.clarification_question is None
    plan = response.normalized_plan
    assert plan is not None and plan.sources[0].asset_id == assets["orders"].asset_id
    assert [(item.function.value, item.field, item.alias) for item in plan.aggregations] == [
        (function, "total", alias)
    ]
    assert plan.group_by == [] and plan.projections == []
    payload = json.loads(client.planning_calls[0][1]["content"])
    metric_hints = payload["catalog"]["assets"][0]["semantic_metric_hints"]
    assert any(
        hint["field"] == "total" and hint["aggregation"] == function
        for hint in metric_hints
    )


def test_mysql_paid_count_retries_missing_filter_and_explains_single_result(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, query_service, assets = mysql_query_env
    incorrect = _mysql_plan(assets)
    corrected = _mysql_paid_count_plan(assets["orders"])
    client = _SequencedNaturalClient(
        incorrect,
        corrected,
        explanation="C'è 1 ordine MySQL con stato paid.",
    )
    monkeypatch.setattr(
        query_service.mysql_executor,
        "execute",
        lambda compiled: (["orders"], [[1]], False, 1.5),
    )

    response = NaturalLanguageQueryService(
        settings, client=client, query_service=query_service  # type: ignore[arg-type]
    ).translate(
        NaturalLanguageQueryRequest(
            question="Quanti ordini MySQL hanno stato paid?", execute=True
        )
    )

    plan = response.normalized_plan
    assert plan is not None
    assert plan.group_by == [] and plan.projections == []
    assert len(plan.filters) == 1
    assert (
        plan.filters[0].field,
        plan.filters[0].operator.value,
        plan.filters[0].value,
    ) == ("status", "eq", "paid")
    assert [(item.function.value, item.field, item.alias) for item in plan.aggregations] == [
        ("count", "id", "orders")
    ]
    assert response.result is not None and response.result.rows == [[1]]
    assert response.answer == "C'è 1 ordine MySQL con stato paid."
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "missing_explicit_filter"
    assert feedback["semantic_requirements"]["filter"] == {
        "field": "status", "operator": "eq", "value": "paid"
    }
    assert "Remove category projections and group_by" in feedback["instruction"]


def test_natural_language_field_not_found_retry_receives_exact_mysql_schema(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mysql_query_env
    _add_duckdb_orders(settings)
    invalid = _mysql_records_plan(assets["orders"])
    invalid["projections"][0]["field"] = "order_id"
    corrected = _mysql_records_plan(assets["orders"])
    client = _SequencedNaturalClient(invalid, corrected)

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Mostra gli ordini MySQL con totale maggiore di 100"
        )
    )

    assert response.normalized_plan.sources[0].asset_id == assets["orders"].asset_id
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "field_not_found"
    assert feedback["selected_assets"][0]["asset_id"] == assets["orders"].asset_id
    assert set(feedback["selected_assets"][0]["valid_fields"]) >= {
        "id", "customer_id", "status", "total", "created_at", "notes"
    }
    assert "order_id" not in feedback["selected_assets"][0]["valid_fields"]
    assert "same name" in feedback["instruction"]


def test_natural_language_same_name_without_backend_keeps_asset_schemas_atomic(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mysql_query_env
    _add_duckdb_orders(settings)
    client = _SequencedNaturalClient(_mysql_records_plan(assets["orders"]))
    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question="Show orders with total greater than 100")
    )

    catalog = json.loads(client.planning_calls[0][1]["content"])["catalog"]
    order_blocks = [
        asset for asset in catalog["assets"] if asset["logical_name"] == "orders"
    ]
    assert {asset["backend"] for asset in order_blocks} == {"duckdb", "mysql"}
    by_backend = {
        asset["backend"]: {field["name"] for field in asset["fields"]}
        for asset in order_blocks
    }
    assert "order_id" in by_backend["duckdb"] and "order_id" not in by_backend["mysql"]
    assert "total" in by_backend["mysql"] and "total" not in by_backend["duckdb"]
    assert response.normalized_plan.sources[0].asset_id == assets["orders"].asset_id


@pytest.mark.parametrize(
    ("question", "fields", "filter_item"),
    [
        (
            "Mostra id, stato e totale degli ordini MySQL",
            ("id", "status", "total"),
            None,
        ),
        (
            "Mostra gli ordini MySQL con totale inferiore a 100",
            ("id", "customer_id", "status", "total", "created_at"),
            {"field": "total", "operator": "lt", "value": 100},
        ),
        (
            "Mostra gli ordini MySQL con stato pending",
            ("id", "customer_id", "status", "total", "created_at"),
            {"field": "status", "operator": "eq", "value": "pending"},
        ),
    ],
)
def test_natural_language_mysql_row_intent_uses_only_projections_and_filter(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    question: str,
    fields: tuple[str, ...],
    filter_item: dict[str, Any] | None,
) -> None:
    settings, _, assets = mysql_query_env
    client = _SequencedNaturalClient(
        _mysql_row_plan(assets["orders"], fields, filter_item)
    )

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question=question)
    )

    plan = response.normalized_plan
    assert plan is not None
    assert [projection.field for projection in plan.projections] == list(fields)
    assert plan.aggregations == [] and plan.group_by == []
    expected_filters = [] if filter_item is None else [filter_item]
    assert [
        {
            "field": item.field,
            "operator": item.operator.value,
            "value": item.value,
        }
        for item in plan.filters
    ] == expected_filters
    assert len(client.planning_calls) == 1


def test_natural_language_mysql_row_intent_retry_preserves_intent(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mysql_query_env
    incorrect = _mysql_paid_count_plan(assets["orders"])
    incorrect["filters"][0]["value"] = "pending"
    corrected = _mysql_row_plan(
        assets["orders"],
        ("id", "customer_id", "status", "total", "created_at"),
        {"field": "status", "operator": "eq", "value": "pending"},
    )
    client = _SequencedNaturalClient(incorrect, corrected)

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Mostra gli ordini MySQL con stato pending"
        )
    )

    assert response.normalized_plan is not None
    assert response.normalized_plan.aggregations == []
    feedback = json.loads(client.planning_calls[1][-1]["content"])
    assert feedback["validation_code"] == "row_intent_mismatch"
    assert feedback["query_intent"] == "row_returning"
    assert "row-returning" in feedback["instruction"]
    assert feedback["validation_code"] != "unrequested_categories"


@pytest.mark.parametrize(
    ("question", "filter_item"),
    [
        (
            "Mostra gli ordini MySQL con totale inferiore a 100",
            {"field": "total", "operator": "lt", "value": 100},
        ),
        (
            "Mostra gli ordini MySQL con stato pending",
            {"field": "status", "operator": "eq", "value": "pending"},
        ),
    ],
)
def test_natural_language_mysql_filtered_rows_return_matching_order(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    filter_item: dict[str, Any],
) -> None:
    settings, query_service, assets = mysql_query_env
    fields = ("id", "customer_id", "status", "total", "created_at")
    client = _SequencedNaturalClient(
        _mysql_row_plan(assets["orders"], fields, filter_item),
        explanation="L'ordine richiesto ha id 2.",
    )
    monkeypatch.setattr(
        query_service.mysql_executor,
        "execute",
        lambda compiled: (
            list(fields),
            [[2, 20, "pending", 75, "2024-01-02 10:00:00"]],
            False,
            1.0,
        ),
    )

    response = NaturalLanguageQueryService(
        settings, client=client, query_service=query_service  # type: ignore[arg-type]
    ).translate(NaturalLanguageQueryRequest(question=question, execute=True))

    assert response.result is not None
    assert response.result.rows[0][0] == 2
    assert response.normalized_plan is not None
    assert response.normalized_plan.aggregations == []


def test_natural_language_mysql_count_filter_and_consecutive_request_isolation(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, query_service, assets = mysql_query_env
    row_plan = _mysql_row_plan(
        assets["orders"], ("id", "status", "total")
    )
    count_plan = _mysql_paid_count_plan(assets["orders"])
    count_plan["filters"][0]["value"] = "pending"
    client = _SequencedNaturalClient(
        row_plan,
        count_plan,
        explanation="È presente 1 ordine MySQL con stato pending.",
    )
    monkeypatch.setattr(
        query_service.mysql_executor,
        "execute",
        lambda compiled: (["orders"], [[1]], False, 1.0),
    )
    service = NaturalLanguageQueryService(
        settings, client=client, query_service=query_service  # type: ignore[arg-type]
    )

    first = service.translate(
        NaturalLanguageQueryRequest(
            question="Mostra id, stato e totale degli ordini MySQL"
        )
    )
    second = service.translate(
        NaturalLanguageQueryRequest(
            question="Quanti ordini MySQL hanno stato pending?", execute=True
        )
    )

    assert first.normalized_plan is not None
    assert first.normalized_plan.aggregations == []
    assert second.normalized_plan is not None
    assert second.normalized_plan.projections == []
    assert second.normalized_plan.group_by == []
    assert [item.function.value for item in second.normalized_plan.aggregations] == [
        "count"
    ]
    assert second.result is not None and second.result.rows == [[1]]
    assert second.answer == "È presente 1 ordine MySQL con stato pending."


def test_natural_language_context_includes_ready_mysql_asset_without_connection_details(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mysql_query_env
    plan = _mysql_plan(assets)
    client = _NaturalClient(plan)
    service = NaturalLanguageQueryService(settings, client=client)  # type: ignore[arg-type]
    response = service.translate(
        NaturalLanguageQueryRequest(question="Quanti ordini ci sono per stato?")
    )
    prompt = "\n".join(client.prompts)
    assert response.normalized_plan is not None
    assert response.normalized_plan.sources[0].asset_id == assets["orders"].asset_id
    assert assets["orders"].asset_id in prompt and '"backend":"mysql"' in prompt
    assert "status" in prompt
    assert settings.mysql_url not in prompt
    assert settings.mysql_host not in prompt
    assert settings.mysql_password not in prompt
    assert settings.mysql_database not in prompt


def _duckdb_orders_asset(settings: Settings) -> Any:
    return next(
        asset
        for asset in IngestionStorage(settings.catalog_db_path).list_assets()
        if asset.name == "orders" and str(asset.asset_kind) == "file"
    )


def _duckdb_status_count_plan(asset: Any) -> dict[str, Any]:
    return {
        "sources": [{"alias": "o", "asset_id": asset.id}],
        "projections": [{
            "source_alias": "o", "field": "order_status", "alias": "status",
        }],
        "aggregations": [{
            "function": "count", "source_alias": "o",
            "field": "order_id", "alias": "orders",
        }],
        "group_by": [{"source_alias": "o", "field": "order_status"}],
    }


@pytest.mark.parametrize("question", [
    "Quanti ordini ci sono per order_status nel dataset CSV orders?",
    (
        "Nel dataset CSV orders, raggruppa gli ordini contando gli order_id "
        "per ciascun order_status"
    ),
    "Quanti ordini ci sono per order_status nel file orders?",
    "Quanti ordini ci sono per order_status in DuckDB?",
])
def test_duckdb_source_signal_excludes_same_named_mysql_asset(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    question: str,
) -> None:
    settings, _, mysql_assets = mysql_query_env
    _add_duckdb_orders(settings)
    duckdb_asset = _duckdb_orders_asset(settings)
    client = _SequencedNaturalClient(_duckdb_status_count_plan(duckdb_asset))

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question=question)
    )

    catalog = json.loads(client.planning_calls[0][1]["content"])["catalog"]
    assert [asset["backend"] for asset in catalog["assets"]] == ["duckdb"]
    assert mysql_assets["orders"].asset_id not in json.dumps(catalog)
    assert response.normalized_plan.sources[0].asset_id == duckdb_asset.id


def test_duckdb_monthly_count_has_a_bounded_exact_planning_example(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, _ = mysql_query_env
    _add_duckdb_orders(settings)
    asset = _duckdb_orders_asset(settings)
    plan = {
        "sources": [{"alias": "o", "asset_id": asset.id}],
        "projections": [{
            "source_alias": "o", "field": "order_purchase_timestamp",
            "transform": "date_trunc_month", "alias": "month",
        }],
        "aggregations": [{
            "function": "count", "source_alias": "o",
            "field": "order_id", "alias": "orders",
        }],
        "group_by": [{
            "source_alias": "o", "field": "order_purchase_timestamp",
            "transform": "date_trunc_month",
        }],
        "order_by": [{"field": "month", "direction": "asc"}],
    }
    client = _SequencedNaturalClient(plan)

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question=(
                "Quanti ordini del dataset CSV orders ci sono per mese di "
                "order_purchase_timestamp?"
            )
        )
    )

    payload = json.loads(client.planning_calls[0][1]["content"])
    assert payload["duckdb_monthly_count_example"]["plan"] == plan
    assert response.normalized_plan.projections[0].transform == "date_trunc_month"
    assert response.normalized_plan.limit == settings.query_default_limit


def _invalid_duckdb_monthly_count_plan(asset: Any) -> dict[str, Any]:
    return {
        "sources": [{"alias": "o", "asset_id": asset.id}],
        "projections": [],
        "aggregations": [{
            "function": "count",
            "source_alias": "o",
            "field": "order_id",
            "alias": "orders",
        }],
        "group_by": [{
            "source_alias": "o",
            "field": "order_purchase_timestamp",
            "transform": "date_trunc_month",
        }],
        "order_by": [],
        "limit": None,
    }


def test_duckdb_monthly_count_canonicalizes_without_llm_retry(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings, query_service, _ = mysql_query_env
    _add_duckdb_orders(settings)
    asset = _duckdb_orders_asset(settings)
    client = _SequencedNaturalClient(
        _invalid_duckdb_monthly_count_plan(asset)
    )

    with caplog.at_level("INFO", logger="queryx.app.query.natural_language"):
        response = NaturalLanguageQueryService(
            settings,
            client=client,  # type: ignore[arg-type]
            query_service=query_service,
        ).translate(NaturalLanguageQueryRequest(
            question=(
                "Quanti ordini del dataset CSV orders ci sono per mese di "
                "order_purchase_timestamp?"
            ),
            execute=True,
        ))

    assert len(client.planning_calls) == 1
    assert response.normalized_plan is not None
    assert response.normalized_plan.projections[0].source_alias == "o"
    assert response.normalized_plan.projections[0].field == "order_purchase_timestamp"
    assert response.normalized_plan.projections[0].transform == "date_trunc_month"
    assert response.normalized_plan.projections[0].alias == "month"
    assert response.normalized_plan.group_by[0].source_alias == "o"
    assert response.normalized_plan.group_by[0].field == "order_purchase_timestamp"
    assert response.normalized_plan.group_by[0].transform == "date_trunc_month"
    assert response.normalized_plan.aggregations[0].function == "count"
    assert response.normalized_plan.aggregations[0].field == "order_id"
    assert response.normalized_plan.limit == settings.query_default_limit
    query_service.validate(response.normalized_plan)
    assert response.result is not None
    assert response.result.rows == [["2024-01-01", 1]]
    assert "validation_code=invalid_group_by" in caplog.text
    assert "Canonicalized grouped retry" in caplog.text


def test_duckdb_monthly_count_canonicalizes_after_json_retry_without_third_call(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings, query_service, _ = mysql_query_env
    _add_duckdb_orders(settings)
    asset = _duckdb_orders_asset(settings)
    client = _InvalidJsonThenPlanClient(
        _invalid_duckdb_monthly_count_plan(asset)
    )

    with caplog.at_level("INFO", logger="queryx.app.query.natural_language"):
        response = NaturalLanguageQueryService(
            settings,
            client=client,  # type: ignore[arg-type]
            query_service=query_service,
        ).translate(NaturalLanguageQueryRequest(
            question=(
                "Quanti ordini del dataset CSV orders ci sono per mese di "
                "order_purchase_timestamp?"
            ),
            execute=True,
        ))

    assert client.invalid_json_raised is True
    assert len(client.planning_calls) == 2
    assert response.normalized_plan is not None
    assert response.normalized_plan.projections[0].field == "order_purchase_timestamp"
    assert response.normalized_plan.projections[0].transform == "date_trunc_month"
    assert response.normalized_plan.projections[0].alias == "month"
    assert response.normalized_plan.group_by[0].field == "order_purchase_timestamp"
    assert response.normalized_plan.group_by[0].transform == "date_trunc_month"
    assert response.normalized_plan.aggregations[0].field == "order_id"
    assert response.normalized_plan.limit == settings.query_default_limit
    query_service.validate(response.normalized_plan)
    assert response.result is not None
    assert response.result.rows == [["2024-01-01", 1]]
    assert "Canonicalized grouped retry" in caplog.text


def test_duckdb_monthly_count_candidate_shape_fallback_when_requirements_empty(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings, query_service, _ = mysql_query_env
    _add_duckdb_orders(settings)
    asset = _duckdb_orders_asset(settings)
    client = _SequencedNaturalClient(
        _invalid_duckdb_monthly_count_plan(asset)
    )
    monkeypatch.setattr(
        NaturalLanguageQueryService,
        "_semantic_requirements",
        staticmethod(lambda question, context: {}),
    )

    with caplog.at_level("INFO", logger="queryx.app.query.natural_language"):
        response = NaturalLanguageQueryService(
            settings,
            client=client,  # type: ignore[arg-type]
            query_service=query_service,
        ).translate(NaturalLanguageQueryRequest(
            question=(
                "Quanti ordini del dataset CSV orders ci sono per mese di "
                "order_purchase_timestamp?"
            ),
            execute=True,
        ))

    assert len(client.planning_calls) == 1
    assert response.normalized_plan is not None
    assert response.normalized_plan.projections[0].alias == "month"
    assert response.normalized_plan.group_by[0].transform == "date_trunc_month"
    assert response.normalized_plan.aggregations[0].field == "order_id"
    assert response.result is not None
    assert response.result.rows == [["2024-01-01", 1]]
    assert "repair_source=candidate_shape" in caplog.text


def test_natural_language_mysql_top_k_uses_order_and_limit_without_filter(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings, _, assets = mysql_query_env
    projections = [
        {"source_alias": "o", "field": field}
        for field in ("id", "customer_id", "status", "total", "created_at")
    ]
    corrected = {
        "sources": [{"alias": "o", "asset_id": assets["orders"].asset_id}],
        "projections": projections,
        "filters": [],
        "order_by": [{"field": "total", "direction": "desc"}],
        "limit": 5,
    }
    invalid = {
        **corrected,
        "filters": [{
            "source_alias": "o",
            "field": "total",
            "operator": "gt",
            "value": 5,
        }],
    }
    client = _SequencedNaturalClient(invalid, corrected)

    with caplog.at_level("WARNING", logger="queryx.app.query.natural_language"):
        response = NaturalLanguageQueryService(
            settings, client=client  # type: ignore[arg-type]
        ).translate(NaturalLanguageQueryRequest(
            question="Mostra i cinque ordini MySQL con total più alto"
        ))

    retry = json.loads(client.planning_calls[1][-1]["content"])
    assert retry["validation_code"] == "row_filter_mismatch"
    assert "filter" not in retry["semantic_requirements"]
    assert retry["semantic_requirements"]["order_by"] == {
        "field": "total",
        "direction": "desc",
    }
    assert retry["semantic_requirements"]["limit"] == 5
    assert response.normalized_plan is not None
    assert response.normalized_plan.filters == []
    assert response.normalized_plan.order_by[0].field == "total"
    assert response.normalized_plan.order_by[0].direction == "desc"
    assert response.normalized_plan.limit == 5
    assert "validation_code=row_filter_mismatch" in caplog.text
    assert "'value': 5" not in caplog.text


def test_natural_language_mysql_regular_group_by_remains_valid(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, assets = mysql_query_env
    client = _SequencedNaturalClient(_mysql_plan(assets))

    response = NaturalLanguageQueryService(
        settings, client=client  # type: ignore[arg-type]
    ).translate(NaturalLanguageQueryRequest(
        question="Quanti ordini ci sono per stato nel database MySQL?"
    ))

    assert len(client.planning_calls) == 1
    assert response.normalized_plan is not None
    assert response.normalized_plan.projections[0].field == "status"
    assert response.normalized_plan.group_by[0].field == "status"


def test_duckdb_delivered_filter_is_required_and_retried_once(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, _ = mysql_query_env
    _add_duckdb_orders(settings)
    asset = _duckdb_orders_asset(settings)
    invalid = {
        "sources": [{"alias": "o", "asset_id": asset.id}],
        "projections": [
            {"source_alias": "o", "field": "order_id"},
            {"source_alias": "o", "field": "order_status"},
            {"source_alias": "o", "field": "order_purchase_timestamp"},
        ],
    }
    corrected = {
        **invalid,
        "filters": [{
            "source_alias": "o", "field": "order_status",
            "operator": "eq", "value": "delivered",
        }],
    }
    client = _SequencedNaturalClient(invalid, corrected)

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Mostra gli ordini con order_status delivered nel dataset CSV orders"
        )
    )

    assert [(item.field, item.value) for item in response.normalized_plan.filters] == [
        ("order_status", "delivered")
    ]
    assert len(client.planning_calls) == 2
    retry = json.loads(client.planning_calls[1][-1]["content"])
    assert retry["validation_code"] == "missing_explicit_filter"


def test_duckdb_explicit_projection_is_catalog_answerable(
    mysql_query_env: tuple[Settings, QueryService, dict[str, Any]],
) -> None:
    settings, _, _ = mysql_query_env
    _add_duckdb_orders(settings)
    asset = _duckdb_orders_asset(settings)
    plan = {
        "sources": [{"alias": "o", "asset_id": asset.id}],
        "projections": [
            {"source_alias": "o", "field": "order_id"},
            {"source_alias": "o", "field": "order_status"},
            {"source_alias": "o", "field": "order_purchase_timestamp"},
        ],
    }
    client = _SequencedNaturalClient(
        plan,
        classification={
            "classification": "ambiguous",
            "reason": "La sorgente potrebbe essere ambigua.",
            "clarification_question": "Quale sorgente intendi?",
        },
    )

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question=(
                "Mostra order_id, order_status e order_purchase_timestamp "
                "del dataset CSV orders"
            )
        )
    )

    assert response.classification == "answerable"
    assert [item.field for item in response.normalized_plan.projections] == [
        "order_id", "order_status", "order_purchase_timestamp"
    ]
