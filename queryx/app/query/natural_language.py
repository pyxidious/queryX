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
    OllamaModelNotFoundError,
    OllamaTimeoutError,
    OllamaUnavailableError,
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
from queryx.app.sources.registry import SourceRegistry


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
Treat every catalog asset object as an atomic source block identified by its asset_id and backend.
After choosing a source, use only the fields listed for that source. Never combine schemas from assets with the same logical name.
Use semantic_field_hints only within the asset block that contains them; they are catalog-scoped disambiguation evidence.
If the question explicitly names MySQL, choose only an asset whose backend is mysql.
If the question explicitly names MongoDB, choose only an asset whose backend is mongodb.
If the question explicitly names CSV, dataset CSV, file or DuckDB, choose only an asset whose backend is duckdb.
For a DuckDB asset, use only its listed file-dataset fields; never select a same-named MySQL asset.
For DuckDB monthly counts, use one date_trunc_month projection and the identical group_by expression, one count aggregation, and a bounded limit. Keep this plan compact and never repeat objects.
For an asset whose backend is mysql, use exactly one source, no joins and no transforms.
For an asset whose backend is mongodb, use exactly one source, no joins and no transforms.
For MongoDB embedded-document arrays, use array_matches when all predicates must apply to the same array element.
Use unwinds only when the answer requires individual array elements, grouping by an element field, or aggregating an element field.
Never emit MongoDB pipeline stages or operators. Use catalog paths containing [] only with the corresponding unwind; inside array_matches use relative element fields.
For MongoDB profiles, use preferences.newsletter for newsletter conditions when that exact field is listed; never shorten it to newsletter.
"newsletter attiva" or "newsletter abilitata" means exactly one eq true filter; "newsletter disattiva" or "newsletter non attiva" means exactly one eq false filter. Do not add is_not_null to a boolean comparison.
For MongoDB profiles, "lingua inglese" means exactly one preferences.language eq "en" filter and "lingua italiana" means exactly one preferences.language eq "it" filter, only when that exact field is listed.
For MongoDB events, "per tipo" means project and group_by type together with count(_id) as events.
For MongoDB events, "importo totale" means sum(properties.amount) as total and "importo medio" means avg(properties.amount) as avg_amount, with no projections, filters, group_by or order_by.
For MongoDB events, an explicit numeric user such as "utente 1" means exactly one user_id eq 1 filter using a numeric value when user_id is numeric.
For a count request, output only the requested count aggregation: do not add row projections, filters, group_by or order_by unless explicitly requested.
Use the minimum number of assets and joins required to answer the question, and do not add unrequested metrics.
For record-display requests, project only fields directly useful to the request instead of inventing a complete projection.
Treat "mostra", "elenca", "visualizza" and "dammi gli ordini" as row-returning intent: use projections and filters, with no aggregation or group_by unless the question explicitly requests an aggregate.
Treat "quanti", "conta" and "numero di" as count intent. Never turn a row-returning request into a count.
When the user explicitly lists fields, project exactly those fields and do not add filters, aggregations or categories that were not requested.
For row-returning requests, leave order_by empty unless the user explicitly asks for ordering.
Treat a requested result cardinality such as "five", "cinque" or "top 5" as LIMIT, never as a filter threshold.
Treat superlatives such as "highest", "più alto" or "più elevato" as descending order on the named catalog field, not as a filter.
Every source_alias in projections, filters, aggregations, group_by and joins must exactly match an alias declared in sources.
Every MongoDB aggregation must include source_alias, using exactly the alias declared by its source.
Use catalog-scoped semantic_metric_hints for avg or sum only when their field exists in the selected asset.
An explicit categorical condition such as "stato paid" is a filter on the matching field, not a request to group by every category. For a filtered count, do not project or group by that field unless explicitly requested.
An aggregation is already an output column and must not be duplicated in projections.
Aggregation aliases may be referenced by order_by, but must never be used as source_alias in projections.
Every order_by field must exactly match an existing projection output name or aggregation alias.
When aggregations are present, every non-aggregated projection must appear identically in group_by, with the same source_alias, field and transform.
For product categories ranked by revenue, use only products and order_items, join them through their active catalog relationship, project product_category_name, sum order_items.price as revenue, group by product_category_name, and order by revenue descending. Do not include orders.
"""
_CLASSIFICATION_PROMPT = """Classify whether the user question can be answered from the supplied catalog.
Return only one JSON object matching the supplied schema, without markdown or additional keys.
Use classification answerable when the requested result is defined and computable from the listed fields and active relationships.
Use ambiguous when essential intent is unclear, and provide one concise clarification_question.
Use unanswerable when required data or metrics are absent, and explain the missing data briefly in reason.
Before classifying a question as ambiguous, use its explicit backend, candidate assets, fields and catalog-scoped semantic_field_hints. If these provide exactly one plausible interpretation, classify it as answerable.
CSV, dataset CSV, file and DuckDB explicitly identify backend duckdb. A row request listing fields that all exist in that single selected asset is answerable.
Questions grouping a listed timestamp field by month are answerable using the controlled date_trunc_month transform.
If assets share a logical name across backends and the question identifies neither a backend nor fields that select one unambiguously, classify it as ambiguous.
Do not invent data, perform calculations, or include hidden analysis.
Exact example:
Input: "Quali sono i clienti migliori?"
Output: {"classification":"ambiguous","reason":"Il criterio di migliore non è specificato.","clarification_question":"Per migliori intendi i clienti con più ordini, maggiore spesa o un altro criterio?"}
Input: "Qual è il profitto totale?"
Output: {"classification":"unanswerable","reason":"Il catalogo contiene dati sui ricavi, ma non contiene dati sui costi necessari per calcolare il profitto.","clarification_question":null}
Input: "Mostra order_id, order_status e order_purchase_timestamp del dataset CSV orders"
Output: {"classification":"answerable","reason":"I campi richiesti appartengono all'unico asset CSV orders selezionato.","clarification_question":null}
Input: "Quanti ordini del dataset CSV orders ci sono per mese di order_purchase_timestamp?"
Output: {"classification":"answerable","reason":"Il timestamp elencato supporta il raggruppamento mensile controllato.","clarification_question":null}
"""


class _ClassificationParseError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _PlanningSemanticError(ValueError):
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
        classification = self._apply_catalog_disambiguation(
            question, context, classification
        )
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
                        **({"mysql_record_display_example": {
                            "question": "Mostra gli ordini MySQL con totale maggiore di 100",
                            "rule": "Use this shape only when the selected MySQL asset lists these exact fields.",
                            "projections": [
                                {"source_alias": "o", "field": field}
                                for field in (
                                    "id", "customer_id", "status", "total", "created_at"
                                )
                            ],
                            "filters": [
                                {
                                    "source_alias": "o",
                                    "field": "total",
                                    "operator": "gt",
                                    "value": 100,
                                }
                            ],
                        }} if any(
                            asset["backend"] == "mysql" for asset in context["assets"]
                        ) else {}),
                        "catalog_scoped_resolution_examples": self._planning_examples(
                            context
                        ),
                        **({"duckdb_monthly_count_example": self._duckdb_monthly_example(
                            context
                        )} if self._duckdb_monthly_example(context) is not None else {}),
                        **({"correct_multi_asset_revenue_example": {
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
                        }} if context["relationships"] else {}),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        ]
        candidate, retry_used = self._generate(messages)
        candidate = self._unwrap_logical_query_plan(candidate)
        try:
            validation = self._validate_candidate_semantics(
                candidate, question, context
            )
        except (QueryValidationError, _PlanningSemanticError) as exc:
            self._log_rejected_plan(exc.code, candidate)

            # Deterministic repairs are attempted before checking whether the
            # JSON-syntax retry budget has already been consumed.
            repaired_candidate = self._canonicalize_grouped_retry(
                candidate, exc.code, question, context
            )
            active_error: QueryValidationError | _PlanningSemanticError | None = exc
            if repaired_candidate != candidate:
                candidate = repaired_candidate
                try:
                    validation = self._validate_candidate_semantics(
                        candidate, question, context
                    )
                except (QueryValidationError, _PlanningSemanticError) as repair_exc:
                    self._log_rejected_plan(repair_exc.code, candidate)
                    active_error = repair_exc
                else:
                    active_error = None

            if active_error is not None:
                if retry_used:
                    raise self._invalid_plan_error(
                        active_error, candidate
                    ) from active_error
                candidate = self._unwrap_logical_query_plan(
                    self._retry_invalid_plan(
                        messages,
                        candidate,
                        active_error.code,
                        context,
                        question,
                    )
                )
                candidate = self._canonicalize_grouped_retry(
                    candidate, active_error.code, question, context
                )
                try:
                    validation = self._validate_candidate_semantics(
                        candidate, question, context
                    )
                except (QueryValidationError, _PlanningSemanticError) as retry_exc:
                    self._log_rejected_plan(retry_exc.code, candidate)
                    repaired_retry_candidate = self._canonicalize_grouped_retry(
                        candidate, retry_exc.code, question, context
                    )
                    if repaired_retry_candidate == candidate:
                        raise self._invalid_plan_error(
                            retry_exc, candidate
                        ) from retry_exc
                    candidate = repaired_retry_candidate
                    try:
                        validation = self._validate_candidate_semantics(
                            candidate, question, context
                        )
                    except (
                        QueryValidationError,
                        _PlanningSemanticError,
                    ) as repaired_retry_exc:
                        self._log_rejected_plan(
                            repaired_retry_exc.code, candidate
                        )
                        raise self._invalid_plan_error(
                            repaired_retry_exc, candidate
                        ) from repaired_retry_exc
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
        raw_content = ""
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
                            "instruction": "Correct only the classification response. Return only one valid JSON object matching the schema, with no other text.",
                            "previous_content": raw_content[:4000],
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
            except (OllamaUnavailableError, OllamaModelNotFoundError) as exc:
                raise self._llm_unavailable(exc, "classification_retry") from exc
        except OllamaTimeoutError as exc:
            raise NaturalLanguageQueryError(
                "llm_timeout", "Ollama classification request timed out", 504
            ) from exc
        except (OllamaUnavailableError, OllamaModelNotFoundError) as exc:
            raise self._llm_unavailable(exc, "classification") from exc
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
        if (
            normalized.get("classification") in {"answerable", "unanswerable"}
            and normalized.get("clarification_question") == ""
        ):
            normalized["clarification_question"] = None
        try:
            return NaturalLanguageClassification.model_validate(normalized)
        except ValidationError as exc:
            raise _ClassificationParseError("classification_schema_invalid") from exc

    def _explain(
        self, question: str, result: Any
    ) -> tuple[str | None, NaturalLanguageWarning | None]:
        if result.row_count == 0:
            return (
                "The query returned no results."
                if self._is_english_question(question)
                else "La query non ha restituito risultati."
            ), None
        serialized = result.model_dump(mode="json")
        returned_rows = serialized["rows"]
        columns = serialized["columns"]
        requested_limit = self._requested_limit(question.casefold())
        effective_truncated = bool(
            serialized["truncated"] and requested_limit is None
        )
        rows_for_explanation = returned_rows[:_MAX_EXPLANATION_ROWS]
        rows_omitted_from_prompt = max(
            len(returned_rows) - len(rows_for_explanation), 0
        )
        if rows_omitted_from_prompt:
            answer = self._deterministic_tabular_answer(
                question,
                row_count=serialized["row_count"],
                columns=columns,
                truncated=effective_truncated,
            )
            logger.info(
                "Used deterministic explanation because %s result rows were omitted "
                "from the LLM context",
                rows_omitted_from_prompt,
            )
            return answer, None
        result_shape = (
            "scalar"
            if len(returned_rows) == 1 and len(columns) == 1
            else "tabular"
        )
        payload = {
            "question": question,
            "columns": columns,
            "rows": rows_for_explanation,
            "row_count": serialized["row_count"],
            "returned_rows_count": len(returned_rows),
            "rows_in_prompt": len(rows_for_explanation),
            "rows_omitted_from_prompt": 0,
            "result_truncated": effective_truncated,
            "result_shape": result_shape,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "Write a concise natural-language answer of at most three sentences, based "
                    "exclusively on the supplied result metadata and row values. All returned "
                    "rows are present in the rows array. row_count always means the number of "
                    "output rows, never the total of a measure or the number of business objects. "
                    "Only interpret a value as a total count when result_shape is scalar and the "
                    "single visible cell contains that count. For tabular or grouped results, "
                    "describe the visible rows and columns without summing aggregation values. "
                    "Never claim that the query result is truncated unless result_truncated is "
                    "true. When result_truncated is false, do not mention truncation, omitted "
                    "rows, hidden rows or additional rows. Do not infer values, ranges, totals, "
                    "trends, or properties not directly supported by the supplied rows, and do "
                    "not perform or request new calculations. Do not include reasoning or hidden "
                    "analysis. Return only the answer text in the same language as the user's "
                    "question."
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
        answer = self._limit_sentences(
            raw_answer, 2 if effective_truncated else 3
        )
        if effective_truncated:
            truncation_notice = (
                "The displayed result is truncated."
                if self._is_english_question(question)
                else "Il risultato mostrato è troncato."
            )
            answer = f"{answer} {truncation_notice}"
        return answer, None

    @classmethod
    def _deterministic_tabular_answer(
        cls,
        question: str,
        *,
        row_count: int,
        columns: list[str],
        truncated: bool,
    ) -> str:
        rendered_columns = ", ".join(f"`{column}`" for column in columns)
        if cls._is_english_question(question):
            answer = (
                f"The query returned {row_count} rows with the columns "
                f"{rendered_columns}. See the result table for the complete values."
            )
            if truncated:
                answer = f"{answer} The displayed result is truncated."
            return answer
        answer = (
            f"La query ha restituito {row_count} righe con le colonne "
            f"{rendered_columns}. Consulta la tabella dei risultati per i valori completi."
        )
        if truncated:
            answer = f"{answer} Il risultato mostrato è troncato."
        return answer

    @staticmethod
    def _is_english_question(question: str) -> bool:
        return bool(re.search(
            r"\b(show|list|display|how\s+many|count|number\s+of|which|what)\b",
            question,
            re.IGNORECASE,
        ))

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

    def _validate_candidate_semantics(
        self, candidate: dict[str, Any], question: str, context: dict[str, Any]
    ) -> Any:
        requirements = self._semantic_requirements(question, context)
        sources = candidate.get("sources", [])
        selected = next(
            (
                source
                for source in sources
                if isinstance(source, dict)
                and source.get("asset_id") == requirements.get("asset_id")
            ),
            None,
        )
        if requirements.get("asset_id") and selected is None:
            raise _PlanningSemanticError("semantic_asset_mismatch")
        selected_asset = next(
            (
                asset
                for asset in context["assets"]
                if asset.get("asset_id") == requirements.get("asset_id")
            ),
            None,
        )
        if (
            selected is not None
            and selected_asset is not None
            and selected_asset.get("backend") == "mongodb"
        ):
            declared_alias = selected.get("alias")
            if not isinstance(declared_alias, str) or any(
                not isinstance(aggregation, dict)
                or aggregation.get("source_alias") != declared_alias
                for aggregation in candidate.get("aggregations", [])
            ):
                raise _PlanningSemanticError(
                    "mongodb_aggregation_source_alias_mismatch"
                )
        validation = self._validate_candidate(candidate)
        if selected is not None:
            alias = selected.get("alias")
            intent = requirements.get("intent")
            required_filter = requirements.get("filter")
            if required_filter is not None:
                matching_filters = [
                    item
                    for item in candidate.get("filters", [])
                    if (
                    isinstance(item, dict)
                    and item.get("source_alias") == alias
                    and item.get("field") == required_filter["field"]
                    and item.get("operator") == required_filter["operator"]
                    and self._semantic_values_equal(
                        item.get("value"), required_filter["value"]
                    )
                    )
                ]
                if not matching_filters:
                    raise _PlanningSemanticError("missing_explicit_filter")
                if requirements.get("exact_filter") and (
                    len(matching_filters) != 1
                    or len(candidate.get("filters", [])) != 1
                ):
                    raise _PlanningSemanticError("mongodb_filter_mismatch")
                category_field = required_filter["field"]
                if intent == "count" and any(
                    isinstance(item, dict) and item.get("field") == category_field
                    for item in [
                        *candidate.get("projections", []),
                        *candidate.get("group_by", []),
                    ]
                ):
                    raise _PlanningSemanticError("unrequested_categories")
            if intent == "row_returning":
                if candidate.get("aggregations") or candidate.get("group_by"):
                    raise _PlanningSemanticError("row_intent_mismatch")
                if candidate.get("order_by") and not requirements.get("ordering_requested"):
                    raise _PlanningSemanticError("row_order_by_mismatch")
                if requirements.get("strict_row_shape"):
                    expected_filters = 1 if required_filter is not None else 0
                    if len(candidate.get("filters", [])) != expected_filters:
                        raise _PlanningSemanticError("row_filter_mismatch")
                    required_fields = requirements.get("projections", [])
                    projected_fields = [
                        item.get("field")
                        for item in candidate.get("projections", [])
                        if isinstance(item, dict) and item.get("source_alias") == alias
                    ]
                    if projected_fields != required_fields:
                        raise _PlanningSemanticError("row_projection_mismatch")
                    expected_order = requirements.get("order_by")
                    if expected_order is not None:
                        matching_projection = next(
                            (
                                item
                                for item in candidate.get("projections", [])
                                if isinstance(item, dict)
                                and item.get("source_alias") == alias
                                and item.get("field") == expected_order["field"]
                            ),
                            None,
                        )
                        output_name = (
                            matching_projection.get("alias")
                            if isinstance(matching_projection, dict)
                            and matching_projection.get("alias")
                            else expected_order["field"]
                        )
                        if candidate.get("order_by") != [{
                            "field": output_name,
                            "direction": expected_order["direction"],
                        }]:
                            raise _PlanningSemanticError("row_order_by_mismatch")
                    expected_limit = requirements.get("limit")
                    if (
                        expected_limit is not None
                        and candidate.get("limit") != expected_limit
                    ):
                        raise _PlanningSemanticError("row_limit_mismatch")
                if requirements.get("strict_mongodb_row_shape"):
                    projected_fields = [
                        item.get("field")
                        for item in candidate.get("projections", [])
                        if isinstance(item, dict) and item.get("source_alias") == alias
                    ]
                    required_fields = set(requirements["required_projections"])
                    allowed_fields = set(requirements["allowed_projections"])
                    if (
                        not required_fields.issubset(projected_fields)
                        or any(field not in allowed_fields for field in projected_fields)
                        or len(projected_fields) != len(set(projected_fields))
                    ):
                        raise _PlanningSemanticError("mongodb_row_projection_mismatch")
                if requirements.get("strict_mongodb_recent_top_k") and (
                    candidate.get("unwinds") or candidate.get("array_matches")
                ):
                    raise _PlanningSemanticError(
                        "mongodb_events_recent_top_k_mismatch"
                    )
            array_shape_code = requirements.get("mongodb_array_shape_code")
            if (
                isinstance(array_shape_code, str)
                and not self._matches_mongodb_array_shape(
                    candidate, alias, requirements
                )
            ):
                raise _PlanningSemanticError(array_shape_code)
            aggregation = requirements.get("aggregation")
            if aggregation is not None:
                matched = any(
                    isinstance(item, dict)
                    and item.get("function") == aggregation["function"]
                    and item.get("field") == aggregation["field"]
                    and item.get("source_alias") == alias
                    and (
                        aggregation.get("alias") is None
                        or item.get("alias") == aggregation["alias"]
                    )
                    for item in candidate.get("aggregations", [])
                )
                if not matched or (
                    candidate.get("group_by") and not requirements.get("group_by")
                ):
                    raise _PlanningSemanticError("metric_aggregation_mismatch")
            if requirements.get("strict_duckdb_grouped_count"):
                expected_group = requirements["group_by"]
                projections = candidate.get("projections", [])
                group_by = candidate.get("group_by", [])
                if (
                    len(projections) != 1
                    or not isinstance(projections[0], dict)
                    or projections[0].get("source_alias") != alias
                    or projections[0].get("field") != expected_group["field"]
                    or projections[0].get("transform") != expected_group.get("transform")
                    or len(group_by) != 1
                    or not isinstance(group_by[0], dict)
                    or group_by[0].get("source_alias") != alias
                    or group_by[0].get("field") != expected_group["field"]
                    or group_by[0].get("transform") != expected_group.get("transform")
                ):
                    raise _PlanningSemanticError("duckdb_grouped_count_mismatch")
            if requirements.get("strict_mongodb_count_shape"):
                aggregations = candidate.get("aggregations", [])
                expected = requirements["aggregation"]
                expected_filter_count = 1 if required_filter is not None else 0
                expected_group = requirements.get("group_by")
                projections = candidate.get("projections", [])
                group_by = candidate.get("group_by", [])
                grouped_shape_invalid = False
                if expected_group is not None:
                    grouped_shape_invalid = (
                        len(projections) != 1
                        or not isinstance(projections[0], dict)
                        or projections[0].get("source_alias") != alias
                        or projections[0].get("field") != expected_group["field"]
                        or len(group_by) != 1
                        or not isinstance(group_by[0], dict)
                        or group_by[0].get("source_alias") != alias
                        or group_by[0].get("field") != expected_group["field"]
                    )
                else:
                    grouped_shape_invalid = bool(projections or group_by)
                if (
                    len(aggregations) != 1
                    or not isinstance(aggregations[0], dict)
                    or aggregations[0].get("function") != "count"
                    or aggregations[0].get("source_alias") != alias
                    or aggregations[0].get("field") != expected["field"]
                    or aggregations[0].get("alias") != expected["alias"]
                    or len(candidate.get("filters", [])) != expected_filter_count
                    or grouped_shape_invalid
                    or (
                        candidate.get("order_by")
                        and not requirements.get("ordering_requested")
                    )
                    or (
                        requirements.get("strict_mongodb_no_array_ops")
                        and (
                            candidate.get("unwinds")
                            or candidate.get("array_matches")
                        )
                    )
                ):
                    raise _PlanningSemanticError("mongodb_count_intent_mismatch")
            if requirements.get("strict_mongodb_scalar_aggregation"):
                aggregations = candidate.get("aggregations", [])
                expected = requirements["aggregation"]
                if (
                    len(aggregations) != 1
                    or not isinstance(aggregations[0], dict)
                    or aggregations[0].get("function") != expected["function"]
                    or aggregations[0].get("source_alias") != alias
                    or aggregations[0].get("field") != expected["field"]
                    or aggregations[0].get("alias") != expected["alias"]
                    or candidate.get("projections")
                    or candidate.get("filters")
                    or candidate.get("group_by")
                    or candidate.get("order_by")
                ):
                    raise _PlanningSemanticError(
                        "mongodb_scalar_aggregation_mismatch"
                    )
        return validation

    @staticmethod
    def _matches_mongodb_array_shape(
        candidate: dict[str, Any],
        source_alias: Any,
        requirements: dict[str, Any],
    ) -> bool:
        unwind = requirements["unwind"]
        aggregation = requirements["aggregation"]
        projection = requirements.get("projection")
        group_by = requirements.get("group_by")
        array_match = requirements.get("array_match")

        def one(section: str) -> dict[str, Any] | None:
            values = candidate.get(section, [])
            return (
                values[0]
                if len(values) == 1 and isinstance(values[0], dict)
                else None
            )

        candidate_unwind = one("unwinds")
        candidate_projection = one("projections")
        candidate_aggregation = one("aggregations")
        candidate_group_by = one("group_by")
        candidate_array_match = one("array_matches")
        projection_matches = (
            not candidate.get("projections")
            if projection is None
            else (
                candidate_projection is not None
                and candidate_projection.get("source_alias") == source_alias
                and candidate_projection.get("field") == projection["field"]
                and candidate_projection.get("alias") == projection["alias"]
                and candidate_projection.get("transform") is None
            )
        )
        group_by_matches = (
            not candidate.get("group_by")
            if group_by is None
            else (
                candidate_group_by is not None
                and candidate_group_by.get("source_alias") == source_alias
                and candidate_group_by.get("field") == group_by["field"]
                and candidate_group_by.get("transform") is None
            )
        )
        array_match_matches = not candidate.get("array_matches")
        if array_match is not None:
            predicates = (
                candidate_array_match.get("predicates", [])
                if candidate_array_match is not None
                else []
            )
            expected_predicate = array_match["predicate"]
            array_match_matches = (
                candidate_array_match is not None
                and candidate_array_match.get("source_alias") == source_alias
                and candidate_array_match.get("field") == array_match["field"]
                and len(predicates) == 1
                and isinstance(predicates[0], dict)
                and predicates[0].get("field") == expected_predicate["field"]
                and predicates[0].get("operator") == expected_predicate["operator"]
                and NaturalLanguageQueryService._semantic_values_equal(
                    predicates[0].get("value"), expected_predicate["value"]
                )
            )
        return (
            isinstance(source_alias, str)
            and candidate_unwind is not None
            and candidate_unwind.get("source_alias") == source_alias
            and candidate_unwind.get("field") == unwind["field"]
            and not candidate_unwind.get("preserve_null_and_empty_arrays", False)
            and projection_matches
            and candidate_aggregation is not None
            and candidate_aggregation.get("function") == aggregation["function"]
            and candidate_aggregation.get("source_alias") == source_alias
            and candidate_aggregation.get("field") == aggregation["field"]
            and candidate_aggregation.get("alias") == aggregation["alias"]
            and group_by_matches
            and array_match_matches
            and not candidate.get("filters")
            and not candidate.get("order_by")
        )

    @staticmethod
    def _semantic_values_equal(actual: Any, expected: Any) -> bool:
        if isinstance(expected, str):
            return isinstance(actual, str) and actual.casefold() == expected.casefold()
        return actual == expected

    @staticmethod
    def _unwrap_logical_query_plan(candidate: dict[str, Any]) -> dict[str, Any]:
        if set(candidate) == {"logical_query_plan"}:
            wrapped = candidate["logical_query_plan"]
            if isinstance(wrapped, dict):
                return wrapped
        return candidate

    def _invalid_plan_error(
        self, error: QueryValidationError | _PlanningSemanticError,
        candidate: dict[str, Any],
    ) -> NaturalLanguageQueryError:
        return NaturalLanguageQueryError(
            "invalid_logical_plan",
            f"LogicalQueryPlan validation failed: {error.code}",
            422,
            candidate_plan=self._safe_debug_candidate(candidate),
        )

    @staticmethod
    def _log_rejected_plan(code: str, candidate: dict[str, Any]) -> None:
        def fields(section: str, allowed: tuple[str, ...]) -> list[dict[str, Any]]:
            return [
                {key: item[key] for key in allowed if key in item}
                for item in candidate.get(section, [])
                if isinstance(item, dict)
            ]

        structure = {
            "sources": fields("sources", ("alias",)),
            "joins": fields(
                "joins", ("left_alias", "right_alias", "join_type")
            ),
            "projections": fields(
                "projections", ("source_alias", "field", "transform", "alias")
            ),
            "filters": fields("filters", ("source_alias", "field", "operator")),
            "aggregations": fields(
                "aggregations", ("function", "source_alias", "field", "alias")
            ),
            "group_by": fields(
                "group_by", ("source_alias", "field", "transform")
            ),
            "unwinds": fields(
                "unwinds",
                ("source_alias", "field", "preserve_null_and_empty_arrays"),
            ),
            "array_matches": [
                {
                    key: item[key]
                    for key in ("source_alias", "field")
                    if key in item
                }
                for item in candidate.get("array_matches", [])
                if isinstance(item, dict)
            ],
            "order_by": fields("order_by", ("field", "direction")),
            "limit": candidate.get("limit"),
        }
        logger.warning(
            "LogicalQueryPlan rejected validation_code=%s plan_structure=%s",
            code,
            structure,
        )

    def _canonicalize_grouped_retry(
        self,
        candidate: dict[str, Any],
        validation_code: str,
        question: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if validation_code == "mongodb_count_intent_mismatch":
            return self._canonicalize_mongodb_global_count(
                candidate, question, context
            )
        if validation_code == "mongodb_profiles_by_role_mismatch":
            return self._canonicalize_mongodb_profiles_by_role(
                candidate, question, context
            )
        if validation_code in {
            "invalid_group_by",
            "metric_aggregation_mismatch",
            "mongodb_events_recent_top_k_mismatch",
            "row_intent_mismatch",
            "row_projection_mismatch",
            "row_filter_mismatch",
            "row_order_by_mismatch",
            "row_limit_mismatch",
        }:
            recent_candidate = self._canonicalize_mongodb_recent_events_top_k(
                candidate, question, context
            )
            if recent_candidate != candidate:
                return recent_candidate
        if validation_code != "invalid_group_by":
            return candidate

        requirements = self._semantic_requirements(question, context)
        group = requirements.get("group_by")
        aggregation = requirements.get("aggregation")
        asset_id = requirements.get("asset_id")
        repair_source = "semantic_requirements"

        source: dict[str, Any] | None = None
        if isinstance(asset_id, str):
            source = next(
                (
                    item
                    for item in candidate.get("sources", [])
                    if isinstance(item, dict) and item.get("asset_id") == asset_id
                ),
                None,
            )

        if not (
            isinstance(group, dict)
            and isinstance(aggregation, dict)
            and source is not None
            and isinstance(source.get("alias"), str)
        ):
            # Catalog-scoped requirements can be empty when more than one
            # same-named DuckDB asset is present. In that case, repair only the
            # already-selected candidate shape and leave final validation
            # mandatory.
            repair_source = "candidate_shape"
            group_items = candidate.get("group_by", [])
            aggregation_items = candidate.get("aggregations", [])
            if len(group_items) != 1 or len(aggregation_items) != 1:
                logger.info(
                    "Skipped grouped retry canonicalization reason=unsupported_shape "
                    "group_count=%s aggregation_count=%s",
                    len(group_items),
                    len(aggregation_items),
                )
                return candidate

            candidate_group = group_items[0]
            candidate_aggregation = aggregation_items[0]
            if not (
                isinstance(candidate_group, dict)
                and isinstance(candidate_aggregation, dict)
                and candidate_group.get("transform") == "date_trunc_month"
                and candidate_aggregation.get("function") == "count"
                and isinstance(candidate_group.get("source_alias"), str)
                and isinstance(candidate_group.get("field"), str)
                and isinstance(candidate_aggregation.get("source_alias"), str)
                and isinstance(candidate_aggregation.get("field"), str)
                and candidate_group["source_alias"]
                == candidate_aggregation["source_alias"]
            ):
                logger.info(
                    "Skipped grouped retry canonicalization "
                    "reason=unsupported_candidate_expression"
                )
                return candidate

            source_alias = candidate_group["source_alias"]
            source = next(
                (
                    item
                    for item in candidate.get("sources", [])
                    if isinstance(item, dict) and item.get("alias") == source_alias
                ),
                None,
            )
            if source is None:
                logger.info(
                    "Skipped grouped retry canonicalization "
                    "reason=source_alias_not_declared source_alias=%s",
                    source_alias,
                )
                return candidate

            group = {
                "field": candidate_group["field"],
                "transform": candidate_group["transform"],
            }
            aggregation = {
                "function": candidate_aggregation["function"],
                "field": candidate_aggregation["field"],
                **(
                    {"alias": candidate_aggregation["alias"]}
                    if isinstance(candidate_aggregation.get("alias"), str)
                    else {}
                ),
            }

        source_alias = str(source["alias"])
        expression = {
            "source_alias": source_alias,
            "field": group["field"],
            **(
                {"transform": group["transform"]}
                if group.get("transform") is not None
                else {}
            ),
        }
        projection = {
            **expression,
            **(
                {"alias": "month"}
                if group.get("transform") == "date_trunc_month"
                else {}
            ),
        }
        candidate_limit = candidate.get("limit")
        if (
            not isinstance(candidate_limit, int)
            or isinstance(candidate_limit, bool)
            or candidate_limit <= 0
            or candidate_limit > self.settings.query_max_limit
        ):
            candidate_limit = self.settings.query_default_limit

        canonical = {
            **candidate,
            "projections": [projection],
            "aggregations": [{
                "function": aggregation["function"],
                "source_alias": source_alias,
                "field": aggregation["field"],
                **(
                    {"alias": aggregation["alias"]}
                    if aggregation.get("alias") is not None
                    else {}
                ),
            }],
            "group_by": [expression],
            "limit": candidate_limit,
        }
        logger.info(
            "Canonicalized grouped retry validation_code=%s repair_source=%s "
            "source_alias=%s field=%s transform=%s limit=%s",
            validation_code,
            repair_source,
            source_alias,
            group["field"],
            group.get("transform"),
            candidate_limit,
        )
        return canonical

    def _canonicalize_mongodb_global_count(
        self,
        candidate: dict[str, Any],
        question: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        requirements = self._semantic_requirements(question, context)
        aggregation = requirements.get("aggregation")
        asset_id = requirements.get("asset_id")
        if not (
            requirements.get("intent") == "count"
            and requirements.get("strict_mongodb_count_shape") is True
            and isinstance(asset_id, str)
            and isinstance(aggregation, dict)
            and aggregation.get("function") == "count"
            and isinstance(aggregation.get("field"), str)
            and isinstance(aggregation.get("alias"), str)
            and requirements.get("filter") is None
            and requirements.get("group_by") is None
            and not requirements.get("ordering_requested")
            and requirements.get("mongodb_array_shape_code") is None
        ):
            logger.info(
                "Skipped MongoDB global-count canonicalization "
                "reason=non_global_or_ambiguous_intent"
            )
            return candidate

        selected_asset = next(
            (
                asset
                for asset in context.get("assets", [])
                if isinstance(asset, dict) and asset.get("asset_id") == asset_id
            ),
            None,
        )
        if not (
            isinstance(selected_asset, dict)
            and selected_asset.get("backend") == "mongodb"
            and aggregation["field"]
            in {
                str(field.get("name"))
                for field in selected_asset.get("fields", [])
                if isinstance(field, dict)
            }
        ):
            logger.info(
                "Skipped MongoDB global-count canonicalization "
                "reason=asset_or_count_field_not_catalogued"
            )
            return candidate

        sources = candidate.get("sources", [])
        if (
            len(sources) != 1
            or not isinstance(sources[0], dict)
            or sources[0].get("asset_id") != asset_id
            or not isinstance(sources[0].get("alias"), str)
        ):
            logger.info(
                "Skipped MongoDB global-count canonicalization "
                "reason=unsupported_source_shape"
            )
            return candidate

        source = sources[0]
        source_alias = source["alias"]
        canonical = {
            **candidate,
            "sources": [source],
            "joins": [],
            "projections": [],
            "filters": [],
            "aggregations": [{
                "function": "count",
                "source_alias": source_alias,
                "field": aggregation["field"],
                "alias": aggregation["alias"],
            }],
            "group_by": [],
            "unwinds": [],
            "array_matches": [],
            "order_by": [],
            "limit": None,
        }
        logger.info(
            "Canonicalized MongoDB global count source_alias=%s field=%s alias=%s",
            source_alias,
            aggregation["field"],
            aggregation["alias"],
        )
        return canonical

    def _canonicalize_mongodb_profiles_by_role(
        self,
        candidate: dict[str, Any],
        question: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        requirements = self._semantic_requirements(question, context)
        asset_id = requirements.get("asset_id")
        if not (
            requirements.get("intent") == "count"
            and requirements.get("mongodb_array_shape_code")
            == "mongodb_profiles_by_role_mismatch"
            and isinstance(asset_id, str)
            and requirements.get("unwind") == {"field": "roles"}
            and requirements.get("projection")
            == {"field": "roles[]", "alias": "role"}
            and requirements.get("aggregation")
            == {"function": "count", "field": "_id", "alias": "profiles"}
            and requirements.get("group_by") == {"field": "roles[]"}
            and requirements.get("filter") is None
            and not requirements.get("ordering_requested")
        ):
            logger.info(
                "Skipped MongoDB profiles-by-role canonicalization "
                "reason=non_grouped_or_ambiguous_intent"
            )
            return candidate

        selected_asset = next(
            (
                asset
                for asset in context.get("assets", [])
                if isinstance(asset, dict) and asset.get("asset_id") == asset_id
            ),
            None,
        )
        catalog_fields = {
            str(field.get("name"))
            for field in selected_asset.get("fields", [])
            if isinstance(field, dict)
        } if isinstance(selected_asset, dict) else set()
        if not (
            isinstance(selected_asset, dict)
            and selected_asset.get("backend") == "mongodb"
            and {"_id", "roles", "roles[]"}.issubset(catalog_fields)
        ):
            logger.info(
                "Skipped MongoDB profiles-by-role canonicalization "
                "reason=asset_or_fields_not_catalogued"
            )
            return candidate

        sources = candidate.get("sources", [])
        if (
            len(sources) != 1
            or not isinstance(sources[0], dict)
            or sources[0].get("asset_id") != asset_id
            or not isinstance(sources[0].get("alias"), str)
        ):
            logger.info(
                "Skipped MongoDB profiles-by-role canonicalization "
                "reason=unsupported_source_shape"
            )
            return candidate

        source = sources[0]
        source_alias = source["alias"]
        canonical = {
            **candidate,
            "sources": [source],
            "joins": [],
            "unwinds": [{
                "source_alias": source_alias,
                "field": "roles",
                "preserve_null_and_empty_arrays": False,
            }],
            "array_matches": [],
            "projections": [{
                "source_alias": source_alias,
                "field": "roles[]",
                "alias": "role",
            }],
            "filters": [],
            "aggregations": [{
                "function": "count",
                "source_alias": source_alias,
                "field": "_id",
                "alias": "profiles",
            }],
            "group_by": [{
                "source_alias": source_alias,
                "field": "roles[]",
            }],
            "order_by": [],
            "limit": None,
        }
        logger.info(
            "Canonicalized MongoDB profiles by role source_alias=%s",
            source_alias,
        )
        return canonical

    def _canonicalize_mongodb_recent_events_top_k(
        self,
        candidate: dict[str, Any],
        question: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        requirements = self._semantic_requirements(question, context)
        asset_id = requirements.get("asset_id")
        projections = requirements.get("projections")
        order_by = requirements.get("order_by")
        limit = requirements.get("limit")
        if not (
            requirements.get("intent") == "row_returning"
            and requirements.get("strict_mongodb_recent_top_k") is True
            and isinstance(asset_id, str)
            and projections == ["type", "user_id", "created_at"]
            and order_by == {"field": "created_at", "direction": "desc"}
            and isinstance(limit, int)
            and not isinstance(limit, bool)
            and 0 < limit <= self.settings.query_max_limit
            and requirements.get("filter") is None
            and requirements.get("group_by") is None
            and requirements.get("mongodb_array_shape_code") is None
        ):
            return candidate

        selected_asset = next(
            (
                asset
                for asset in context.get("assets", [])
                if isinstance(asset, dict) and asset.get("asset_id") == asset_id
            ),
            None,
        )
        catalog_fields = {
            str(field.get("name"))
            for field in selected_asset.get("fields", [])
            if isinstance(field, dict)
        } if isinstance(selected_asset, dict) else set()
        if not (
            isinstance(selected_asset, dict)
            and selected_asset.get("backend") == "mongodb"
            and set(projections).issubset(catalog_fields)
        ):
            logger.info(
                "Skipped MongoDB recent-events canonicalization "
                "reason=asset_or_fields_not_catalogued"
            )
            return candidate

        sources = candidate.get("sources", [])
        if (
            len(sources) != 1
            or not isinstance(sources[0], dict)
            or sources[0].get("asset_id") != asset_id
            or not isinstance(sources[0].get("alias"), str)
        ):
            logger.info(
                "Skipped MongoDB recent-events canonicalization "
                "reason=unsupported_source_shape"
            )
            return candidate

        source = sources[0]
        source_alias = source["alias"]
        canonical = {
            **candidate,
            "sources": [source],
            "joins": [],
            "unwinds": [],
            "array_matches": [],
            "projections": [
                {
                    "source_alias": source_alias,
                    "field": field,
                    "alias": field,
                }
                for field in projections
            ],
            "filters": [],
            "aggregations": [],
            "group_by": [],
            "order_by": [{
                "field": order_by["field"],
                "direction": order_by["direction"],
            }],
            "limit": limit,
        }
        logger.info(
            "Canonicalized MongoDB recent events top-k source_alias=%s limit=%s",
            source_alias,
            limit,
        )
        return canonical

    def _retry_invalid_plan(
        self,
        messages: list[dict[str, str]],
        candidate: dict[str, Any],
        validation_code: str,
        context: dict[str, Any],
        question: str,
    ) -> dict[str, Any]:
        selected_ids = {
            source.get("asset_id")
            for source in candidate.get("sources", [])
            if isinstance(source, dict) and isinstance(source.get("asset_id"), str)
        }
        selected_assets = [
            asset for asset in context["assets"] if asset["asset_id"] in selected_ids
        ]
        instruction = (
            "Correct only the LogicalQueryPlan: remove unnecessary assets, joins, "
            "and metrics; correct output aliases and join order; then return only "
            "the corrected plan object."
        )
        if validation_code == "field_not_found":
            instruction = (
                "Regenerate only the LogicalQueryPlan using the selected asset and only "
                "its exact valid_fields. Do not copy fields from an asset with the same "
                "name. Honor semantic_requirements, remove unrequested fields and filters, "
                "and return only the corrected plan object."
            )
        elif validation_code == "source_alias_not_found":
            instruction = (
                "Correct only alias consistency in the LogicalQueryPlan. Every reference must "
                "exactly match a source alias. If one source declaration is an obvious typo and "
                "all references consistently use the intended alias, correct that declaration; "
                "otherwise use an exact declared_source_alias. Preserve the original intent, "
                "fields, filters, aggregations and ordering. Return only the corrected plan object."
            )
        elif validation_code == "mongodb_aggregation_source_alias_mismatch":
            instruction = (
                "Correct only MongoDB aggregation source_alias values. Every aggregation must "
                "include source_alias set exactly to the alias declared in sources. Preserve "
                "the numeric filter and keep projections, group_by and order_by unchanged. "
                "Return only the corrected plan object."
            )
        elif validation_code == "mongodb_quantity_by_sku_mismatch":
            instruction = (
                "Correct only the MongoDB LogicalQueryPlan for the catalog-scoped "
                "quantity-by-SKU intent. Unwind exactly items; project items[].sku as sku; "
                "group by items[].sku; sum items[].quantity as quantity. Do not use "
                "properties.amount, array_matches, filters or extra fields. Return only "
                "the corrected plan object."
            )
        elif validation_code == "mongodb_profiles_by_role_mismatch":
            instruction = (
                "Correct only the MongoDB LogicalQueryPlan for the catalog-scoped "
                "profiles-by-role count. Unwind exactly roles; project roles[] as role; "
                "group by roles[]; count _id as profiles. Do not use array_matches, "
                "filters or extra fields. Return only the corrected plan object."
            )
        elif validation_code == "mongodb_filtered_item_quantity_sum_mismatch":
            instruction = (
                "Correct only the MongoDB LogicalQueryPlan for the catalog-scoped "
                "filtered item-quantity sum. Unwind exactly items; add exactly one "
                "array_match on items with the quantity predicate from "
                "semantic_requirements; sum items[].quantity as quantity. Do not "
                "project or group fields, do not count documents, and do not use "
                "properties.amount, filters or order_by. Return only the corrected "
                "plan object."
            )
        elif validation_code == "mongodb_events_recent_top_k_mismatch":
            instruction = (
                "Correct only the MongoDB row-returning LogicalQueryPlan from "
                "semantic_requirements. Project exactly type, user_id and created_at; "
                "order by the created_at output descending; apply the requested bounded "
                "limit; use no filters, aggregations, grouping or array operations. "
                "Return only the corrected plan object."
            )
        semantic_requirements = self._semantic_requirements(question, context)
        if (
            semantic_requirements.get("intent") == "row_returning"
            and validation_code in {
                "missing_explicit_filter",
                "row_intent_mismatch",
                "row_filter_mismatch",
                "row_projection_mismatch",
                "row_order_by_mismatch",
                "row_limit_mismatch",
            }
        ):
            instruction = (
                "Correct only the LogicalQueryPlan while preserving the original row-returning "
                "intent. Use exactly the projections, filters, order_by and limit declared in "
                "semantic_requirements; an absent filter means filters must be empty. A requested "
                "cardinality is a limit, never a filter threshold. Use no aggregations or group_by. "
                "Do not reuse a count or category structure from another request. "
                "Return only the corrected plan object."
            )
        elif validation_code in {
            "missing_explicit_filter",
            "unrequested_categories",
            "filtered_count_mismatch",
        }:
            instruction = (
                "Correct only the LogicalQueryPlan. Preserve the explicit categorical "
                "condition as the required eq filter. Remove category projections and "
                "group_by that were not requested, then return only the corrected plan object."
            )
        elif validation_code == "metric_aggregation_mismatch":
            instruction = (
                "Correct only the LogicalQueryPlan using the catalog-scoped required "
                "aggregation and field. Do not add group_by, then return only the corrected plan object."
            )
        elif validation_code == "semantic_asset_mismatch":
            instruction = (
                "Correct only the LogicalQueryPlan using the catalog-scoped required asset, "
                "rebuild its shape from semantic_requirements without fragments from the previous "
                "asset, then return only the corrected plan object."
            )
        elif validation_code == "catalog_resolved_question":
            instruction = (
                "The catalog-scoped semantic requirements resolve the question. Generate only "
                "the corresponding LogicalQueryPlan and return only the plan object."
            )
        elif validation_code == "duckdb_grouped_count_mismatch":
            instruction = (
                "Regenerate only the compact DuckDB LogicalQueryPlan from semantic_requirements. "
                "Use exactly one projection and the identical group_by expression, exactly one "
                "count aggregation, and no extra sources, fields or metrics. Return only the plan object."
            )
        elif (
            validation_code == "invalid_group_by"
            and semantic_requirements.get("group_by")
        ):
            instruction = (
                "Regenerate only the grouped LogicalQueryPlan from semantic_requirements. "
                "Use exactly one non-aggregated projection and an identical group_by expression "
                "with the same source_alias, physical field and transform. Use exactly one count "
                "aggregation and no extra projections, fields or metrics. Return only the plan object."
            )
        elif validation_code in {
            "mongodb_filter_mismatch",
            "mongodb_row_projection_mismatch",
            "mongodb_count_intent_mismatch",
            "mongodb_scalar_aggregation_mismatch",
        }:
            instruction = (
                "Regenerate only the MongoDB LogicalQueryPlan from a clean state using the exact "
                "catalog-scoped semantic_requirements. Remove every unrequested projection, "
                "filter, aggregation, group_by and order_by, and do not reuse fragments from "
                "another request. Return only the corrected plan object."
            )
        retry_messages = [
            *messages,
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "instruction": instruction,
                        "previous_plan": candidate,
                        "validation_code": validation_code,
                        "query_intent": semantic_requirements.get("intent"),
                        "declared_source_aliases": [
                            source["alias"]
                            for source in candidate.get("sources", [])
                            if isinstance(source, dict)
                            and isinstance(source.get("alias"), str)
                        ],
                        "referenced_source_aliases": sorted({
                            alias
                            for section in (
                                "projections", "filters", "aggregations", "group_by",
                                "unwinds", "array_matches",
                            )
                            for item in candidate.get(section, [])
                            if isinstance(item, dict)
                            for alias in [item.get("source_alias")]
                            if isinstance(alias, str)
                        }),
                        "semantic_requirements": semantic_requirements,
                        "selected_assets": [
                            {
                                "asset_id": asset["asset_id"],
                                "asset_version_id": asset["asset_version_id"],
                                "backend": asset["backend"],
                                "valid_fields": [field["name"] for field in asset["fields"]],
                            }
                            for asset in selected_assets
                        ],
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
        try:
            return self.client.chat_json(
                messages, self._planning_format()
            ).content
        except OllamaInvalidResponseError:
            raise
        except OllamaTimeoutError as exc:
            raise NaturalLanguageQueryError(
                "llm_timeout", "Ollama request timed out", 504
            ) from exc
        except (OllamaUnavailableError, OllamaModelNotFoundError) as exc:
            raise self._llm_unavailable(exc, "planning") from exc

    def _planning_format(self) -> dict[str, Any] | str:
        if self.settings.ollama_planning_format == "json":
            return "json"
        return LogicalQueryPlan.model_json_schema()

    @staticmethod
    def _llm_unavailable(
        exc: OllamaUnavailableError | OllamaModelNotFoundError,
        stage: str,
    ) -> NaturalLanguageQueryError:
        logger.warning(
            "Ollama failure mapped to llm_unavailable stage=%s exception_type=%s",
            stage,
            type(exc).__name__,
        )
        return NaturalLanguageQueryError(
            "llm_unavailable", "Ollama is unavailable", 503
        )

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
                candidate = {
                    "asset_id": asset.id,
                    "asset_version_id": version.id,
                    "logical_name": asset.name,
                    "backend": "duckdb",
                    "source_id": "manual_upload",
                    "source_name": "Local dataset upload",
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
                candidate["semantic_field_hints"] = self._semantic_field_hints(candidate)
                candidate["semantic_entity_terms"] = self._semantic_entity_terms(candidate)
                candidate["semantic_metric_hints"] = self._semantic_metric_hints(candidate)
                candidates.append(candidate)
                break
        for asset in self.query_service.mysql_catalog.list_ready_assets():
            source = SourceRegistry(self.settings).get_source(asset.source_id)
            candidate = {
                "asset_id": asset.asset_id,
                "asset_version_id": asset.asset_version_id,
                "logical_name": asset.name,
                "backend": "mysql",
                "source_id": asset.source_id,
                "source_name": source.name if source is not None else asset.source_id,
                "fields": [
                    {
                        "name": field["name"],
                        "data_type": field["data_type"],
                        "nullable": field["nullable"],
                    }
                    for field in asset.fields.values()
                ],
            }
            candidate["semantic_field_hints"] = self._semantic_field_hints(candidate)
            candidate["semantic_entity_terms"] = self._semantic_entity_terms(candidate)
            candidate["semantic_metric_hints"] = self._semantic_metric_hints(candidate)
            candidates.append(candidate)
        for asset in self.query_service.mongodb_catalog.list_ready_assets():
            source = SourceRegistry(self.settings).get_source(asset.source_id)
            candidate = {
                "asset_id": asset.asset_id,
                "asset_version_id": asset.asset_version_id,
                "logical_name": asset.name,
                "backend": "mongodb",
                "source_id": asset.source_id,
                "source_name": source.name if source is not None else asset.source_id,
                "fields": list(asset.fields.values()),
            }
            candidate["semantic_field_hints"] = self._semantic_field_hints(candidate)
            candidate["semantic_entity_terms"] = self._semantic_entity_terms(candidate)
            candidate["semantic_metric_hints"] = self._semantic_metric_hints(candidate)
            candidates.append(candidate)
        if re.search(r"\b(?:csv|file|duckdb)\b", question, re.IGNORECASE):
            candidates = [asset for asset in candidates if asset["backend"] == "duckdb"]
        elif re.search(r"\bmysql\b", question, re.IGNORECASE):
            candidates = [asset for asset in candidates if asset["backend"] == "mysql"]
        elif re.search(r"\bmongo(?:db)?\b", question, re.IGNORECASE):
            candidates = [asset for asset in candidates if asset["backend"] == "mongodb"]
        active_relationships = [
            relationship for relationship in self.storage.list_relationships(False)
        ]
        tokens = {
            token for token in re.findall(r"[\w]+", question.casefold()) if len(token) >= 3
        }
        scores: dict[str, int] = {}
        for asset in candidates:
            hint_terms = [
                term
                for hint in asset["semantic_field_hints"]
                for term in hint["terms"]
            ]
            metric_terms = [
                term
                for hint in asset["semantic_metric_hints"]
                for term in hint["terms"]
            ]
            searchable = " ".join(
                [
                    asset["logical_name"],
                    *(field["name"] for field in asset["fields"]),
                    *asset["semantic_entity_terms"],
                    *hint_terms,
                    *metric_terms,
                ]
            ).casefold()
            searchable_tokens = set(re.findall(r"[\w]+", searchable))
            scores[asset["asset_id"]] = sum(
                token in searchable_tokens for token in tokens
            )
        matched = {asset_id for asset_id, score in scores.items() if score > 0}
        if matched:
            for relationship in active_relationships:
                if relationship.left_asset_id in matched or relationship.right_asset_id in matched:
                    matched.update({relationship.left_asset_id, relationship.right_asset_id})
            candidates = [asset for asset in candidates if asset["asset_id"] in matched]
        candidates.sort(key=lambda asset: (-scores.get(asset["asset_id"], 0), asset["logical_name"], asset["asset_id"]))
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

    @staticmethod
    def _semantic_field_hints(asset: dict[str, Any]) -> list[dict[str, Any]]:
        fields = {field["name"].casefold() for field in asset["fields"]}
        if (
            asset.get("backend") == "mongodb"
            and asset["logical_name"].casefold() == "profiles"
        ):
            hints: list[dict[str, Any]] = []
            if "preferences.newsletter" in fields:
                hints.append({
                    "terms": [
                        "newsletter",
                        "newsletter attiva",
                        "newsletter abilitata",
                        "newsletter disattiva",
                        "newsletter non attiva",
                    ],
                    "field": "preferences.newsletter",
                    "reason": "the newsletter flag is nested in the observed profiles schema",
                })
            if "preferences.language" in fields:
                hints.append({
                    "terms": [
                        "lingua",
                        "lingua inglese",
                        "lingua italiana",
                        "language",
                        "English language",
                        "Italian language",
                    ],
                    "field": "preferences.language",
                    "reason": "the language code is nested in the observed profiles schema",
                })
            return hints
        if asset.get("backend") == "mongodb" and asset["logical_name"].casefold() == "events":
            hints: list[dict[str, Any]] = []
            if "type" in fields:
                hints.append({
                    "terms": ["tipo", "per tipo", "type"],
                    "field": "type",
                    "reason": "type is the observed event category field",
                })
            if "user_id" in fields:
                hints.append({
                    "terms": ["utente", "user", "user id"],
                    "field": "user_id",
                    "reason": "user_id is the observed event user field",
                })
            if "properties.amount" in fields:
                hints.append({
                    "terms": ["importo", "amount"],
                    "field": "properties.amount",
                    "reason": "properties.amount is the observed event amount field",
                })
            return hints
        if (
            asset["logical_name"].casefold() == "orders"
            and "status" in fields
            and not fields.intersection({"state", "customer_state", "shipping_state"})
        ):
            return [{
                "terms": ["stato", "stato ordine", "stato dell'ordine"],
                "field": "status",
                "reason": "status is the only order-state field in this asset",
            }]
        if (
            asset.get("backend") == "duckdb"
            and asset["logical_name"].casefold() == "orders"
            and "order_status" in fields
        ):
            return [{
                "terms": ["stato", "stato ordine", "order_status"],
                "field": "order_status",
                "reason": "order_status is the observed status field in this file dataset",
            }]
        return []

    @staticmethod
    def _semantic_entity_terms(asset: dict[str, Any]) -> list[str]:
        logical_name = asset["logical_name"].casefold()
        if logical_name == "orders":
            return ["ordine", "ordini"]
        if asset.get("backend") == "mongodb" and logical_name == "profiles":
            return ["profilo", "profili", "profile", "profiles"]
        if asset.get("backend") == "mongodb" and logical_name == "events":
            return ["evento", "eventi", "event", "events"]
        return []

    @staticmethod
    def _semantic_metric_hints(asset: dict[str, Any]) -> list[dict[str, Any]]:
        fields = {
            field["name"].casefold(): str(field["data_type"]).casefold()
            for field in asset["fields"]
        }
        numeric_tokens = ("int", "decimal", "numeric", "float", "double", "real")
        hints: list[dict[str, Any]] = []
        total_type = fields.get("total", "")
        if any(numeric in total_type for numeric in numeric_tokens):
            hints.extend([
                {
                    "terms": ["totale medio", "media del totale", "valore medio"],
                    "field": "total",
                    "aggregation": "avg",
                },
                {
                    "terms": ["valore totale", "somma dei totali", "totale complessivo"],
                    "field": "total",
                    "aggregation": "sum",
                },
            ])
        amount_type = fields.get("properties.amount", "")
        if (
            asset.get("backend") == "mongodb"
            and asset["logical_name"].casefold() == "events"
            and any(numeric in amount_type for numeric in numeric_tokens)
        ):
            hints.extend([
                {
                    "terms": ["importo totale", "somma degli importi", "total amount"],
                    "field": "properties.amount",
                    "aggregation": "sum",
                    "alias": "total",
                },
                {
                    "terms": ["importo medio", "media degli importi", "average amount"],
                    "field": "properties.amount",
                    "aggregation": "avg",
                    "alias": "avg_amount",
                },
            ])
        return hints

    @staticmethod
    def _semantic_requirements(
        question: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        normalized = question.casefold()
        assets = [
            asset
            for asset in context["assets"]
            if any(
                re.search(rf"\b{re.escape(term)}\b", normalized)
                for term in asset.get("semantic_entity_terms", [])
            )
        ]
        if (
            not assets
            and len(context["assets"]) == 1
            and re.search(r"\b(?:csv|file|duckdb)\b", normalized)
        ):
            assets = list(context["assets"])
        if len(assets) != 1:
            return {}
        asset = assets[0]
        requirements: dict[str, Any] = {"asset_id": asset["asset_id"]}
        intent = NaturalLanguageQueryService._question_intent(normalized)
        if intent is not None:
            requirements["intent"] = intent
        if intent in {"row_returning", "count"}:
            requirements["ordering_requested"] = (
                NaturalLanguageQueryService._ordering_requested(normalized)
            )
        if intent == "row_returning":
            requested_order = NaturalLanguageQueryService._requested_ordering(
                normalized, asset
            )
            if requested_order is not None:
                requirements["order_by"] = requested_order
                requested_limit = NaturalLanguageQueryService._requested_limit(
                    normalized
                )
                if requested_limit is not None:
                    requirements["limit"] = requested_limit
        for hint in asset.get("semantic_metric_hints", []):
            if any(term in normalized for term in hint["terms"]):
                requirements["aggregation"] = {
                    "function": hint["aggregation"],
                    "field": hint["field"],
                    **({"alias": hint["alias"]} if hint.get("alias") else {}),
                }
                break
        explicit_status = re.search(
            r"\b(?:con|ha|hanno)\s+(?:lo\s+)?stato\s+([\w-]+)", normalized
        )
        if explicit_status:
            status_fields = {
                hint["field"]
                for hint in asset.get("semantic_field_hints", [])
                if "stato" in hint["terms"]
            }
            if status_fields == {"status"}:
                requirements["filter"] = {
                    "field": "status",
                    "operator": "eq",
                    "value": explicit_status.group(1),
                }
                if intent == "count":
                    requirements["aggregation"] = {
                        "function": "count",
                        "field": "id",
                        "alias": "orders",
                    }
        if asset.get("backend") == "duckdb":
            fields = {str(field["name"]): field for field in asset["fields"]}
            if intent == "row_returning":
                requirements["strict_row_shape"] = True
                explicit_projections = (
                    NaturalLanguageQueryService._explicit_projection_fields(
                        normalized, asset
                    )
                )
                if explicit_projections:
                    requirements["explicit_projections"] = True
                requirements["projections"] = (
                    explicit_projections
                    or [
                        field
                        for field in (
                            "order_id", "order_status", "order_purchase_timestamp"
                        )
                        if field in fields
                    ]
                )
                status_match = re.search(
                    r"\bcon\s+order_status\s+([\w-]+)\b", normalized
                )
                if status_match is not None and "order_status" in fields:
                    requirements["filter"] = {
                        "field": "order_status",
                        "operator": "eq",
                        "value": status_match.group(1),
                    }
            if intent == "count" and {"order_id", "order_status"}.issubset(fields):
                if re.search(r"\bper\s+(?:ciascun\s+)?order_status\b", normalized):
                    requirements["aggregation"] = {
                        "function": "count", "field": "order_id", "alias": "orders"
                    }
                    requirements["group_by"] = {"field": "order_status"}
                    requirements["strict_duckdb_grouped_count"] = True
            if (
                intent == "count"
                and "order_id" in fields
                and "order_purchase_timestamp" in fields
                and re.search(r"\bper\s+mese\b", normalized)
            ):
                requirements["aggregation"] = {
                    "function": "count", "field": "order_id", "alias": "orders"
                }
                requirements["group_by"] = {
                    "field": "order_purchase_timestamp",
                    "transform": "date_trunc_month",
                }
                requirements["strict_duckdb_grouped_count"] = True
        if intent == "row_returning" and asset.get("backend") == "mysql":
            requirements["strict_row_shape"] = True
            numeric_filter = NaturalLanguageQueryService._numeric_filter(
                normalized, asset
            )
            if numeric_filter is not None:
                requirements["filter"] = numeric_filter
            requirements["projections"] = (
                NaturalLanguageQueryService._explicit_projection_fields(
                    normalized, asset
                )
                or NaturalLanguageQueryService._default_row_fields(asset)
            )
        if asset.get("backend") == "mongodb" and intent == "count":
            fields = {str(field["name"]): field for field in asset["fields"]}
            if "_id" in fields:
                requirements["aggregation"] = {
                    "function": "count",
                    "field": "_id",
                    "alias": asset["logical_name"],
                }
                requirements["strict_mongodb_count_shape"] = True
                if (
                    {"roles", "roles[]"}.issubset(fields)
                    and not NaturalLanguageQueryService._count_per_role_intent(
                        normalized
                    )
                ):
                    requirements["strict_mongodb_no_array_ops"] = True
            if (
                "type" in fields
                and NaturalLanguageQueryService._count_per_type_intent(normalized)
            ):
                requirements["group_by"] = {"field": "type"}
            user_filter = NaturalLanguageQueryService._mongodb_event_user_filter(
                normalized, fields
            )
            if asset["logical_name"].casefold() == "events" and user_filter:
                requirements["filter"] = user_filter
                requirements["exact_filter"] = True
            if (
                {"roles", "roles[]"}.issubset(fields)
                and NaturalLanguageQueryService._count_per_role_intent(normalized)
            ):
                requirements.update({
                    "unwind": {"field": "roles"},
                    "projection": {"field": "roles[]", "alias": "role"},
                    "aggregation": {
                        "function": "count",
                        "field": "_id",
                        "alias": "profiles",
                    },
                    "group_by": {"field": "roles[]"},
                    "mongodb_array_shape_code": (
                        "mongodb_profiles_by_role_mismatch"
                    ),
                })
        if asset.get("backend") == "mongodb":
            fields = {str(field["name"]): field for field in asset["fields"]}
            if (
                {"items", "items[].sku", "items[].quantity"}.issubset(fields)
                and NaturalLanguageQueryService._quantity_per_sku_intent(normalized)
            ):
                requirements.update({
                    "unwind": {"field": "items"},
                    "projection": {"field": "items[].sku", "alias": "sku"},
                    "aggregation": {
                        "function": "sum",
                        "field": "items[].quantity",
                        "alias": "quantity",
                    },
                    "group_by": {"field": "items[].sku"},
                    "mongodb_array_shape_code": (
                        "mongodb_quantity_by_sku_mismatch"
                    ),
                })
            filtered_quantity = (
                NaturalLanguageQueryService._filtered_item_quantity_sum(
                    normalized
                )
            )
            if (
                intent != "count"
                and {"items", "items[]", "items[].quantity"}.issubset(fields)
                and filtered_quantity is not None
                and not requirements.get("mongodb_array_shape_code")
            ):
                requirements.update({
                    "unwind": {"field": "items"},
                    "projection": None,
                    "aggregation": {
                        "function": "sum",
                        "field": "items[].quantity",
                        "alias": "quantity",
                    },
                    "group_by": None,
                    "array_match": {
                        "field": "items",
                        "predicate": {
                            "field": "quantity",
                            "operator": "gte",
                            "value": filtered_quantity,
                        },
                    },
                    "mongodb_array_shape_code": (
                        "mongodb_filtered_item_quantity_sum_mismatch"
                    ),
                })
            if (
                intent == "row_returning"
                and {"type", "user_id", "created_at"}.issubset(fields)
                and NaturalLanguageQueryService._recent_events_intent(normalized)
            ):
                requested_limit = NaturalLanguageQueryService._requested_limit(
                    normalized
                )
                if requested_limit is not None:
                    requirements.update({
                        "strict_row_shape": True,
                        "strict_mongodb_recent_top_k": True,
                        "projections": ["type", "user_id", "created_at"],
                        "order_by": {
                            "field": "created_at",
                            "direction": "desc",
                        },
                        "limit": requested_limit,
                    })
        if (
            asset.get("backend") == "mongodb"
            and asset["logical_name"].casefold() == "events"
            and requirements.get("aggregation", {}).get("function") in {"sum", "avg"}
            and intent != "count"
            and not requirements.get("mongodb_array_shape_code")
        ):
            requirements["strict_mongodb_scalar_aggregation"] = True
        if (
            asset.get("backend") == "mongodb"
            and asset["logical_name"].casefold() == "events"
            and intent == "row_returning"
        ):
            fields = {str(field["name"]): field for field in asset["fields"]}
            amount_filter = NaturalLanguageQueryService._mongodb_event_amount_filter(
                normalized, fields
            )
            if amount_filter is not None:
                requirements["filter"] = amount_filter
                requirements["exact_filter"] = True
        if (
            asset.get("backend") == "mongodb"
            and asset["logical_name"].casefold() == "profiles"
            and intent == "row_returning"
        ):
            newsletter_value = NaturalLanguageQueryService._newsletter_value(
                normalized
            )
            language_value = NaturalLanguageQueryService._language_value(normalized)
            fields = {str(field["name"]) for field in asset["fields"]}
            if newsletter_value is not None and "preferences.newsletter" in fields:
                requirements["filter"] = {
                    "field": "preferences.newsletter",
                    "operator": "eq",
                    "value": newsletter_value,
                }
                requirements["exact_filter"] = True
                requirements["strict_mongodb_row_shape"] = True
                requirements["required_projections"] = [
                    field
                    for field in ("email", "preferences.newsletter")
                    if field in fields
                ]
                requirements["allowed_projections"] = [
                    field
                    for field in (
                        "email",
                        "preferences.newsletter",
                        "preferences.language",
                    )
                    if field in fields
                ]
            elif language_value is not None and "preferences.language" in fields:
                requirements["filter"] = {
                    "field": "preferences.language",
                    "operator": "eq",
                    "value": language_value,
                }
                requirements["exact_filter"] = True
                requirements["strict_mongodb_row_shape"] = True
                requirements["required_projections"] = [
                    field
                    for field in ("email", "preferences.language")
                    if field in fields
                ]
                requirements["allowed_projections"] = list(
                    requirements["required_projections"]
                )
        return requirements

    @staticmethod
    def _quantity_per_sku_intent(normalized_question: str) -> bool:
        return bool(
            re.search(r"\b(?:quantit[aà]|quantity)\b", normalized_question)
            and re.search(
                r"\b(?:per|by|for)\s+(?:(?:ciascun|ogni|each)\s+)?sku\b",
                normalized_question,
            )
        )

    @staticmethod
    def _count_per_role_intent(normalized_question: str) -> bool:
        role = r"(?:ruolo|ruoli|role|roles)"
        return bool(
            re.search(
                rf"\b(?:per|by)\s+(?:(?:ciascun|ogni|each)\s+)?{role}\b",
                normalized_question,
            )
            or re.search(
                rf"\b(?:ciascun|ogni|each)\s+{role}\b",
                normalized_question,
            )
        )

    @staticmethod
    def _count_per_type_intent(normalized_question: str) -> bool:
        return bool(
            re.search(
                r"\b(?:per|by)\s+(?:(?:ciascun|ogni|each)\s+)?"
                r"(?:tipo|type)\b",
                normalized_question,
            )
            or (
                re.search(r"\b(?:raggruppa|group)\b", normalized_question)
                and re.search(r"\b(?:tipo|type)\b", normalized_question)
            )
        )

    @staticmethod
    def _filtered_item_quantity_sum(
        normalized_question: str,
    ) -> int | float | None:
        if not (
            re.search(r"\b(?:articol[oi]|items?)\b", normalized_question)
            and re.search(r"\b(?:quantit[aà]|quantity)\b", normalized_question)
            and re.search(
                r"\b(?:totale|complessiva|sum|total)\b",
                normalized_question,
            )
        ):
            return None
        threshold = re.search(
            r"(?:>=|maggiore\s+o\s+uguale\s+a|almeno|"
            r"greater\s+than\s+or\s+equal\s+to|at\s+least)\s*(\d+(?:\.\d+)?)",
            normalized_question,
        )
        if threshold is None:
            return None
        value = float(threshold.group(1))
        return int(value) if value.is_integer() else value

    @staticmethod
    def _recent_events_intent(normalized_question: str) -> bool:
        return bool(
            re.search(r"\b(?:eventi|events)\b", normalized_question)
            and re.search(
                r"\b(?:pi[uù]\s+recenti|most\s+recent|latest)\b",
                normalized_question,
            )
            and all(
                re.search(rf"\b{re.escape(field)}\b", normalized_question)
                for field in ("type", "user_id", "created_at")
            )
        )

    @staticmethod
    def _mongodb_event_user_filter(
        normalized_question: str, fields: dict[str, dict[str, Any]]
    ) -> dict[str, Any] | None:
        field = fields.get("user_id")
        match = re.search(r"\b(?:utente|user)\s+(\d+)\b", normalized_question)
        if field is None or match is None:
            return None
        data_type = str(field.get("data_type", "")).casefold()
        if not any(token in data_type for token in ("int", "numeric", "decimal")):
            return None
        return {"field": "user_id", "operator": "eq", "value": int(match.group(1))}

    @staticmethod
    def _mongodb_event_amount_filter(
        normalized_question: str, fields: dict[str, dict[str, Any]]
    ) -> dict[str, Any] | None:
        field = fields.get("properties.amount")
        match = re.search(
            r"\bimporto\s+(?:maggiore|superiore)\s+(?:(?:a|di)\s+)?"
            r"(-?\d+(?:\.\d+)?)\b",
            normalized_question,
        )
        if field is None or match is None:
            return None
        data_type = str(field.get("data_type", "")).casefold()
        if not any(
            token in data_type
            for token in ("int", "numeric", "decimal", "float", "double", "real")
        ):
            return None
        raw_value = match.group(1)
        value: int | float = float(raw_value) if "." in raw_value else int(raw_value)
        return {"field": "properties.amount", "operator": "gt", "value": value}

    @staticmethod
    def _newsletter_value(normalized_question: str) -> bool | None:
        if re.search(
            r"\bnewsletter\s+(?:disattiva|non\s+attiva|disabled|inactive)\b",
            normalized_question,
        ):
            return False
        if re.search(
            r"\bnewsletter\s+(?:attiva|abilitata|active|enabled)\b",
            normalized_question,
        ):
            return True
        return None

    @staticmethod
    def _language_value(normalized_question: str) -> str | None:
        if re.search(
            r"\b(?:lingua\s+inglese|english\s+language)\b",
            normalized_question,
        ):
            return "en"
        if re.search(
            r"\b(?:lingua\s+italiana|italian\s+language)\b",
            normalized_question,
        ):
            return "it"
        return None

    @staticmethod
    def _question_intent(normalized_question: str) -> str | None:
        if re.search(
            r"\b(quanti|conta(?:ndo|li|le)?|numero\s+di|how\s+many|count|number\s+of)\b",
            normalized_question,
        ):
            return "count"
        if re.search(
            r"\b(mostra|elenca|visualizza|dammi\s+gli\s+ordini|show|list|display)\b",
            normalized_question,
        ):
            return "row_returning"
        return None

    @staticmethod
    def _ordering_requested(normalized_question: str) -> bool:
        return bool(re.search(
            r"\b(ordina|ordinati|ordinate|ordine\s+(?:crescente|decrescente)|"
            r"crescente|decrescente|pi[uù]\s+recenti|meno\s+recenti|"
            r"pi[uù]\s+(?:alto|alti|elevato|elevati)|highest|top)\b",
            normalized_question,
        ))

    @staticmethod
    def _requested_ordering(
        normalized_question: str, asset: dict[str, Any]
    ) -> dict[str, str] | None:
        descending = re.search(
            r"\b(?:pi[uù]\s+(?:alto|alti|elevato|elevati)|highest|top)\b",
            normalized_question,
        )
        if descending is None:
            return None
        for field in asset["fields"]:
            name = str(field["name"])
            terms = {name.casefold(), name.casefold().replace("_", " ")}
            for hint in asset.get("semantic_field_hints", []):
                if hint.get("field") == name:
                    terms.update(
                        str(term).casefold() for term in hint.get("terms", [])
                    )
            if any(
                re.search(rf"\b{re.escape(term)}\b", normalized_question)
                for term in terms
            ):
                return {"field": name, "direction": "desc"}
        return None

    @staticmethod
    def _requested_limit(normalized_question: str) -> int | None:
        words = {
            "uno": 1,
            "due": 2,
            "tre": 3,
            "quattro": 4,
            "cinque": 5,
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
        }
        match = re.search(
            r"\b(?:top\s+)?(\d+|uno|due|tre|quattro|cinque|"
            r"one|two|three|four|five)\b",
            normalized_question,
        )
        if match is None:
            return None
        token = match.group(1)
        return int(token) if token.isdigit() else words[token]

    @staticmethod
    def _explicit_projection_fields(
        normalized_question: str, asset: dict[str, Any]
    ) -> list[str]:
        match = re.search(
            r"\b(?:mostra|elenca|visualizza)\s+(.+?)\s+"
            r"(?:(?:degli|delle|dei)\s+ordini\b|(?:del|nel)\s+dataset\b)",
            normalized_question,
        )
        if match is None:
            return []
        field_clause = match.group(1)
        if re.search(r"\b(?:gli|degli)\s+ordini\b", field_clause):
            return []
        candidates: list[tuple[int, str]] = []
        for field in asset["fields"]:
            name = str(field["name"])
            terms = {name.casefold(), name.casefold().replace("_", " ")}
            if name.casefold() == "total":
                terms.add("totale")
            for hint in asset.get("semantic_field_hints", []):
                if hint.get("field") == name:
                    terms.update(str(term).casefold() for term in hint.get("terms", []))
            positions = [
                found.start()
                for term in terms
                if (found := re.search(rf"\b{re.escape(term)}\b", field_clause))
            ]
            if positions:
                candidates.append((min(positions), name))
        return [name for _, name in sorted(candidates)]

    @staticmethod
    def _default_row_fields(asset: dict[str, Any]) -> list[str]:
        available = {str(field["name"]) for field in asset["fields"]}
        return [
            name
            for name in ("id", "customer_id", "status", "total", "created_at")
            if name in available
        ]

    @staticmethod
    def _numeric_filter(
        normalized_question: str, asset: dict[str, Any]
    ) -> dict[str, Any] | None:
        fields = {
            str(field["name"]).casefold(): str(field["data_type"]).casefold()
            for field in asset["fields"]
        }
        if "total" not in fields or not any(
            token in fields["total"]
            for token in ("int", "decimal", "numeric", "float", "double", "real")
        ):
            return None
        comparisons = (
            (r"\btotale\s+(?:inferiore|minore)\s+(?:(?:a|di)\s+)?(-?\d+(?:\.\d+)?)", "lt"),
            (r"\btotale\s+(?:superiore|maggiore)\s+(?:(?:a|di)\s+)?(-?\d+(?:\.\d+)?)", "gt"),
        )
        for pattern, operator in comparisons:
            match = re.search(pattern, normalized_question)
            if match is not None:
                raw_value = match.group(1)
                value: int | float = (
                    float(raw_value) if "." in raw_value else int(raw_value)
                )
                return {"field": "total", "operator": operator, "value": value}
        return None

    @staticmethod
    def _apply_catalog_disambiguation(
        question: str,
        context: dict[str, Any],
        classification: NaturalLanguageClassification,
    ) -> NaturalLanguageClassification:
        if classification.classification != QueryClassification.AMBIGUOUS:
            return classification
        requirements = NaturalLanguageQueryService._semantic_requirements(
            question, context
        )
        if requirements.get("aggregation") or (
            requirements.get("intent") == "row_returning"
            and requirements.get("asset_id")
            and requirements.get("projections")
            and (
                requirements.get("explicit_projections")
                or requirements.get("filter")
            )
        ):
            return NaturalLanguageClassification(
                classification=QueryClassification.ANSWERABLE,
                reason="The catalog uniquely resolves the requested fields and operation.",
                clarification_question=None,
            )
        normalized = question.casefold()
        matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for asset in context["assets"]:
            entity_matches = any(
                re.search(rf"\b{re.escape(term)}\b", normalized)
                for term in asset.get("semantic_entity_terms", [])
            )
            if not entity_matches:
                continue
            for hint in asset.get("semantic_field_hints", []):
                if any(term in normalized for term in hint["terms"]):
                    matches.append((asset, hint))
        if len(matches) != 1:
            return classification
        asset, hint = matches[0]
        return NaturalLanguageClassification(
            classification=QueryClassification.ANSWERABLE,
            reason=(
                f"Catalog field '{hint['field']}' uniquely resolves the term for "
                f"asset '{asset['logical_name']}'."
            ),
            clarification_question=None,
        )

    @staticmethod
    def _duckdb_monthly_example(
        context: dict[str, Any]
    ) -> dict[str, Any] | None:
        asset = next(
            (
                item
                for item in context["assets"]
                if item["backend"] == "duckdb"
                and item["logical_name"].casefold() == "orders"
                and {"order_id", "order_purchase_timestamp"}.issubset(
                    {field["name"] for field in item["fields"]}
                )
            ),
            None,
        )
        if asset is None:
            return None
        return {
            "question": (
                "Quanti ordini del dataset CSV orders ci sono per mese di "
                "order_purchase_timestamp?"
            ),
            "plan": {
                "sources": [{"alias": "o", "asset_id": asset["asset_id"]}],
                "projections": [{
                    "source_alias": "o",
                    "field": "order_purchase_timestamp",
                    "transform": "date_trunc_month",
                    "alias": "month",
                }],
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
                "order_by": [{"field": "month", "direction": "asc"}],
            },
        }

    @staticmethod
    def _planning_examples(context: dict[str, Any]) -> list[dict[str, Any]]:
        examples: list[dict[str, Any]] = []
        for asset in context["assets"]:
            hints = asset.get("semantic_field_hints", [])
            fields = {field["name"] for field in asset["fields"]}
            if (
                asset["backend"] == "mysql"
                and any(hint.get("field") == "status" for hint in hints)
                and "id" in fields
            ):
                examples.append({
                    "question": "Quanti ordini ci sono per stato nel database MySQL?",
                    "plan": {
                        "sources": [{
                            "alias": "o",
                            "asset_id": asset["asset_id"],
                            "asset_version_id": asset["asset_version_id"],
                        }],
                        "projections": [{
                            "source_alias": "o", "field": "status", "alias": "status"
                        }],
                        "aggregations": [{
                            "function": "count", "source_alias": "o",
                            "field": "id", "alias": "orders",
                        }],
                        "group_by": [{"source_alias": "o", "field": "status"}],
                        "order_by": [{"field": "orders", "direction": "desc"}],
                    },
                })
                if asset.get("semantic_metric_hints"):
                    for function, question, alias in (
                        (
                            "avg",
                            "Qual è il totale medio degli ordini nel database MySQL?",
                            "average_total",
                        ),
                        (
                            "sum",
                            "Qual è il valore totale degli ordini nel database MySQL?",
                            "total_value",
                        ),
                    ):
                        examples.append({
                            "question": question,
                            "plan": {
                                "sources": [{
                                    "alias": "o",
                                    "asset_id": asset["asset_id"],
                                    "asset_version_id": asset["asset_version_id"],
                                }],
                                "aggregations": [{
                                    "function": function,
                                    "source_alias": "o",
                                    "field": "total",
                                    "alias": alias,
                                }],
                            },
                        })
                examples.append({
                    "question": "Quanti ordini MySQL hanno stato paid?",
                    "plan": {
                        "sources": [{
                            "alias": "o",
                            "asset_id": asset["asset_id"],
                            "asset_version_id": asset["asset_version_id"],
                        }],
                        "filters": [{
                            "source_alias": "o",
                            "field": "status",
                            "operator": "eq",
                            "value": "paid",
                        }],
                        "aggregations": [{
                            "function": "count",
                            "source_alias": "o",
                            "field": "id",
                            "alias": "orders",
                        }],
                    },
                })
                for question, projections, filters in (
                    (
                        "Mostra id, stato e totale degli ordini MySQL",
                        ("id", "status", "total"),
                        (),
                    ),
                    (
                        "Mostra gli ordini MySQL con totale inferiore a 100",
                        ("id", "customer_id", "status", "total", "created_at"),
                        ({"field": "total", "operator": "lt", "value": 100},),
                    ),
                    (
                        "Mostra gli ordini MySQL con stato pending",
                        ("id", "customer_id", "status", "total", "created_at"),
                        ({"field": "status", "operator": "eq", "value": "pending"},),
                    ),
                ):
                    examples.append({
                        "question": question,
                        "intent": "row_returning",
                        "plan": {
                            "sources": [{
                                "alias": "o",
                                "asset_id": asset["asset_id"],
                                "asset_version_id": asset["asset_version_id"],
                            }],
                            "projections": [
                                {"source_alias": "o", "field": field}
                                for field in projections
                            ],
                            "filters": [
                                {"source_alias": "o", **filter_item}
                                for filter_item in filters
                            ],
                            "aggregations": [],
                            "group_by": [],
                        },
                    })
            if (
                asset["backend"] == "mongodb"
                and asset["logical_name"].casefold() == "profiles"
                and {"_id", "email", "preferences.newsletter"}.issubset(fields)
            ):
                examples.extend([
                    {
                        "question": "Mostra i profili MongoDB con newsletter attiva",
                        "intent": "row_returning",
                        "plan": {
                            "sources": [{
                                "alias": "profiles",
                                "asset_id": asset["asset_id"],
                                "asset_version_id": asset["asset_version_id"],
                            }],
                            "projections": [{
                                "source_alias": "profiles", "field": field
                            } for field in ("email", "preferences.newsletter")],
                            "filters": [{
                                "source_alias": "profiles",
                                "field": "preferences.newsletter",
                                "operator": "eq",
                                "value": True,
                            }],
                            "aggregations": [],
                            "group_by": [],
                            "order_by": [],
                        },
                    },
                    {
                        "question": "Quanti profili ci sono nel database MongoDB?",
                        "intent": "count",
                        "plan": {
                            "sources": [{
                                "alias": "profiles",
                                "asset_id": asset["asset_id"],
                                "asset_version_id": asset["asset_version_id"],
                            }],
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
                        },
                    },
                ])
                if "preferences.language" in fields:
                    examples.append({
                        "question": "Mostra i profili MongoDB con lingua inglese",
                        "intent": "row_returning",
                        "plan": {
                            "sources": [{
                                "alias": "profiles",
                                "asset_id": asset["asset_id"],
                                "asset_version_id": asset["asset_version_id"],
                            }],
                            "projections": [{
                                "source_alias": "profiles", "field": field
                            } for field in ("email", "preferences.language")],
                            "filters": [{
                                "source_alias": "profiles",
                                "field": "preferences.language",
                                "operator": "eq",
                                "value": "en",
                            }],
                            "aggregations": [],
                            "group_by": [],
                            "order_by": [],
                        },
                    })
            if (
                asset["backend"] == "mongodb"
                and asset["logical_name"].casefold() == "events"
                and {"_id", "type", "properties.amount", "user_id"}.issubset(fields)
            ):
                source = {
                    "alias": "events",
                    "asset_id": asset["asset_id"],
                    "asset_version_id": asset["asset_version_id"],
                }
                examples.extend([
                    {
                        "question": "Quanti eventi ci sono per tipo nel database MongoDB?",
                        "intent": "count",
                        "plan": {
                            "sources": [source],
                            "projections": [{
                                "source_alias": "events", "field": "type"
                            }],
                            "filters": [],
                            "aggregations": [{
                                "function": "count", "source_alias": "events",
                                "field": "_id", "alias": "events",
                            }],
                            "group_by": [{
                                "source_alias": "events", "field": "type"
                            }],
                            "order_by": [],
                        },
                    },
                    *[
                        {
                            "question": question,
                            "plan": {
                                "sources": [source],
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
                            },
                        }
                        for question, function, alias in (
                            ("Qual è l’importo totale degli eventi MongoDB?", "sum", "total"),
                            ("Qual è l’importo medio degli eventi MongoDB?", "avg", "avg_amount"),
                        )
                    ],
                    {
                        "question": "Quanti eventi MongoDB sono stati generati dall’utente 1?",
                        "intent": "count",
                        "plan": {
                            "sources": [source],
                            "projections": [],
                            "filters": [{
                                "source_alias": "events", "field": "user_id",
                                "operator": "eq", "value": 1,
                            }],
                            "aggregations": [{
                                "function": "count", "source_alias": "events",
                                "field": "_id", "alias": "events",
                            }],
                            "group_by": [],
                            "order_by": [],
                        },
                    },
                ])
                if {
                    "items",
                    "items[]",
                    "items[].sku",
                    "items[].quantity",
                }.issubset(fields):
                    examples.extend([
                        {
                            "question": (
                                "Quanti eventi MongoDB contengono almeno un "
                                "articolo con quantità maggiore o uguale a 2?"
                            ),
                            "intent": "count",
                            "plan": {
                                "sources": [source],
                                "array_matches": [{
                                    "source_alias": "events",
                                    "field": "items",
                                    "predicates": [{
                                        "field": "quantity",
                                        "operator": "gte",
                                        "value": 2,
                                    }],
                                }],
                                "aggregations": [{
                                    "function": "count",
                                    "source_alias": "events",
                                    "field": "_id",
                                    "alias": "events",
                                }],
                            },
                        },
                        {
                            "question": (
                                "Qual è la quantità totale acquistata per SKU "
                                "negli eventi MongoDB?"
                            ),
                            "plan": {
                                "sources": [source],
                                "unwinds": [{
                                    "source_alias": "events",
                                    "field": "items",
                                }],
                                "projections": [{
                                    "source_alias": "events",
                                    "field": "items[].sku",
                                    "alias": "sku",
                                }],
                                "aggregations": [{
                                    "function": "sum",
                                    "source_alias": "events",
                                    "field": "items[].quantity",
                                    "alias": "quantity",
                                }],
                                "group_by": [{
                                    "source_alias": "events",
                                    "field": "items[].sku",
                                }],
                            },
                        },
                    ])
        return examples
