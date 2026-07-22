from __future__ import annotations

import json
import logging
import re
from time import monotonic
from typing import Any

from pydantic import ValidationError

from queryx.app.core.config import Settings
from queryx.app.ingestion.models import BindingRole, BindingStatus
from queryx.app.ingestion.storage import IngestionStorage
from queryx.app.llm.ollama_client import (
    OllamaClient,
    OllamaError,
    OllamaInvalidResponseError,
    OllamaTimeoutError,
)
from queryx.app.processing.storage import ProcessingStorage
from queryx.app.query.models import (
    LogicalQueryPlan,
    NaturalLanguageClassification,
    NaturalLanguageQueryRequest,
    NaturalLanguageQueryResponse,
    NaturalLanguageWarning,
    QueryClassification,
)
from queryx.app.query.service import QueryService
from queryx.app.query.storage import QueryStorage
from queryx.app.query.validation import QueryValidationError


_MAX_CONTEXT_ASSETS = 12
_MAX_EXPLANATION_ROWS = 10
logger = logging.getLogger(__name__)
_QUERY_LANGUAGE = re.compile(
    r"\b(select|insert|update|delete|drop|alter|create|pragma|attach|detach|copy|call)\b",
    re.IGNORECASE,
)
_SYSTEM_PROMPT = """You translate a user question into the supplied LogicalQueryPlan JSON schema.
Return the LogicalQueryPlan object directly, without wrappers, markdown, commentary or additional top-level keys.
Use only listed asset_id, asset_version_id, fields, transforms, operators and relationship_id values.
Never invent assets, fields, functions or relationships.
Use the minimum number of assets and joins required to answer the question, and do not add unrequested metrics.
An aggregation is already an output column and must not be duplicated in projections.
Aggregation aliases may be referenced by order_by, but must never be used as source_alias in projections.
Every order_by field must exactly match an existing projection output name or aggregation alias.
When aggregations are present, every non-aggregated projection must appear identically in group_by, with the same source_alias, field and transform.
For product categories ranked by revenue, use only products and order_items, join them through their active catalog relationship, project product_category_name, sum order_items.price as revenue, group by product_category_name, and order by revenue descending. Do not include orders.
If the question cannot be resolved unambiguously from the catalog, return {"error":"ambiguous_question"}.
"""
_CLASSIFICATION_PROMPT = """Classify whether the user question can be answered from the supplied catalog.
Return only one JSON object matching the supplied schema, without markdown or additional keys.
Use classification answerable when the requested result is defined and computable from the listed fields and active relationships.
Use ambiguous when essential intent is unclear, and provide one concise clarification_question.
Use unanswerable when required data or metrics are absent, and explain the missing data briefly in reason.
Do not invent data, perform calculations, or include hidden analysis.
Exact example:
Input: "Quali sono i clienti migliori?"
Output: {"classification":"ambiguous","reason":"Il criterio di migliore non è specificato.","clarification_question":"Per migliori intendi i clienti con più ordini, maggiore spesa o un altro criterio?"}
"""


class _ClassificationParseError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class NaturalLanguageQueryError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        *,
        candidate_plan: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.candidate_plan = candidate_plan

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
        planning_started = monotonic()
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
        classification = self._classify(question, context)
        if classification.classification != QueryClassification.ANSWERABLE:
            planning_time_ms = (monotonic() - planning_started) * 1000
            return NaturalLanguageQueryResponse(
                classification=classification.classification,
                clarification_question=classification.clarification_question,
                reason=classification.reason,
                answer=(
                    classification.reason
                    if classification.classification == QueryClassification.UNANSWERABLE
                    else None
                ),
                planning_time_ms=planning_time_ms,
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
                        "correct_grouped_count_example": {
                            "projections": [
                                {
                                    "source_alias": "o",
                                    "field": "order_status",
                                    "alias": "status",
                                }
                            ],
                            "aggregations": [
                                {
                                    "function": "count",
                                    "source_alias": "o",
                                    "field": "order_id",
                                    "alias": "orders",
                                }
                            ],
                            "group_by": [
                                {"source_alias": "o", "field": "order_status"}
                            ],
                        },
                        "correct_multi_asset_revenue_example": {
                            "sources": [
                                {
                                    "alias": "p",
                                    "asset_id": "<products asset_id from catalog>",
                                },
                                {
                                    "alias": "oi",
                                    "asset_id": "<order_items asset_id from catalog>",
                                },
                            ],
                            "joins": [
                                {
                                    "relationship_id": "<active products-order_items relationship_id from catalog>",
                                    "left_alias": "p",
                                    "right_alias": "oi",
                                }
                            ],
                            "projections": [
                                {
                                    "source_alias": "p",
                                    "field": "product_category_name",
                                    "alias": "category",
                                }
                            ],
                            "aggregations": [
                                {
                                    "function": "sum",
                                    "source_alias": "oi",
                                    "field": "price",
                                    "alias": "revenue",
                                }
                            ],
                            "group_by": [
                                {
                                    "source_alias": "p",
                                    "field": "product_category_name",
                                }
                            ],
                            "order_by": [
                                {"field": "revenue", "direction": "desc"}
                            ],
                        },
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        ]
        candidate, retry_used = self._generate(messages)
        candidate = self._unwrap_logical_query_plan(candidate)
        if candidate.get("error") == "ambiguous_question":
            raise NaturalLanguageQueryError(
                "ambiguous_question", "The question requires clarification", 422
            )
        try:
            validation = self._validate_candidate(candidate)
        except QueryValidationError as exc:
            if retry_used:
                raise self._invalid_plan_error(exc, candidate) from exc
            candidate = self._unwrap_logical_query_plan(
                self._retry_invalid_plan(messages, candidate, exc.code)
            )
            if candidate.get("error") == "ambiguous_question":
                raise NaturalLanguageQueryError(
                    "ambiguous_question", "The question requires clarification", 422
                )
            try:
                validation = self._validate_candidate(candidate)
            except QueryValidationError as retry_exc:
                raise self._invalid_plan_error(retry_exc, candidate) from retry_exc
        planning_time_ms = (monotonic() - planning_started) * 1000
        result = self.query_service.execute(validation.normalized_plan) if request.execute else None
        answer: str | None = None
        explanation_warning: NaturalLanguageWarning | None = None
        explanation_time_ms: float | None = None
        if result is not None:
            explanation_started = monotonic()
            answer, explanation_warning = self._explain(question, result)
            explanation_time_ms = (monotonic() - explanation_started) * 1000
        return NaturalLanguageQueryResponse(
            normalized_plan=validation.normalized_plan,
            output_schema=validation.output_schema,
            warnings=validation.warnings,
            result=result,
            answer=answer,
            planning_time_ms=planning_time_ms,
            execution_time_ms=result.execution_time_ms if result is not None else None,
            explanation_time_ms=explanation_time_ms,
            explanation_warning=explanation_warning,
            classification=classification.classification,
            reason=classification.reason,
        )

    def _classify(
        self, question: str, context: dict[str, Any]
    ) -> NaturalLanguageClassification:
        messages = [
            {"role": "system", "content": _CLASSIFICATION_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"question": question, "catalog": context},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        ]
        schema = NaturalLanguageClassification.model_json_schema()
        try:
            raw_content = self.client.chat_text(messages, schema).content
            classification = self._parse_classification(raw_content)
        except (OllamaInvalidResponseError, _ClassificationParseError) as first_error:
            error_code = (
                first_error.code
                if isinstance(first_error, _ClassificationParseError)
                else "missing_message_content"
            )
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "instruction": "Correct only the classification response and return one valid JSON object matching the schema.",
                            "validation_error": error_code,
                            "classification_schema": schema,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ]
            try:
                raw_content = self.client.chat_text(retry_messages, schema).content
                classification = self._parse_classification(raw_content)
            except (OllamaInvalidResponseError, _ClassificationParseError) as exc:
                raise NaturalLanguageQueryError(
                    "invalid_classification",
                    "Ollama returned an invalid classification after one retry",
                    502,
                ) from exc
            except OllamaTimeoutError as exc:
                raise NaturalLanguageQueryError(
                    "llm_timeout", "Ollama classification request timed out", 504
                ) from exc
            except OllamaError as exc:
                raise NaturalLanguageQueryError(
                    "llm_unavailable", "Ollama is unavailable", 503
                ) from exc
        except OllamaTimeoutError as exc:
            raise NaturalLanguageQueryError(
                "llm_timeout", "Ollama classification request timed out", 504
            ) from exc
        except OllamaError as exc:
            raise NaturalLanguageQueryError(
                "llm_unavailable", "Ollama is unavailable", 503
            ) from exc
        return classification

    @staticmethod
    def _parse_classification(raw_content: str) -> NaturalLanguageClassification:
        logger.debug("Ollama classifier message.content=%r", raw_content[:4000])
        content = raw_content.strip()
        fenced = re.fullmatch(r"```json\s*(.*?)\s*```", content, re.DOTALL)
        if fenced:
            content = fenced.group(1).strip()
        try:
            candidate = json.loads(content)
        except json.JSONDecodeError as exc:
            raise _ClassificationParseError("invalid_json") from exc
        if not isinstance(candidate, dict):
            raise _ClassificationParseError("classification_must_be_object")
        if set(candidate) == {"classification_result"}:
            wrapped = candidate["classification_result"]
            if not isinstance(wrapped, dict):
                raise _ClassificationParseError("invalid_classification_wrapper")
            candidate = wrapped
        normalized = dict(candidate)
        for field in ("classification", "reason", "clarification_question"):
            value = normalized.get(field)
            if isinstance(value, str):
                normalized[field] = value.strip()
        try:
            return NaturalLanguageClassification.model_validate(normalized)
        except ValidationError as exc:
            raise _ClassificationParseError("classification_schema_invalid") from exc

    def _explain(
        self, question: str, result: Any
    ) -> tuple[str | None, NaturalLanguageWarning | None]:
        if result.row_count == 0:
            return "La query non ha restituito risultati.", None
        serialized = result.model_dump(mode="json")
        payload = {
            "question": question,
            "columns": serialized["columns"],
            "rows": serialized["rows"][:_MAX_EXPLANATION_ROWS],
            "row_count": serialized["row_count"],
            "truncated": serialized["truncated"],
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "Write a concise natural-language answer of at most three sentences, based "
                    "exclusively on the supplied result values. Do not perform or request new "
                    "calculations. Do not include reasoning or hidden analysis. If truncated is "
                    "true, clearly state that the displayed result is truncated. Return only "
                    "the answer text."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
        ]
        try:
            raw_answer = self.client.chat_text(messages).content.strip()
        except OllamaError:
            return None, NaturalLanguageWarning(
                code="explanation_unavailable",
                message="The query succeeded, but its natural-language explanation is unavailable.",
            )
        if not raw_answer:
            return None, NaturalLanguageWarning(
                code="explanation_unavailable",
                message="The query succeeded, but Ollama returned an empty explanation.",
            )
        answer = self._limit_sentences(raw_answer, 2 if result.truncated else 3)
        if result.truncated:
            answer = f"{answer} Il risultato mostrato è troncato."
        return answer, None

    @staticmethod
    def _limit_sentences(answer: str, maximum: int) -> str:
        normalized = " ".join(answer.split())
        sentences = re.split(r"(?<=[.!?])\s+", normalized)
        return " ".join(sentences[:maximum])

    def _validate_candidate(self, candidate: dict[str, Any]) -> Any:
        try:
            plan = LogicalQueryPlan.model_validate(candidate)
        except ValidationError as exc:
            raise NaturalLanguageQueryError(
                "invalid_logical_plan",
                "Ollama returned an invalid LogicalQueryPlan",
                422,
                candidate_plan=self._safe_debug_candidate(candidate),
            ) from exc
        return self.query_service.validate(plan)

    @staticmethod
    def _unwrap_logical_query_plan(candidate: dict[str, Any]) -> dict[str, Any]:
        if set(candidate) == {"logical_query_plan"}:
            wrapped = candidate["logical_query_plan"]
            if isinstance(wrapped, dict):
                return wrapped
        return candidate

    def _invalid_plan_error(
        self, error: QueryValidationError, candidate: dict[str, Any]
    ) -> NaturalLanguageQueryError:
        return NaturalLanguageQueryError(
            "invalid_logical_plan",
            f"LogicalQueryPlan validation failed: {error.code}",
            422,
            candidate_plan=self._safe_debug_candidate(candidate),
        )

    def _retry_invalid_plan(
        self,
        messages: list[dict[str, str]],
        candidate: dict[str, Any],
        validation_code: str,
    ) -> dict[str, Any]:
        retry_messages = [
            *messages,
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "instruction": (
                            "Correct only the LogicalQueryPlan: remove unnecessary assets, joins, "
                            "and metrics; correct output aliases and join order; then return only "
                            "the corrected plan object."
                        ),
                        "previous_plan": candidate,
                        "validation_code": validation_code,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        ]
        try:
            return self._request(retry_messages)
        except OllamaInvalidResponseError as exc:
            raise NaturalLanguageQueryError(
                "invalid_llm_json", "Ollama returned invalid JSON after one retry", 502
            ) from exc

    def _generate(
        self, messages: list[dict[str, str]]
    ) -> tuple[dict[str, Any], bool]:
        try:
            return self._request(messages), False
        except OllamaInvalidResponseError:
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": "The previous response was not valid JSON. Return exactly one valid JSON object.",
                },
            ]
            try:
                return self._request(retry_messages), True
            except OllamaInvalidResponseError as exc:
                raise NaturalLanguageQueryError(
                    "invalid_llm_json", "Ollama returned invalid JSON after one retry", 502
                ) from exc

    def _request(self, messages: list[dict[str, str]]) -> dict[str, Any]:
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
            raise
        except OllamaTimeoutError as exc:
            raise NaturalLanguageQueryError(
                "llm_timeout", "Ollama request timed out", 504
            ) from exc
        except OllamaError as exc:
            raise NaturalLanguageQueryError(
                "llm_unavailable", "Ollama is unavailable", 503
            ) from exc

    @staticmethod
    def _safe_debug_candidate(candidate: dict[str, Any]) -> dict[str, Any] | None:
        forbidden = {"sql", "thinking", "physical_location", "relation_name", "path"}

        def contains_forbidden(value: Any) -> bool:
            if isinstance(value, dict):
                return any(
                    str(key).casefold() in forbidden or contains_forbidden(item)
                    for key, item in value.items()
                )
            if isinstance(value, list):
                return any(contains_forbidden(item) for item in value)
            return False

        return None if contains_forbidden(candidate) else candidate

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
