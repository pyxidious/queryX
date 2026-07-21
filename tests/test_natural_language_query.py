from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import UploadFile

from queryx.app.api import routes as api_routes
from queryx.app.core.config import Settings
from queryx.app.ingestion.service import IngestionService
from queryx.app.llm.ollama_client import (
    OllamaInvalidResponseError,
    OllamaResponse,
    OllamaTimeoutError,
    OllamaUnavailableError,
)
from queryx.app.main import create_app
from queryx.app.processing.service import ProcessingService
from queryx.app.query.models import AssetRelationshipCreate, NaturalLanguageQueryRequest
from queryx.app.query.natural_language import (
    NaturalLanguageQueryError,
    NaturalLanguageQueryService,
)
from queryx.app.query.service import RelationshipService
from queryx.app.ui import routes as ui_routes


class StubOllamaClient:
    def __init__(self, *responses: dict[str, Any] | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []

    def chat_json(
        self, messages: list[dict[str, str]], json_schema: dict[str, Any]
    ) -> OllamaResponse:
        self.calls.append((messages, json_schema))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return OllamaResponse(response, {})


@pytest.fixture(scope="module")
def nl_env(tmp_path_factory: pytest.TempPathFactory) -> tuple[Settings, dict[str, Any], str]:
    data = tmp_path_factory.mktemp("natural-language") / "data"
    settings = Settings(
        catalog_db_path=data / "catalog.sqlite3",
        data_raw_dir=data / "raw",
        data_staging_dir=data / "staging",
        data_normalized_dir=data / "normalized",
        duckdb_path=data / "queryx.duckdb",
        duckdb_lock_path=data / "queryx.duckdb.lock",
        ollama_base_url="http://ollama.invalid:11434",
        ollama_model="mock-model",
        mysql_enabled=False,
        mongodb_enabled=False,
    )
    datasets = {
        "orders": (
            "order_id,order_status\n"
            "o1,delivered\n"
            "o2,shipped\n"
            "o3,delivered\n"
        ),
        "order_items": "order_id,product_id,price\no1,p1,10.5\no2,p2,20.0\no3,p1,5.0\n",
        "products": "product_id,product_category_name\np1,books\np2,toys\n",
    }
    ingestion = IngestionService(settings)
    processing = ProcessingService(settings)
    assets: dict[str, Any] = {}
    for name, content in datasets.items():
        stream = tempfile.SpooledTemporaryFile()
        stream.write(content.encode())
        stream.seek(0)
        uploaded = asyncio.run(
            ingestion.ingest_upload(UploadFile(stream, filename=f"{name}.csv"), logical_name=name)
        )
        processing.prepare(uploaded.asset_id or "", uploaded.asset_version_id or "")
        assets[name] = uploaded
    relationship = RelationshipService(settings).create(AssetRelationshipCreate(
        left_asset_id=assets["order_items"].asset_id,
        left_field="product_id",
        right_asset_id=assets["products"].asset_id,
        right_field="product_id",
        relationship_type="many_to_one",
    ))
    return settings, assets, relationship.id


def _single_plan(assets: dict[str, Any]) -> dict[str, Any]:
    return {
        "sources": [{"alias": "o", "asset_id": assets["orders"].asset_id}],
        "projections": [
            {"source_alias": "o", "field": "order_status", "alias": "status"}
        ],
        "aggregations": [
            {"function": "count", "source_alias": "o", "field": "order_id", "alias": "orders"}
        ],
        "group_by": [{"source_alias": "o", "field": "order_status"}],
        "order_by": [{"field": "orders", "direction": "desc"}],
    }


def _join_plan(assets: dict[str, Any], relationship_id: str) -> dict[str, Any]:
    return {
        "sources": [
            {"alias": "oi", "asset_id": assets["order_items"].asset_id},
            {"alias": "p", "asset_id": assets["products"].asset_id},
        ],
        "joins": [{
            "relationship_id": relationship_id,
            "left_alias": "oi",
            "right_alias": "p",
        }],
        "projections": [{
            "source_alias": "p", "field": "product_category_name", "alias": "category"
        }],
        "aggregations": [{
            "function": "sum", "source_alias": "oi", "field": "price", "alias": "revenue"
        }],
        "group_by": [{"source_alias": "p", "field": "product_category_name"}],
        "order_by": [{"field": "revenue", "direction": "desc"}],
    }


def _invalid_group_plan(assets: dict[str, Any]) -> dict[str, Any]:
    plan = _single_plan(assets)
    plan["group_by"] = []
    return plan


def test_valid_single_source_plan_and_safe_relevant_prompt(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, assets, _ = nl_env
    client = StubOllamaClient(_single_plan(assets))
    service = NaturalLanguageQueryService(settings, client=client)  # type: ignore[arg-type]

    response = service.translate(NaturalLanguageQueryRequest(question="orders by status"))

    assert response.normalized_plan.limit == settings.query_default_limit
    assert response.result is None
    prompt = "\n".join(message["content"] for message in client.calls[0][0])
    assert assets["orders"].asset_id in prompt
    assert "order_status" in prompt and "logical_query_plan_schema" in prompt
    assert "physical_location" not in prompt and "serving_schema" not in prompt
    assert "queryx_managed" not in prompt and "/data/" not in prompt
    assert "sample" not in prompt.casefold() and '"rows"' not in prompt
    assert "sql" not in prompt.casefold()
    assert "every non-aggregated projection must appear identically in group_by" in prompt
    assert "without wrappers" in prompt
    assert '"field":"order_status"' in prompt
    assert '"function":"count"' in prompt
    assert service.client is client
    assert NaturalLanguageQueryService(settings).client.temperature == 0


def test_exact_logical_query_plan_wrapper_is_unwrapped(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, assets, _ = nl_env
    plan = _single_plan(assets)
    response = NaturalLanguageQueryService(
        settings,
        client=StubOllamaClient({"logical_query_plan": plan}),  # type: ignore[arg-type]
    ).translate(NaturalLanguageQueryRequest(question="orders by status"))

    assert response.normalized_plan.sources[0].asset_id == assets["orders"].asset_id
    assert response.normalized_plan.group_by[0].field == "order_status"


@pytest.mark.parametrize(
    "candidate",
    [
        {"logical_query_plan": {}, "comment": "extra"},
        {"plan": {}},
    ],
)
def test_non_exact_or_arbitrary_wrappers_are_rejected(
    nl_env: tuple[Settings, dict[str, Any], str], candidate: dict[str, Any],
) -> None:
    settings, _, _ = nl_env
    with pytest.raises(NaturalLanguageQueryError) as captured:
        NaturalLanguageQueryService(
            settings, client=StubOllamaClient(candidate)  # type: ignore[arg-type]
        ).translate(NaturalLanguageQueryRequest(question="orders by status"))

    assert captured.value.code == "invalid_logical_plan"


def test_unwrapped_plan_still_passes_semantic_validation(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, assets, _ = nl_env
    invalid = _invalid_group_plan(assets)
    client = StubOllamaClient(
        {"logical_query_plan": invalid},
        {"logical_query_plan": invalid},
    )

    with pytest.raises(NaturalLanguageQueryError) as captured:
        NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
            NaturalLanguageQueryRequest(question="orders by status")
        )

    assert captured.value.code == "invalid_logical_plan"
    assert captured.value.candidate_plan == invalid
    assert "logical_query_plan" not in captured.value.candidate_plan


def test_valid_join_plan_contains_only_active_declared_relationship(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, assets, relationship_id = nl_env
    client = StubOllamaClient(_join_plan(assets, relationship_id))
    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question="revenue by product category")
    )
    prompt = client.calls[0][0][1]["content"]
    assert response.normalized_plan.joins[0].relationship_id == relationship_id
    assert relationship_id in prompt
    assert "product_category_name" in prompt

    full_prompt = "\n".join(message["content"] for message in client.calls[0][0])
    assert "minimum number of assets and joins" in full_prompt
    assert "do not add unrequested metrics" in full_prompt
    assert "must not be duplicated in projections" in full_prompt
    assert "must never be used as source_alias" in full_prompt
    assert "must exactly match an existing projection output name or aggregation alias" in full_prompt
    prompt_payload = json.loads(client.calls[0][0][1]["content"])
    example = prompt_payload["correct_multi_asset_revenue_example"]
    assert [source["alias"] for source in example["sources"]] == ["p", "oi"]
    assert example["aggregations"] == [{
        "function": "sum", "source_alias": "oi", "field": "price", "alias": "revenue"
    }]
    assert example["order_by"] == [{"field": "revenue", "direction": "desc"}]
    assert all("orders" not in source["asset_id"] for source in example["sources"])


def test_invalid_join_order_retry_requests_a_minimal_correct_plan(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, assets, relationship_id = nl_env
    invalid = _join_plan(assets, relationship_id)
    invalid["joins"][0]["left_alias"] = "p"
    invalid["joins"][0]["right_alias"] = "oi"
    corrected = _join_plan(assets, relationship_id)
    client = StubOllamaClient(invalid, corrected)

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(
            question="Quali categorie di prodotto hanno generato più ricavi?"
        )
    )

    assert len(response.normalized_plan.sources) == 2
    assert len(response.normalized_plan.joins) == 1
    assert [item.alias for item in response.normalized_plan.aggregations] == ["revenue"]
    assert response.normalized_plan.order_by[0].field == "revenue"
    feedback = json.loads(client.calls[1][0][-1]["content"])
    assert feedback["validation_code"] == "invalid_join_order"
    instruction = feedback["instruction"]
    assert "remove unnecessary assets, joins, and metrics" in instruction
    assert "correct output aliases and join order" in instruction
    assert "return only the corrected plan object" in instruction


def test_invalid_json_gets_exactly_one_retry(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, assets, _ = nl_env
    client = StubOllamaClient(
        OllamaInvalidResponseError("invalid JSON"),
        _single_plan(assets),
    )
    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question="orders by status")
    )
    assert response.normalized_plan.sources[0].asset_id == assets["orders"].asset_id
    assert len(client.calls) == 2
    assert "previous response" in client.calls[1][0][-1]["content"].lower()


def test_invalid_json_after_retry_is_structured(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, _, _ = nl_env
    client = StubOllamaClient(
        OllamaInvalidResponseError("invalid JSON"),
        OllamaInvalidResponseError("invalid JSON"),
    )
    with pytest.raises(NaturalLanguageQueryError) as captured:
        NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
            NaturalLanguageQueryRequest(question="orders by status")
        )
    assert captured.value.code == "invalid_llm_json" and len(client.calls) == 2


def test_invalid_plan_and_ambiguous_question_are_rejected(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, assets, _ = nl_env
    invalid = _single_plan(assets)
    invalid["projections"][0]["field"] = "invented_field"
    with pytest.raises(NaturalLanguageQueryError) as plan_error:
        NaturalLanguageQueryService(
            settings, client=StubOllamaClient(invalid, invalid)  # type: ignore[arg-type]
        ).translate(
            NaturalLanguageQueryRequest(question="orders by status")
        )
    assert plan_error.value.code == "invalid_logical_plan"
    with pytest.raises(NaturalLanguageQueryError) as ambiguous:
        NaturalLanguageQueryService(
            settings, client=StubOllamaClient({"error": "ambiguous_question"})  # type: ignore[arg-type]
        ).translate(NaturalLanguageQueryRequest(question="show me the data"))
    assert ambiguous.value.code == "ambiguous_question"


def test_invalid_group_by_is_corrected_with_the_single_retry(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, assets, _ = nl_env
    invalid = _invalid_group_plan(assets)
    corrected = _single_plan(assets)
    client = StubOllamaClient(invalid, corrected)

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question="Quanti ordini ci sono per stato?")
    )

    assert response.normalized_plan.group_by[0].field == "order_status"
    assert len(client.calls) == 2
    feedback = json.loads(client.calls[1][0][-1]["content"])
    assert feedback["previous_plan"] == invalid
    assert feedback["validation_code"] == "invalid_group_by"
    assert "only the LogicalQueryPlan" in feedback["instruction"]


def test_second_invalid_plan_is_rejected_before_execution(
    nl_env: tuple[Settings, dict[str, Any], str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, assets, _ = nl_env
    first = _invalid_group_plan(assets)
    second = _invalid_group_plan(assets)
    client = StubOllamaClient(first, second)
    service = NaturalLanguageQueryService(settings, client=client)  # type: ignore[arg-type]
    monkeypatch.setattr(
        service.query_service,
        "execute",
        lambda plan: (_ for _ in ()).throw(AssertionError("invalid plan was executed")),
    )

    with pytest.raises(NaturalLanguageQueryError) as captured:
        service.translate(
            NaturalLanguageQueryRequest(
                question="Quanti ordini ci sono per stato?", execute=True
            )
        )

    assert captured.value.code == "invalid_logical_plan"
    assert captured.value.candidate_plan == second
    assert len(client.calls) == 2


def test_ollama_unavailable_is_structured(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, _, _ = nl_env
    with pytest.raises(NaturalLanguageQueryError) as captured:
        NaturalLanguageQueryService(
            settings, client=StubOllamaClient(OllamaUnavailableError("offline"))  # type: ignore[arg-type]
        ).translate(NaturalLanguageQueryRequest(question="orders by status"))
    assert captured.value.code == "llm_unavailable"


def test_ollama_timeout_is_distinct_from_unavailability(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, _, _ = nl_env
    with pytest.raises(NaturalLanguageQueryError) as captured:
        NaturalLanguageQueryService(
            settings, client=StubOllamaClient(OllamaTimeoutError("slow"))  # type: ignore[arg-type]
        ).translate(NaturalLanguageQueryRequest(question="orders by status"))
    assert captured.value.code == "llm_timeout"
    assert captured.value.status_code == 504


def test_execute_false_does_not_execute_and_true_uses_existing_service(
    nl_env: tuple[Settings, dict[str, Any], str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, assets, _ = nl_env
    generate = NaturalLanguageQueryService(settings, client=StubOllamaClient(_single_plan(assets)))  # type: ignore[arg-type]
    monkeypatch.setattr(
        generate.query_service,
        "execute",
        lambda plan: (_ for _ in ()).throw(AssertionError("execute must not be called")),
    )
    assert generate.translate(
        NaturalLanguageQueryRequest(question="orders by status", execute=False)
    ).result is None

    execute = NaturalLanguageQueryService(settings, client=StubOllamaClient(_single_plan(assets)))  # type: ignore[arg-type]
    original_execute = execute.query_service.execute
    called = 0

    def tracked(plan: Any) -> Any:
        nonlocal called
        called += 1
        return original_execute(plan)

    monkeypatch.setattr(execute.query_service, "execute", tracked)
    response = execute.translate(
        NaturalLanguageQueryRequest(question="orders by status", execute=True)
    )
    assert called == 1 and response.result is not None
    assert response.result.columns == ["status", "orders"]


def test_query_language_and_candidate_sql_are_never_accepted(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, _, _ = nl_env
    untouched = StubOllamaClient({"sql": "forbidden"})
    with pytest.raises(NaturalLanguageQueryError) as question_error:
        NaturalLanguageQueryService(settings, client=untouched).translate(  # type: ignore[arg-type]
            NaturalLanguageQueryRequest(question="SELECT * FROM orders")
        )
    assert question_error.value.code == "invalid_logical_plan" and untouched.calls == []
    with pytest.raises(NaturalLanguageQueryError) as candidate_error:
        NaturalLanguageQueryService(
            settings, client=StubOllamaClient({"sql": "forbidden"})  # type: ignore[arg-type]
        ).translate(NaturalLanguageQueryRequest(question="orders by status"))
    assert candidate_error.value.code == "invalid_logical_plan"


def test_api_and_ui_natural_language_flow(
    nl_env: tuple[Settings, dict[str, Any], str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, assets, _ = nl_env
    api_service = NaturalLanguageQueryService(
        settings, client=StubOllamaClient(_single_plan(assets))  # type: ignore[arg-type]
    )
    monkeypatch.setattr(api_routes, "_natural_language_query_service", lambda settings=None: api_service)
    import queryx.app.main as main
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    async def exercise_api() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            return await client.post(
                "/query/natural-language", json={"question": "orders by status"}
            )

    api_response = asyncio.run(exercise_api())
    assert api_response.status_code == 200
    assert "normalized_plan" in api_response.json() and "result" not in api_response.json()

    ui_service = NaturalLanguageQueryService(
        settings, client=StubOllamaClient(_single_plan(assets))  # type: ignore[arg-type]
    )
    monkeypatch.setattr(ui_routes, "NaturalLanguageQueryService", lambda settings: ui_service)

    async def exercise_ui() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            page = await client.get("/ui/query")
            token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)  # type: ignore[union-attr]
            generated = await client.post(
                "/ui/query/natural-language",
                data={
                    "csrf_token": token,
                    "question": "orders by status",
                    "execute": "true",
                },
            )
            return page, generated

    page, generated = asyncio.run(exercise_ui())
    assert page.status_code == generated.status_code == 200
    assert "Domanda in linguaggio naturale" in page.text
    assert "Genera piano" in page.text and "Genera ed esegui" in page.text
    assert "Piano generato e validato" in generated.text
    assert "order_status" in generated.text and "Risultato" in generated.text


def test_invalid_generated_plan_remains_visible_in_ui(
    nl_env: tuple[Settings, dict[str, Any], str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, assets, _ = nl_env
    invalid = _invalid_group_plan(assets)
    service = NaturalLanguageQueryService(
        settings,
        client=StubOllamaClient(  # type: ignore[arg-type]
            {"logical_query_plan": invalid},
            {"logical_query_plan": invalid},
        ),
    )
    monkeypatch.setattr(ui_routes, "NaturalLanguageQueryService", lambda settings: service)
    import queryx.app.main as main
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    async def exercise_ui() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            page = await client.get("/ui/query")
            token = re.search(
                r'name="csrf_token" value="([^"]+)"', page.text
            ).group(1)  # type: ignore[union-attr]
            return await client.post(
                "/ui/query/natural-language",
                data={
                    "csrf_token": token,
                    "question": "Quanti ordini ci sono per stato?",
                    "execute": "true",
                },
            )

    response = asyncio.run(exercise_ui())
    assert response.status_code == 422
    assert "invalid_logical_plan" in response.text
    assert "order_status" in response.text and "group_by" in response.text
    assert "logical_query_plan" not in response.text
