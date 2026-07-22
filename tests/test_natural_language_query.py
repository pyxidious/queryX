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
    OllamaTextResponse,
    OllamaTimeoutError,
    OllamaUnavailableError,
)
from queryx.app.main import create_app
from queryx.app.processing.service import ProcessingService
from queryx.app.query.models import (
    AssetRelationshipCreate,
    NaturalLanguageQueryRequest,
    QueryExecutionResult,
)
from queryx.app.query.natural_language import (
    NaturalLanguageQueryError,
    NaturalLanguageQueryService,
)
from queryx.app.query.service import RelationshipService
from queryx.app.ui import routes as ui_routes


class StubOllamaClient:
    def __init__(
        self,
        *responses: dict[str, Any] | str | Exception,
        classifications: list[dict[str, Any] | Exception] | None = None,
    ) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []
        self.classifications = list(classifications or [])
        self.classification_calls: list[
            tuple[list[dict[str, str]], dict[str, Any]]
        ] = []

    def chat_json(
        self, messages: list[dict[str, str]], json_schema: dict[str, Any]
    ) -> OllamaResponse:
        self.calls.append((messages, json_schema))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, dict)
        return OllamaResponse(response, {})

    def chat_text(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any] | None = None,
    ) -> OllamaTextResponse:
        if json_schema and "classification" in json_schema.get("properties", {}):
            self.classification_calls.append((messages, json_schema))
            response = (
                self.classifications.pop(0)
                if self.classifications
                else {"classification": "answerable", "reason": "Catalog data is sufficient."}
            )
            if isinstance(response, Exception):
                raise response
            content = response if isinstance(response, str) else json.dumps(response)
            return OllamaTextResponse(content, {})
        self.calls.append((messages, {}))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, str)
        return OllamaTextResponse(response, {})


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
    assert response.classification == "answerable"
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


def test_classification_uses_catalog_before_answerable_planning(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, assets, _ = nl_env
    client = StubOllamaClient(_single_plan(assets))

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question="Quanti ordini ci sono per stato?")
    )

    assert response.classification == "answerable"
    assert response.normalized_plan is not None
    assert len(client.classification_calls) == 1 and len(client.calls) == 1
    classification_prompt = "\n".join(
        message["content"] for message in client.classification_calls[0][0]
    )
    assert assets["orders"].asset_id in classification_prompt
    assert "order_status" in classification_prompt
    assert "logical_query_plan_schema" not in classification_prompt
    assert "physical_location" not in classification_prompt
    assert 'Input: "Quali sono i clienti migliori?"' in classification_prompt
    assert "Per migliori intendi i clienti con più ordini" in classification_prompt


@pytest.mark.parametrize(
    "raw_classification",
    [
        json.dumps({
            "classification": "ambiguous",
            "reason": "Il criterio non è specificato.",
            "clarification_question": "Intendi più ordini o maggiore spesa?",
        }),
        json.dumps({
            "classification_result": {
                "classification": "ambiguous",
                "reason": "Il criterio non è specificato.",
                "clarification_question": "Intendi più ordini o maggiore spesa?",
            }
        }),
        """```json
        {"classification":"ambiguous","reason":"Il criterio non è specificato.","clarification_question":"Intendi più ordini o maggiore spesa?"}
        ```""",
    ],
)
def test_classification_parser_accepts_direct_wrapper_and_json_fence(
    nl_env: tuple[Settings, dict[str, Any], str], raw_classification: str,
) -> None:
    settings, _, _ = nl_env
    client = StubOllamaClient(classifications=[raw_classification])

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question="Quali sono i clienti migliori?")
    )

    assert response.classification == "ambiguous"
    assert response.clarification_question == "Intendi più ordini o maggiore spesa?"
    assert client.calls == []


@pytest.mark.parametrize(
    "raw_classification",
    [
        json.dumps({
            "classification": "ambiguous",
            "reason": "Il criterio non è specificato.",
            "clarification_question": "Quale criterio?",
            "extra": "not allowed",
        }),
        json.dumps({
            "classification": "ambiguous",
            "reason": "Il criterio non è specificato.",
        }),
        "prefix " + json.dumps({
            "classification": "answerable",
            "reason": "I dati sono disponibili.",
        }),
    ],
)
def test_classification_parser_rejects_extra_missing_and_surrounding_text(
    nl_env: tuple[Settings, dict[str, Any], str], raw_classification: str,
) -> None:
    settings, _, _ = nl_env
    client = StubOllamaClient(
        classifications=[raw_classification, raw_classification]
    )

    with pytest.raises(NaturalLanguageQueryError) as captured:
        NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
            NaturalLanguageQueryRequest(question="Quali sono i clienti migliori?")
        )

    assert captured.value.code == "invalid_classification"
    assert len(client.classification_calls) == 2
    assert client.calls == []


def test_classification_retry_can_recover_with_explicit_schema_error(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, _, _ = nl_env
    client = StubOllamaClient(classifications=[
        "not JSON",
        json.dumps({
            "classification": "ambiguous",
            "reason": "Il criterio non è specificato.",
            "clarification_question": "Quale criterio vuoi usare?",
        }),
    ])

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question="Quali sono i clienti migliori?")
    )

    assert response.classification == "ambiguous"
    assert len(client.classification_calls) == 2
    retry = json.loads(client.classification_calls[1][0][-1]["content"])
    assert retry["validation_error"] == "invalid_json"
    assert "classification_schema" in retry


@pytest.mark.parametrize(
    ("classification", "question", "reason", "clarification"),
    [
        (
            "ambiguous",
            "Quali sono i clienti migliori?",
            "Il criterio per stabilire i clienti migliori non è specificato.",
            "Per migliori intendi quelli con più ordini, maggiore spesa o altro?",
        ),
        (
            "unanswerable",
            "Qual è il profitto totale?",
            "Il catalogo contiene ricavi ma non dati di costo necessari per calcolare il profitto.",
            None,
        ),
    ],
)
def test_non_answerable_classification_skips_plan_and_execution(
    nl_env: tuple[Settings, dict[str, Any], str], monkeypatch: pytest.MonkeyPatch,
    classification: str, question: str, reason: str, clarification: str | None,
) -> None:
    settings, _, _ = nl_env
    payload: dict[str, Any] = {"classification": classification, "reason": reason}
    if clarification:
        payload["clarification_question"] = clarification
    client = StubOllamaClient(classifications=[payload])
    service = NaturalLanguageQueryService(settings, client=client)  # type: ignore[arg-type]
    monkeypatch.setattr(
        service.query_service,
        "execute",
        lambda plan: (_ for _ in ()).throw(AssertionError("execute must not be called")),
    )

    response = service.translate(
        NaturalLanguageQueryRequest(question=question, execute=True)
    )

    assert response.classification == classification
    assert response.normalized_plan is None and response.result is None
    assert response.reason == reason
    assert response.clarification_question == clarification
    assert response.answer == (reason if classification == "unanswerable" else None)
    assert client.calls == []


def test_invalid_classification_json_has_only_one_retry(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, _, _ = nl_env
    client = StubOllamaClient(
        classifications=[
            OllamaInvalidResponseError("invalid"),
            OllamaInvalidResponseError("invalid"),
        ]
    )

    with pytest.raises(NaturalLanguageQueryError) as captured:
        NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
            NaturalLanguageQueryRequest(question="orders by status")
        )

    assert captured.value.code == "invalid_classification"
    assert len(client.classification_calls) == 2
    assert client.calls == []


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

    client = StubOllamaClient(
        _single_plan(assets),
        "  Delivered has two orders and shipped has one.  ",
    )
    execute = NaturalLanguageQueryService(settings, client=client)  # type: ignore[arg-type]
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
    assert response.answer == "Delivered has two orders and shipped has one."
    assert response.planning_time_ms >= 0
    assert response.execution_time_ms == response.result.execution_time_ms
    assert response.explanation_time_ms is not None
    explanation_payload = json.loads(client.calls[1][0][1]["content"])
    assert set(explanation_payload) == {
        "question", "columns", "rows", "row_count", "truncated"
    }
    assert explanation_payload["question"] == "orders by status"
    assert "thinking" not in response.model_dump(mode="json")
    assert response.explanation_warning is None


def test_explanation_failure_does_not_fail_successful_query(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, assets, _ = nl_env
    client = StubOllamaClient(
        _single_plan(assets), OllamaUnavailableError("offline")
    )

    response = NaturalLanguageQueryService(settings, client=client).translate(  # type: ignore[arg-type]
        NaturalLanguageQueryRequest(question="orders by status", execute=True)
    )

    assert response.result is not None and response.result.row_count == 2
    assert response.answer is None
    assert response.explanation_time_ms is not None
    assert response.explanation_warning is not None
    assert response.explanation_warning.code == "explanation_unavailable"


def test_empty_explanation_content_returns_warning_and_keeps_result(
    nl_env: tuple[Settings, dict[str, Any], str],
) -> None:
    settings, assets, _ = nl_env
    response = NaturalLanguageQueryService(
        settings, client=StubOllamaClient(_single_plan(assets), "   ")  # type: ignore[arg-type]
    ).translate(NaturalLanguageQueryRequest(question="orders by status", execute=True))

    assert response.result is not None
    assert response.answer is None
    assert response.explanation_warning is not None
    assert response.explanation_warning.code == "explanation_unavailable"


def test_explanation_uses_bounded_rows_and_marks_truncation(
    nl_env: tuple[Settings, dict[str, Any], str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, assets, _ = nl_env
    client = StubOllamaClient(
        _single_plan(assets),
        "Prima frase. Seconda frase. Terza frase. Quarta frase.",
    )
    service = NaturalLanguageQueryService(settings, client=client)  # type: ignore[arg-type]
    result = QueryExecutionResult(
        columns=["status", "orders"],
        rows=[[f"status-{index}", index] for index in range(15)],
        row_count=15,
        truncated=True,
        execution_time_ms=1.5,
        plan_fingerprint="fingerprint",
    )
    monkeypatch.setattr(service.query_service, "execute", lambda plan: result)

    response = service.translate(
        NaturalLanguageQueryRequest(question="orders by status", execute=True)
    )

    explanation_payload = json.loads(client.calls[1][0][1]["content"])
    assert len(explanation_payload["rows"]) == 10
    assert response.answer == (
        "Prima frase. Seconda frase. Il risultato mostrato è troncato."
    )
    assert len(re.split(r"(?<=[.!?])\s+", response.answer)) == 3


def test_empty_result_has_a_clear_answer_without_an_llm_call(
    nl_env: tuple[Settings, dict[str, Any], str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, assets, _ = nl_env
    client = StubOllamaClient(_single_plan(assets))
    service = NaturalLanguageQueryService(settings, client=client)  # type: ignore[arg-type]
    monkeypatch.setattr(
        service.query_service,
        "execute",
        lambda plan: QueryExecutionResult(
            columns=["status", "orders"],
            rows=[],
            row_count=0,
            truncated=False,
            execution_time_ms=0.5,
            plan_fingerprint="fingerprint",
        ),
    )

    response = service.translate(
        NaturalLanguageQueryRequest(question="orders by status", execute=True)
    )

    assert response.answer == "La query non ha restituito risultati."
    assert len(client.calls) == 1


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

    api_execute_service = NaturalLanguageQueryService(
        settings,
        client=StubOllamaClient(  # type: ignore[arg-type]
            _single_plan(assets), "Delivered è lo stato più frequente."
        ),
    )
    monkeypatch.setattr(
        api_routes,
        "_natural_language_query_service",
        lambda settings=None: api_execute_service,
    )

    async def exercise_api_execute() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            return await client.post(
                "/query/natural-language",
                json={"question": "orders by status", "execute": True},
            )

    api_execute_response = asyncio.run(exercise_api_execute())
    assert api_execute_response.status_code == 200
    assert api_execute_response.json()["answer"] == "Delivered è lo stato più frequente."

    ui_service = NaturalLanguageQueryService(
        settings,
        client=StubOllamaClient(  # type: ignore[arg-type]
            _single_plan(assets),
            _single_plan(assets),
            "Delivered ha due ordini; shipped ne ha uno.",
        ),
    )
    monkeypatch.setattr(ui_routes, "NaturalLanguageQueryService", lambda settings: ui_service)

    async def exercise_ui() -> tuple[
        httpx.Response, httpx.Response, httpx.Response,
        httpx.Response, httpx.Response, httpx.Response,
    ]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            page = await client.get("/ui/query")
            token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)  # type: ignore[union-attr]
            planned = await client.post(
                "/ui/query/natural-language",
                data={
                    "csrf_token": token,
                    "question": "orders by status",
                    "execute": "false",
                },
            )
            generated = await client.post(
                "/ui/query/natural-language",
                data={
                    "csrf_token": token,
                    "question": "orders by status",
                    "execute": "true",
                },
            )
            plan_json = json.dumps(_single_plan(assets))
            validated = await client.post(
                "/ui/query/validate",
                data={"csrf_token": token, "plan_json": plan_json},
            )
            executed = await client.post(
                "/ui/query/execute",
                data={"csrf_token": token, "plan_json": plan_json},
            )
            script = await client.get("/ui/static/queryx-query.js")
            return page, planned, generated, validated, executed, script

    page, planned, generated, validated, executed, script = asyncio.run(exercise_ui())
    assert {
        page.status_code,
        planned.status_code,
        generated.status_code,
        validated.status_code,
        executed.status_code,
    } == {200}
    assert "Domanda in linguaggio naturale" in page.text
    assert "Genera piano" in page.text and "Genera ed esegui" in page.text
    assert "Piano generato e validato" in generated.text
    assert "order_status" in generated.text and "Risultato" in generated.text
    assert "Risposta" in generated.text
    assert "Delivered ha due ordini" in generated.text
    assert "Planning" in generated.text
    assert "Execution" in generated.text and "Explanation" in generated.text
    assert 'id="query-loading"' in page.text and 'aria-busy="false"' in page.text
    assert page.text.count("data-loading-text=") == 4
    assert 'action="/ui/query/natural-language"' in page.text
    assert 'action="/ui/query/validate"' in page.text
    assert 'formaction="/ui/query/execute"' in page.text
    assert "Generazione del piano in corso" in page.text
    assert "Validazione in corso" in page.text
    assert "Esecuzione della query in corso" in page.text
    assert script.status_code == 200
    assert "button.disabled = active" in script.text
    assert "if (busy) return" in script.text
    assert script.text.count("setBusy(false)") == 2
    assert script.text.index("event.preventDefault()") < script.text.index("setBusy(true")
    assert script.text.index("setBusy(true") < script.text.index("await fetch")
    assert 'document.addEventListener("submit", submit)' in script.text
    assert "workspace.replaceWith(replacement)" in script.text
    assert "document.write" not in script.text and ".submit()" not in script.text


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


def test_ui_renders_ambiguous_and_unanswerable_without_plan_actions(
    nl_env: tuple[Settings, dict[str, Any], str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _, _ = nl_env
    services = iter([
        NaturalLanguageQueryService(
            settings,
            client=StubOllamaClient(classifications=[{
                "classification": "ambiguous",
                "reason": "Il criterio non è specificato.",
                "clarification_question": "Per migliori intendi più ordini o maggiore spesa?",
            }]),  # type: ignore[arg-type]
        ),
        NaturalLanguageQueryService(
            settings,
            client=StubOllamaClient(classifications=[{
                "classification": "unanswerable",
                "reason": "Il profitto richiede dati di costo non presenti.",
            }]),  # type: ignore[arg-type]
        ),
    ])
    monkeypatch.setattr(
        ui_routes, "NaturalLanguageQueryService", lambda settings: next(services)
    )
    import queryx.app.main as main
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    async def exercise_ui() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            page = await client.get("/ui/query")
            token = re.search(
                r'name="csrf_token" value="([^"]+)"', page.text
            ).group(1)  # type: ignore[union-attr]
            ambiguous = await client.post(
                "/ui/query/natural-language",
                data={
                    "csrf_token": token,
                    "question": "Quali sono i clienti migliori?",
                    "execute": "true",
                },
            )
            unanswerable = await client.post(
                "/ui/query/natural-language",
                data={
                    "csrf_token": token,
                    "question": "Qual è il profitto totale?",
                    "execute": "true",
                },
            )
            return ambiguous, unanswerable

    ambiguous, unanswerable = asyncio.run(exercise_ui())
    assert ambiguous.status_code == unanswerable.status_code == 200
    assert "Chiarimento necessario" in ambiguous.text
    assert "Per migliori intendi più ordini o maggiore spesa?" in ambiguous.text
    assert "Quali sono i clienti migliori?" in ambiguous.text
    assert "Richiesta non calcolabile" in unanswerable.text
    assert "dati di costo non presenti" in unanswerable.text
    assert "Qual è il profitto totale?" in unanswerable.text
    for response in (ambiguous, unanswerable):
        assert ">Validate</button>" not in response.text
        assert ">Execute</button>" not in response.text


def test_api_returns_structured_ambiguous_classification_without_result(
    nl_env: tuple[Settings, dict[str, Any], str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _, _ = nl_env
    service = NaturalLanguageQueryService(
        settings,
        client=StubOllamaClient(classifications=[{
            "classification": "ambiguous",
            "reason": "Il criterio migliore non è definito.",
            "clarification_question": "Vuoi ordinare per spesa o numero di ordini?",
        }]),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        api_routes, "_natural_language_query_service", lambda settings=None: service
    )
    import queryx.app.main as main
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    async def exercise_api() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            return await client.post(
                "/query/natural-language",
                json={"question": "Quali sono i clienti migliori?", "execute": True},
            )

    response = asyncio.run(exercise_api())
    payload = response.json()
    assert response.status_code == 200
    assert payload["classification"] == "ambiguous"
    assert payload["clarification_question"] == "Vuoi ordinare per spesa o numero di ordini?"
    assert payload["normalized_plan"] is None
    assert payload["result"] is None
