from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from queryx.app.agent.orchestrator import ScanOrchestrator
from queryx.app.api import routes
from queryx.app.catalog.models import (
    DataSource,
    EnrichmentRequest,
    EnrichmentResult,
    EntitySemanticAnnotation,
    FieldSemanticAnnotation,
)
from queryx.app.catalog.service import CatalogService
from queryx.app.catalog.storage import CatalogStorage
from queryx.app.connectors.base import ConnectorError, MetadataConnector
from queryx.app.core.config import Settings
from queryx.app.llm.ollama_client import (
    OllamaInvalidResponseError,
    OllamaModelNotFoundError,
    OllamaResponse,
    OllamaTimeoutError,
    OllamaUnavailableError,
)
from queryx.app.llm.semantic_enrichment import SemanticEnrichmentService


class _Source(DataSource):
    pass


MYSQL_SOURCE = _Source(id="mysql", name="MySQL", database_type="mysql", host="secret-host", port=3306, database="db")


class _Connector(MetadataConnector):
    source = "mysql"
    source_id = "mysql"
    database_type = "mysql"

    def __init__(self, tables: list[dict[str, Any]]) -> None:
        self.tables = tables

    def health_check(self) -> dict[str, bool]:
        return {"ok": True}

    def scan(self):
        from queryx.app.catalog.models import SourceMetadata

        return SourceMetadata(source="mysql", database_type="mysql", declared={"tables": self.tables})


class _FailingConnector(_Connector):
    def scan(self):
        raise ConnectorError("down")


class _FakeOllama:
    model = "qwen3.5:9b"

    def __init__(self) -> None:
        self.calls = 0
        self.ensure_calls = 0
        self.fail_entities: set[str] = set()
        self.ensure_error: Exception | None = None
        self.chat_errors: list[Exception] = []
        self.prompts: list[str] = []

    def ensure_model(self) -> None:
        self.ensure_calls += 1
        if self.ensure_error is not None:
            raise self.ensure_error

    def chat_json(self, messages: list[dict[str, str]], json_schema: dict[str, Any]) -> OllamaResponse:
        self.calls += 1
        if self.chat_errors:
            raise self.chat_errors.pop(0)
        payload = messages[-1]["content"]
        self.prompts.append(payload)
        parsed = __import__("json").loads(payload)
        if parsed["name"] in self.fail_entities:
            raise OllamaInvalidResponseError("bad entity")
        fields = parsed.get("fields", [])
        content = {
            "entity": {
                "source_id": parsed["source_id"],
                "entity_name": parsed["name"],
                "entity_kind": parsed["entity_kind"],
                "description": "Tabella annotata",
                "business_domain": "unknown",
                "synonyms": [],
                "tags": ["catalog"],
                "confidence": 0.7,
                "confidence_source": "model_self_reported",
                "language": parsed["language"],
            },
            "fields": [
                {
                    "source_id": parsed["source_id"],
                    "entity_name": parsed["name"],
                    "field_path": field["field_path"],
                    "description": "Campo annotato",
                    "semantic_type": "unknown",
                    "business_terms": [],
                    "synonyms": [],
                    "unit": None,
                    "sensitivity": "unknown",
                    "confidence": 0.6,
                    "confidence_source": "model_self_reported",
                    "language": parsed["language"],
                }
                for field in fields
            ],
            "warnings": [],
            "unannotated_fields": [],
            "output_schema_version": "semantic-annotation-v1",
        }
        return OllamaResponse(content=content, metrics={"prompt_eval_count": 3, "eval_count": 5})


def _table(name: str, field_count: int = 2, field_type: str = "INTEGER") -> dict[str, Any]:
    return {
        "name": name,
        "columns": [
            {"name": f"field_{index}", "type": field_type, "nullable": index != 0}
            for index in range(field_count)
        ],
        "primary_key": {"columns": ["field_0"]},
        "foreign_keys": [],
        "indexes": [{"name": f"idx_{name}", "columns": ["field_0"], "unique": False}],
    }


def _catalog_with_scan(tmp_path: Path, tables: list[dict[str, Any]] | None = None) -> CatalogService:
    catalog = CatalogService(CatalogStorage(tmp_path / "catalog.sqlite3"))
    ScanOrchestrator([_Connector(tables or [_table("customers")])], catalog).scan()
    return catalog


def _settings(tmp_path: Path, **updates: Any) -> Settings:
    values = {"catalog_db_path": tmp_path / "x.sqlite3", **updates}
    return Settings(**values)


def test_enrichment_output_validates_against_schema() -> None:
    payload = {
        "entity": {
            "source_id": "mysql",
            "entity_name": "customers",
            "entity_kind": "table",
            "description": "Clienti",
            "business_domain": "crm",
            "synonyms": [],
            "tags": [],
            "confidence": 0.9,
            "language": "it",
        },
        "fields": [],
    }

    result = EnrichmentResult.model_validate(payload)

    assert result.entity.confidence_source == "model_self_reported"


def test_semantic_validation_rejects_bad_confidence_and_sensitivity() -> None:
    with pytest.raises(ValidationError):
        EntitySemanticAnnotation(
            source_id="mysql",
            entity_name="x",
            entity_kind="table",
            description="x",
            business_domain="x",
            confidence=1.5,
        )
    with pytest.raises(ValidationError):
        FieldSemanticAnnotation(
            source_id="mysql",
            entity_name="x",
            field_path="secret",
            description="x",
            semantic_type="unknown",
            sensitivity="pii",
            confidence=0.5,
        )


def test_enrichment_saves_annotations_separately_and_preserves_technical_metadata(tmp_path: Path) -> None:
    catalog = _catalog_with_scan(tmp_path)
    snapshot = catalog.latest_successful_source("mysql")
    assert snapshot is not None
    before = snapshot.declared_metadata.copy()
    service = SemanticEnrichmentService(catalog, _FakeOllama(), _settings(tmp_path))

    run = service.enrich_source(MYSQL_SOURCE, EnrichmentRequest())
    after = catalog.latest_successful_source("mysql")

    assert run.status == "completed"
    assert run.results[0].entity.entity_name == "customers"
    assert after is not None
    assert after.declared_metadata == before


def test_idempotency_reuses_completed_run_and_force_creates_new_run(tmp_path: Path) -> None:
    catalog = _catalog_with_scan(tmp_path)
    fake = _FakeOllama()
    service = SemanticEnrichmentService(catalog, fake, _settings(tmp_path))

    first = service.enrich_source(MYSQL_SOURCE, EnrichmentRequest())
    second = service.enrich_source(MYSQL_SOURCE, EnrichmentRequest())
    third = service.enrich_source(MYSQL_SOURCE, EnrichmentRequest(force=True))

    assert first.id == second.id
    assert second.reused_result is True
    assert third.id != first.id
    assert fake.calls == 2


def test_fingerprint_change_makes_semantic_current_stale_and_missing_before_enrichment(tmp_path: Path) -> None:
    catalog = _catalog_with_scan(tmp_path, [_table("customers", field_type="INTEGER")])
    service = SemanticEnrichmentService(catalog, _FakeOllama(), _settings(tmp_path))
    service.enrich_source(MYSQL_SOURCE, EnrichmentRequest())
    current_before = catalog.semantic_current([MYSQL_SOURCE])
    ScanOrchestrator([_Connector([_table("customers", field_type="BIGINT")])], catalog).scan()
    current_after = catalog.semantic_current([MYSQL_SOURCE])

    assert current_before["sources"][0]["semantic_status"] == "current"
    assert current_after["sources"][0]["semantic_status"] == "stale"

    empty_catalog = _catalog_with_scan(tmp_path / "empty")
    assert empty_catalog.semantic_current([MYSQL_SOURCE])["sources"][0]["semantic_status"] == "missing"


def test_get_semantic_current_does_not_call_ollama(tmp_path: Path) -> None:
    catalog = _catalog_with_scan(tmp_path)
    fake = _FakeOllama()

    catalog.semantic_current([MYSQL_SOURCE])

    assert fake.calls == 0


def test_max_entities_and_field_batching_are_respected(tmp_path: Path) -> None:
    catalog = _catalog_with_scan(tmp_path, [_table("one", 5), _table("two", 5)])
    fake = _FakeOllama()
    service = SemanticEnrichmentService(
        catalog,
        fake,
        _settings(tmp_path, queryx_enrichment_max_entities=1, queryx_enrichment_max_fields_per_request=2),
    )

    run = service.enrich_source(MYSQL_SOURCE, EnrichmentRequest())

    assert run.entities_processed == 1
    assert run.fields_processed == 5
    assert fake.calls == 3


def test_prompt_excludes_samples_passwords_uris_and_hosts(tmp_path: Path) -> None:
    catalog = _catalog_with_scan(tmp_path)
    fake = _FakeOllama()
    service = SemanticEnrichmentService(catalog, fake, _settings(tmp_path))

    service.enrich_source(MYSQL_SOURCE, EnrichmentRequest())
    prompt = fake.prompts[0]

    assert "mysql://" not in prompt
    assert "password" not in prompt.lower()
    assert "secret-host" not in prompt
    assert "ada@example.com" not in prompt


def test_partial_and_failed_statuses_for_entity_failures(tmp_path: Path) -> None:
    catalog = _catalog_with_scan(tmp_path, [_table("good"), _table("bad")])
    fake = _FakeOllama()
    fake.fail_entities = {"bad"}
    service = SemanticEnrichmentService(catalog, fake, _settings(tmp_path))

    partial = service.enrich_source(MYSQL_SOURCE, EnrichmentRequest())

    assert partial.status == "partial"
    assert partial.failures == 1

    catalog_all_bad = _catalog_with_scan(tmp_path / "all_bad", [_table("bad")])
    fake_all_bad = _FakeOllama()
    fake_all_bad.fail_entities = {"bad"}
    failed = SemanticEnrichmentService(catalog_all_bad, fake_all_bad, _settings(tmp_path)).enrich_source(
        MYSQL_SOURCE,
        EnrichmentRequest(),
    )

    assert failed.status == "failed"


def test_ollama_errors_and_retry_limit_are_handled(tmp_path: Path) -> None:
    for error in (
        OllamaUnavailableError("offline"),
        OllamaModelNotFoundError("missing"),
        OllamaTimeoutError("timeout"),
    ):
        catalog = _catalog_with_scan(tmp_path / error.__class__.__name__)
        fake = _FakeOllama()
        fake.ensure_error = error
        run = SemanticEnrichmentService(catalog, fake, _settings(tmp_path)).enrich_source(MYSQL_SOURCE, EnrichmentRequest())
        assert run.status == "failed"

    catalog = _catalog_with_scan(tmp_path / "retry")
    fake = _FakeOllama()
    fake.chat_errors = [OllamaUnavailableError("temporary")]
    run = SemanticEnrichmentService(
        catalog,
        fake,
        _settings(tmp_path, queryx_enrichment_max_retries=1),
    ).enrich_source(MYSQL_SOURCE, EnrichmentRequest())

    assert run.status == "completed"
    assert run.retry_count == 1


def test_llm_health_available_and_unavailable() -> None:
    class _Healthy:
        def health(self) -> dict[str, Any]:
            return {"status": "ok", "reachable": True, "model": "qwen3.5:9b", "model_present": True}

    class _Down:
        def health(self) -> dict[str, Any]:
            return {"status": "unavailable", "reachable": False, "model": "qwen3.5:9b", "model_present": False}

    original = routes._ollama_client
    routes._ollama_client = lambda settings=None: _Healthy()  # type: ignore[assignment]
    assert routes.llm_health()["status"] == "ok"
    routes._ollama_client = lambda settings=None: _Down()  # type: ignore[assignment]
    try:
        assert routes.llm_health()["status"] == "unavailable"
    finally:
        routes._ollama_client = original
