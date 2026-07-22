from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RelationshipType(StrEnum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_ONE = "many_to_one"
    MANY_TO_MANY = "many_to_many"


class JoinType(StrEnum):
    INNER = "inner"
    LEFT = "left"


class RelationshipSource(StrEnum):
    DECLARED = "declared"
    INFERRED = "inferred"


class RelationshipStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class AssetRelationshipCreate(StrictModel):
    name: str | None = Field(default=None, max_length=256)
    left_asset_id: str = Field(min_length=1, max_length=128)
    left_field: str = Field(min_length=1, max_length=256)
    right_asset_id: str = Field(min_length=1, max_length=128)
    right_field: str = Field(min_length=1, max_length=256)
    relationship_type: RelationshipType
    join_type_default: JoinType = JoinType.INNER
    source: RelationshipSource = RelationshipSource.DECLARED
    confidence: float | None = Field(default=None, ge=0, le=1)


class AssetRelationship(AssetRelationshipCreate):
    id: str
    status: RelationshipStatus
    created_at: datetime
    updated_at: datetime


class QuerySource(StrictModel):
    alias: str = Field(min_length=1, max_length=128)
    asset_id: str = Field(min_length=1, max_length=128)
    asset_version_id: str | None = Field(default=None, min_length=1, max_length=128)


class QueryJoin(StrictModel):
    relationship_id: str = Field(min_length=1, max_length=128)
    left_alias: str = Field(min_length=1, max_length=128)
    right_alias: str = Field(min_length=1, max_length=128)
    join_type: JoinType | None = None


class FieldExpression(StrictModel):
    source_alias: str = Field(min_length=1, max_length=128)
    field: str = Field(min_length=1, max_length=256)
    transform: Literal["date_trunc_month"] | None = None


class Projection(FieldExpression):
    alias: str | None = Field(default=None, min_length=1, max_length=256)


class FilterOperator(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"
    BETWEEN = "between"


class QueryFilter(StrictModel):
    source_alias: str = Field(min_length=1, max_length=128)
    field: str = Field(min_length=1, max_length=256)
    operator: FilterOperator
    value: Any = None

    @model_validator(mode="after")
    def validate_operand(self) -> QueryFilter:
        if self.operator == FilterOperator.IN and (
            not isinstance(self.value, list) or not self.value
        ):
            raise ValueError("operator 'in' requires a non-empty list")
        if self.operator == FilterOperator.BETWEEN and (
            not isinstance(self.value, list) or len(self.value) != 2
        ):
            raise ValueError("operator 'between' requires exactly two values")
        if self.operator in {FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL}:
            self.value = None
        return self


class AggregationFunction(StrEnum):
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class Aggregation(StrictModel):
    function: AggregationFunction
    source_alias: str | None = Field(default=None, min_length=1, max_length=128)
    field: str | None = Field(default=None, min_length=1, max_length=256)
    alias: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_field(self) -> Aggregation:
        if self.function != AggregationFunction.COUNT and (not self.source_alias or not self.field):
            raise ValueError(f"aggregation '{self.function}' requires source_alias and field")
        if (self.source_alias is None) != (self.field is None):
            raise ValueError("source_alias and field must be provided together")
        return self


class OrderBy(StrictModel):
    field: str = Field(min_length=1, max_length=256)
    direction: Literal["asc", "desc"] = "asc"


class LogicalQueryPlan(StrictModel):
    sources: list[QuerySource] = Field(min_length=1)
    joins: list[QueryJoin] = Field(default_factory=list)
    projections: list[Projection] = Field(default_factory=list)
    filters: list[QueryFilter] = Field(default_factory=list)
    aggregations: list[Aggregation] = Field(default_factory=list)
    group_by: list[FieldExpression] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1)


class OutputField(StrictModel):
    name: str
    data_type: str
    nullable: bool = True


class QueryRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class QueryRun(StrictModel):
    id: str
    plan_fingerprint: str
    normalized_plan: dict[str, Any]
    status: QueryRunStatus
    source_asset_versions: list[str]
    rows_returned: int = 0
    truncated: bool = False
    execution_time_ms: float | None = None
    error: dict[str, Any] | None = None
    created_at: datetime
    finished_at: datetime | None = None


class QueryValidationResult(StrictModel):
    normalized_plan: LogicalQueryPlan
    output_schema: list[OutputField]
    plan_fingerprint: str
    warnings: list[str] = Field(default_factory=list)


class QueryExecutionResult(StrictModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    execution_time_ms: float
    plan_fingerprint: str
    warnings: list[str] = Field(default_factory=list)


class NaturalLanguageQueryRequest(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    execute: bool = False


class QueryClassification(StrEnum):
    ANSWERABLE = "answerable"
    AMBIGUOUS = "ambiguous"
    UNANSWERABLE = "unanswerable"


class NaturalLanguageClassification(StrictModel):
    classification: QueryClassification
    reason: str = Field(min_length=1, max_length=500)
    clarification_question: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_clarification(self) -> NaturalLanguageClassification:
        if self.classification == QueryClassification.AMBIGUOUS and not self.clarification_question:
            raise ValueError("ambiguous classification requires clarification_question")
        return self


class NaturalLanguageWarning(StrictModel):
    code: str
    message: str


class NaturalLanguageQueryResponse(StrictModel):
    normalized_plan: LogicalQueryPlan | None = None
    output_schema: list[OutputField] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    result: QueryExecutionResult | None = None
    answer: str | None = None
    planning_time_ms: float = Field(ge=0)
    execution_time_ms: float | None = Field(default=None, ge=0)
    explanation_time_ms: float | None = Field(default=None, ge=0)
    explanation_warning: NaturalLanguageWarning | None = None
    classification: QueryClassification | None = None
    clarification_question: str | None = None
    reason: str | None = None
