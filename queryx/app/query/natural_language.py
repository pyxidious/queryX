from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from queryx.app.core.config import Settings
from queryx.app.ingestion.models import BindingRole, BindingStatus
from queryx.app.ingestion.storage import IngestionStorage
from queryx.app.llm.ollama_client import (
    OllamaClient,
    OllamaError,
    OllamaInvalidResponseError,
)
from queryx.app.processing.storage import ProcessingStorage
from queryx.app.query.models import (
    LogicalQueryPlan,
    NaturalLanguageQueryRequest,
    NaturalLanguageQueryResponse,
)
from queryx.app.query.service import QueryService
from queryx.app.query.storage import QueryStorage
from queryx.app.query.validation import QueryValidationError


_MAX_CONTEXT_ASSETS = 12
_QUERY_LANGUAGE = re.compile(
    r"\b(select|insert|update|delete|drop|alter|create|pragma|attach|detach|copy|call)\b",
    re.IGNORECASE,
)
_SYSTEM_PROMPT = """You translate a user question into the supplied LogicalQueryPlan JSON schema.
Return one JSON object only, without markdown or commentary.
Use only listed asset_id, asset_version_id, fields, transforms, operators and relationship_id values.
Never invent assets, fields, functions or relationships.
If the question cannot be resolved unambiguously from the catalog, return {"error":"ambiguous_question"}.
"""


class NaturalLanguageQueryError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def payload(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class NaturalLanguageQueryService:
    def __init__(
        self,
        settings: Settings,
        client: OllamaClient | None = None,
        query_service: QueryService | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            num_ctx=settings.ollama_num_ctx,
            temperature=0,
            think=settings.ollama_think,
            keep_alive=settings.ollama_keep_alive,
        )
        self.query_service = query_service or QueryService(settings)
        self.ingestion = IngestionStorage(settings.catalog_db_path)
        self.processing = ProcessingStorage(settings.catalog_db_path)
        self.storage = QueryStorage(settings.catalog_db_path)

    def translate(
        self, request: NaturalLanguageQueryRequest
    ) -> NaturalLanguageQueryResponse:
        question = " ".join(request.question.split())
        if not question:
            raise NaturalLanguageQueryError(
                "ambiguous_question", "The question is empty or ambiguous", 422
            )
        if _QUERY_LANGUAGE.search(question):
            raise NaturalLanguageQueryError(
                "invalid_logical_plan", "Arbitrary query-language input is not accepted", 422
            )
        context = self._catalog_context(question)
        if not context["assets"]:
            raise NaturalLanguageQueryError(
                "invalid_logical_plan", "No ready queryable assets are available", 409
            )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": question,
                        "catalog": context,
                        "logical_query_plan_schema": LogicalQueryPlan.model_json_schema(),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        ]
        candidate = self._generate(messages)
        if candidate.get("error") == "ambiguous_question":
            raise NaturalLanguageQueryError(
                "ambiguous_question", "The question requires clarification", 422
            )
        try:
            plan = LogicalQueryPlan.model_validate(candidate)
        except ValidationError as exc:
            raise NaturalLanguageQueryError(
                "invalid_logical_plan", "Ollama returned an invalid LogicalQueryPlan", 422
            ) from exc
        try:
            validation = self.query_service.validate(plan)
        except QueryValidationError as exc:
            raise NaturalLanguageQueryError(
                "invalid_logical_plan", f"LogicalQueryPlan validation failed: {exc.code}", 422
            ) from exc
        result = self.query_service.execute(validation.normalized_plan) if request.execute else None
        return NaturalLanguageQueryResponse(
            normalized_plan=validation.normalized_plan,
            output_schema=validation.output_schema,
            warnings=validation.warnings,
            result=result,
        )

    def _generate(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        schema = {
            "oneOf": [
                LogicalQueryPlan.model_json_schema(),
                {
                    "type": "object",
                    "properties": {"error": {"const": "ambiguous_question"}},
                    "required": ["error"],
                    "additionalProperties": False,
                },
            ]
        }
        try:
            return self.client.chat_json(messages, schema).content
        except OllamaInvalidResponseError:
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": "The previous response was not valid JSON. Return exactly one valid JSON object.",
                },
            ]
            try:
                return self.client.chat_json(retry_messages, schema).content
            except OllamaInvalidResponseError as exc:
                raise NaturalLanguageQueryError(
                    "invalid_llm_json", "Ollama returned invalid JSON after one retry", 502
                ) from exc
            except OllamaError as exc:
                raise NaturalLanguageQueryError(
                    "llm_unavailable", "Ollama is unavailable", 503
                ) from exc
        except OllamaError as exc:
            raise NaturalLanguageQueryError(
                "llm_unavailable", "Ollama is unavailable", 503
            ) from exc

    def _catalog_context(self, question: str) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        for asset in self.ingestion.list_assets():
            versions = [version for version in asset.versions if str(version.status) == "ready"]
            for version in versions:
                bindings = self.processing.list_bindings(
                    version.id, BindingRole.SERVING, BindingStatus.READY
                )
                binding = next(
                    (item for item in reversed(bindings) if str(item.backend_type) == "duckdb"),
                    None,
                )
                schema = binding.metadata.get("serving_schema") if binding else None
                if binding is None or not isinstance(schema, list):
                    continue
                candidates.append(
                    {
                        "asset_id": asset.id,
                        "asset_version_id": version.id,
                        "name": asset.name,
                        "fields": [
                            {
                                "name": str(field.get("name")),
                                "data_type": str(field.get("data_type")),
                                "nullable": bool(field.get("nullable", True)),
                            }
                            for field in schema
                            if isinstance(field, dict) and field.get("name")
                        ],
                    }
                )
                break
        active_relationships = [
            relationship for relationship in self.storage.list_relationships(False)
        ]
        tokens = {
            token for token in re.findall(r"[\w]+", question.casefold()) if len(token) >= 3
        }
        scores: dict[str, int] = {}
        for asset in candidates:
            searchable = " ".join(
                [asset["name"], *(field["name"] for field in asset["fields"])]
            ).casefold()
            scores[asset["asset_id"]] = sum(token in searchable for token in tokens)
        matched = {asset_id for asset_id, score in scores.items() if score > 0}
        if matched:
            for relationship in active_relationships:
                if relationship.left_asset_id in matched or relationship.right_asset_id in matched:
                    matched.update({relationship.left_asset_id, relationship.right_asset_id})
            candidates = [asset for asset in candidates if asset["asset_id"] in matched]
        candidates.sort(key=lambda asset: (-scores.get(asset["asset_id"], 0), asset["name"], asset["asset_id"]))
        selected = candidates[:_MAX_CONTEXT_ASSETS]
        selected_ids = {asset["asset_id"] for asset in selected}
        relationships = [
            {
                "relationship_id": relationship.id,
                "left_asset_id": relationship.left_asset_id,
                "left_field": relationship.left_field,
                "right_asset_id": relationship.right_asset_id,
                "right_field": relationship.right_field,
                "relationship_type": relationship.relationship_type.value,
                "join_type_default": relationship.join_type_default.value,
            }
            for relationship in active_relationships
            if relationship.left_asset_id in selected_ids
            and relationship.right_asset_id in selected_ids
        ]
        return {"assets": selected, "relationships": relationships}
