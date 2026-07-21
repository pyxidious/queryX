from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from queryx.app.query.models import AggregationFunction, FilterOperator, OutputField
from queryx.app.query.validation import ValidatedPlan, _aggregation_name


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    parameters: list[Any]
    output_schema: list[OutputField]
    result_limit: int


class DuckDBQueryCompiler:
    def __init__(self, schema: str) -> None:
        self.schema = schema

    def compile(self, validated: ValidatedPlan) -> CompiledQuery:
        plan = validated.plan
        select_items: list[str] = []
        for projection in plan.projections:
            expression = self._field(projection.source_alias, projection.field)
            if projection.transform == "date_trunc_month":
                expression = f"CAST(date_trunc('month', {expression}) AS DATE)"
            select_items.append(f"{expression} AS {_quote(projection.alias or projection.field)}")
        for aggregation in plan.aggregations:
            if aggregation.source_alias is None:
                argument = "*"
            else:
                assert aggregation.field is not None
                argument = self._field(aggregation.source_alias, aggregation.field)
            if aggregation.function == AggregationFunction.COUNT_DISTINCT:
                expression = f"COUNT(DISTINCT {argument})"
            else:
                expression = f"{aggregation.function.value.upper()}({argument})"
            select_items.append(
                f"{expression} AS {_quote(aggregation.alias or _aggregation_name(aggregation))}"
            )

        first = validated.sources[plan.sources[0].alias]
        sql = (
            f"SELECT {', '.join(select_items)} FROM "
            f"{_quote(self.schema)}.{_quote(first.relation)} AS {_quote(first.alias)}"
        )
        for join in plan.joins:
            relationship = validated.relationships[join.relationship_id]
            right = validated.sources[join.right_alias]
            join_type = (join.join_type or relationship.join_type_default).value.upper()
            sql += (
                f" {join_type} JOIN {_quote(self.schema)}.{_quote(right.relation)} AS {_quote(right.alias)}"
                f" ON {self._field(join.left_alias, relationship.left_field)}"
                f" = {self._field(join.right_alias, relationship.right_field)}"
            )

        parameters: list[Any] = []
        predicates: list[str] = []
        operators = {
            FilterOperator.EQ: "=", FilterOperator.NEQ: "<>", FilterOperator.GT: ">",
            FilterOperator.GTE: ">=", FilterOperator.LT: "<", FilterOperator.LTE: "<=",
        }
        for query_filter in plan.filters:
            field = self._field(query_filter.source_alias, query_filter.field)
            if query_filter.operator in operators:
                predicates.append(f"{field} {operators[query_filter.operator]} ?")
                parameters.append(query_filter.value)
            elif query_filter.operator == FilterOperator.IN:
                placeholders = ", ".join("?" for _ in query_filter.value)
                predicates.append(f"{field} IN ({placeholders})")
                parameters.extend(query_filter.value)
            elif query_filter.operator == FilterOperator.BETWEEN:
                predicates.append(f"{field} BETWEEN ? AND ?")
                parameters.extend(query_filter.value)
            elif query_filter.operator == FilterOperator.IS_NULL:
                predicates.append(f"{field} IS NULL")
            else:
                predicates.append(f"{field} IS NOT NULL")
        if predicates:
            sql += " WHERE " + " AND ".join(predicates)
        if plan.group_by:
            groups: list[str] = []
            for item in plan.group_by:
                expression = self._field(item.source_alias, item.field)
                if item.transform == "date_trunc_month":
                    expression = f"CAST(date_trunc('month', {expression}) AS DATE)"
                groups.append(expression)
            sql += " GROUP BY " + ", ".join(groups)
        if plan.order_by:
            sql += " ORDER BY " + ", ".join(
                f"{_quote(item.field)} {item.direction.upper()}" for item in plan.order_by
            )
        assert plan.limit is not None
        sql += " LIMIT ?"
        parameters.append(plan.limit + 1)
        return CompiledQuery(sql, parameters, validated.output_schema, plan.limit)

    @staticmethod
    def _field(source_alias: str, field: str) -> str:
        return f"{_quote(source_alias)}.{_quote(field)}"


def _quote(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'

