from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from pydantic import ValidationError

from queryx.app.catalog.models import (
    DataSource,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentRun,
    EntitySemanticAnnotation,
    FieldSemanticAnnotation,
    RunStatus,
    SourceScanResult,
)
from queryx.app.catalog.service import CatalogService
from queryx.app.core.config import Settings
from queryx.app.llm.ollama_client import (
    OllamaClient,
    OllamaError,
    OllamaInvalidResponseError,
    OllamaModelNotFoundError,
    OllamaResponse,
    OllamaTimeoutError,
    OllamaUnavailableError,
)
from queryx.app.llm.prompts.semantic_enrichment_v1 import PROMPT_VERSION, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

OUTPUT_SCHEMA_VERSION = "semantic-annotation-v1"


class SemanticEnrichmentService:
    def __init__(
        self,
        catalog: CatalogService,
        client: OllamaClient,
        settings: Settings,
    ) -> None:
        self.catalog = catalog
        self.client = client
        self.settings = settings

    def enrich_source(
        self,
        source: DataSource,
        request: EnrichmentRequest,
    ) -> EnrichmentRun:
        snapshot = self.catalog.latest_successful_source(source.id)
        if snapshot is None or snapshot.scan_run_id is None or snapshot.fingerprint is None:
            raise ValueError("No successful technical snapshot available")
        max_entities = request.max_entities or self.settings.queryx_enrichment_max_entities
        if not request.force:
            reusable = self.catalog.find_reusable_enrichment_run(
                source.id,
                snapshot.scan_run_id,
                snapshot.fingerprint,
                self.client.model,
                PROMPT_VERSION,
                OUTPUT_SCHEMA_VERSION,
            )
            if reusable is not None:
                return reusable

        started_at = datetime.now(timezone.utc)
        started = monotonic()
        errors: list[dict[str, Any]] = []
        warnings: list[str] = []
        results: list[EnrichmentResult] = []
        token_metrics: dict[str, Any] = {}
        request_count = 0
        retry_count = 0
        invalid_responses = 0

        try:
            self.client.ensure_model()
        except OllamaError as exc:
            return self._failed_run(source, snapshot, started_at, started, exc)

        for entity in self._entities(source, snapshot)[:max_entities]:
            try:
                result, calls, retries, metrics = self._enrich_entity(entity, request.language)
                request_count += calls
                retry_count += retries
                self._merge_metrics(token_metrics, metrics)
                results.append(result)
            except OllamaInvalidResponseError as exc:
                invalid_responses += 1
                errors.append({"code": "invalid_llm_response", "entity": entity["name"], "message": str(exc)})
            except OllamaError as exc:
                errors.append({"code": "llm_error", "entity": entity["name"], "message": str(exc)})
            except ValidationError as exc:
                invalid_responses += 1
                errors.append({"code": "semantic_validation_failed", "entity": entity["name"], "message": exc.errors()})

        finished_at = datetime.now(timezone.utc)
        failures = len(errors)
        status = self._status(len(results), failures)
        run = EnrichmentRun(
            source_id=source.id,
            source_snapshot_id=snapshot.scan_run_id,
            technical_fingerprint=snapshot.fingerprint,
            model_name=self.client.model,
            prompt_version=PROMPT_VERSION,
            output_schema_version=OUTPUT_SCHEMA_VERSION,
            created_at=started_at,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((monotonic() - started) * 1000),
            status=status,
            entities_processed=len(results),
            fields_processed=sum(len(result.fields) for result in results),
            failures=failures,
            token_metrics=token_metrics,
            warnings=warnings,
            errors=errors,
            request_count=request_count,
            retry_count=retry_count,
            invalid_responses=invalid_responses,
            results=results,
        )
        return self.catalog.save_enrichment_run(run)

    def _enrich_entity(self, entity: dict[str, Any], language: str) -> tuple[EnrichmentResult, int, int, dict[str, Any]]:
        fields = entity["fields"]
        batches = _chunks(fields, self.settings.queryx_enrichment_max_fields_per_request)
        entity_annotation: EntitySemanticAnnotation | None = None
        field_annotations: dict[str, FieldSemanticAnnotation] = {}
        warnings: list[str] = []
        unannotated: list[str] = []
        calls = 0
        retries = 0
        metrics: dict[str, Any] = {}

        for batch in batches:
            payload = {**entity, "fields": batch, "language": language}
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._compact_payload(payload)},
            ]
            response = self._call_with_retries(messages)
            calls += 1
            retries += response.retries
            self._merge_metrics(metrics, response.metrics)
            parsed = EnrichmentResult.model_validate(response.content)
            if entity_annotation is None:
                entity_annotation = parsed.entity
            for field in parsed.fields:
                field_annotations[field.field_path] = field
            warnings.extend(parsed.warnings)
            unannotated.extend(parsed.unannotated_fields)

        if entity_annotation is None:
            raise OllamaInvalidResponseError("No entity annotation returned")
        return (
            EnrichmentResult(
                entity=entity_annotation,
                fields=list(field_annotations.values()),
                warnings=warnings,
                unannotated_fields=sorted(set(unannotated)),
                output_schema_version=OUTPUT_SCHEMA_VERSION,
            ),
            calls,
            retries,
            metrics,
        )

    def _call_with_retries(self, messages: list[dict[str, str]]) -> OllamaResponse:
        attempts = self.settings.queryx_enrichment_max_retries + 1
        last_error: OllamaError | None = None
        for attempt in range(attempts):
            try:
                response = self.client.chat_json(messages, EnrichmentResult.model_json_schema())
                return OllamaResponse(response.content, response.metrics, retries=attempt)
            except OllamaError as exc:
                last_error = exc
                if attempt >= attempts - 1:
                    break
        raise last_error or OllamaUnavailableError("Ollama request failed")

    def _compact_payload(self, payload: dict[str, Any]) -> str:
        compact = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        if len(compact) > self.settings.queryx_enrichment_max_prompt_chars:
            compact = compact[: self.settings.queryx_enrichment_max_prompt_chars]
        logger.debug("Semantic enrichment prompt hash=%s chars=%s", hashlib.sha256(compact.encode()).hexdigest(), len(compact))
        return compact

    def _entities(self, source: DataSource, snapshot: SourceScanResult) -> list[dict[str, Any]]:
        if source.database_type == "mysql":
            return [
                {
                    "source_id": source.id,
                    "database_type": source.database_type,
                    "entity_kind": "table",
                    "name": table["name"],
                    "columns": [
                        {"name": column.get("name"), "type": column.get("type"), "nullable": column.get("nullable")}
                        for column in table.get("columns", [])
                    ],
                    "primary_key": table.get("primary_key", {}),
                    "foreign_keys": table.get("foreign_keys", []),
                    "indexes": table.get("indexes", []),
                    "profiling": _entity_metrics(snapshot.profiling_metrics, table["name"]),
                    "fields": [
                        {"field_path": column["name"], "type": column.get("type"), "nullable": column.get("nullable")}
                        for column in table.get("columns", [])
                    ],
                }
                for table in snapshot.declared_metadata.get("tables", [])
            ]
        inferred = {
            collection.get("name"): collection for collection in snapshot.inferred_metadata.get("collections", [])
        }
        entities = []
        for collection in snapshot.declared_metadata.get("collections", []):
            inferred_collection = inferred.get(collection["name"], {})
            entities.append(
                {
                    "source_id": source.id,
                    "database_type": source.database_type,
                    "entity_kind": "collection",
                    "name": collection["name"],
                    "indexes": collection.get("indexes", []),
                    "validator": collection.get("validator"),
                    "sample_size": inferred_collection.get("sample_size", 0),
                    "fields": [
                        {
                            "field_path": field.get("path"),
                            "types": field.get("types", []),
                            "sample_presence": field.get("presence"),
                        }
                        for field in inferred_collection.get("fields", [])
                    ],
                }
            )
        return entities

    def _failed_run(
        self,
        source: DataSource,
        snapshot: SourceScanResult,
        started_at: datetime,
        started: float,
        exc: Exception,
    ) -> EnrichmentRun:
        finished_at = datetime.now(timezone.utc)
        code = "llm_unavailable"
        if isinstance(exc, OllamaModelNotFoundError):
            code = "llm_model_not_found"
        elif isinstance(exc, OllamaTimeoutError):
            code = "llm_timeout"
        run = EnrichmentRun(
            source_id=source.id,
            source_snapshot_id=snapshot.scan_run_id or 0,
            technical_fingerprint=snapshot.fingerprint or "",
            model_name=self.client.model,
            prompt_version=PROMPT_VERSION,
            output_schema_version=OUTPUT_SCHEMA_VERSION,
            created_at=started_at,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((monotonic() - started) * 1000),
            status="failed",
            entities_processed=0,
            fields_processed=0,
            failures=1,
            errors=[{"code": code, "message": str(exc)}],
        )
        return self.catalog.save_enrichment_run(run)

    @staticmethod
    def _status(successes: int, failures: int) -> RunStatus:
        if successes == 0:
            return "failed"
        if failures > 0:
            return "partial"
        return "completed"

    @staticmethod
    def _merge_metrics(target: dict[str, Any], metrics: dict[str, Any]) -> None:
        for key, value in metrics.items():
            if isinstance(value, (int, float)) and value is not None:
                target[key] = target.get(key, 0) + value


def _chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)] or [[]]


def _entity_metrics(metrics: dict[str, Any], entity_name: str) -> dict[str, Any]:
    for entity in metrics.get("entities", []):
        if entity.get("name") == entity_name:
            return entity
    return {}
