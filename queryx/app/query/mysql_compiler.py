from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from queryx.app.query.models import AggregationFunction, FilterOperator, OutputField
from queryx.app.query.validation import ValidatedPlan, _aggregation_name


@dataclass(frozen=True)
class MySQLCompiledQuery:
    sql: str
    parameters: dict[str, Any]
    output_schema: list[OutputField]
    result_limit: int


class MySQLQueryCompiler:
    def compile(self, validated: ValidatedPlan) -> MySQLCompiledQuery:
        plan = validated.plan
        source = validated.sources[plan.sources[0].alias]
        if source.backend != "mysql" or source.schema is None:
            raise ValueError("MySQL compiler requires one resolved MySQL source")

        select_items: list[str] = []
        for projection in plan.projections:
            expression = self._field(projection.source_alias, projection.field)
            select_items.append(
                f"{expression} AS {_quote(projection.alias or projection.field)}"
            )
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

        sql = (
            f"SELECT {', '.join(select_items)} FROM "
            f"{_quote(source.schema)}.{_quote(source.relation)} AS {_quote(source.alias)}"
        )
        parameters: dict[str, Any] = {}
        predicates: list[str] = []
        operators = {
            FilterOperator.EQ: "=",
            FilterOperator.NEQ: "<>",
            FilterOperator.GT: ">",
            FilterOperator.GTE: ">=",
            FilterOperator.LT: "<",
            FilterOperator.LTE: "<=",
        }

        def parameter(value: Any) -> str:
            name = f"p{len(parameters)}"
            parameters[name] = value
            return f":{name}"

        for query_filter in plan.filters:
            field = self._field(query_filter.source_alias, query_filter.field)
            if query_filter.operator in operators:
                predicates.append(
                    f"{field} {operators[query_filter.operator]} {parameter(query_filter.value)}"
                )
            elif query_filter.operator == FilterOperator.IN:
                placeholders = ", ".join(parameter(value) for value in query_filter.value)
                predicates.append(f"{field} IN ({placeholders})")
            elif query_filter.operator == FilterOperator.BETWEEN:
                lower, upper = query_filter.value
                predicates.append(
                    f"{field} BETWEEN {parameter(lower)} AND {parameter(upper)}"
                )
            elif query_filter.operator == FilterOperator.IS_NULL:
                predicates.append(f"{field} IS NULL")
            else:
                predicates.append(f"{field} IS NOT NULL")
        if predicates:
            sql += " WHERE " + " AND ".join(predicates)
        if plan.group_by:
            sql += " GROUP BY " + ", ".join(
                self._field(item.source_alias, item.field) for item in plan.group_by
            )
        if plan.order_by:
            sql += " ORDER BY " + ", ".join(
                f"{_quote(item.field)} {item.direction.upper()}" for item in plan.order_by
            )
        assert plan.limit is not None
        parameters["result_limit"] = plan.limit + 1
        sql += " LIMIT :result_limit"
        return MySQLCompiledQuery(sql, parameters, validated.output_schema, plan.limit)

    @staticmethod
    def _field(source_alias: str, field: str) -> str:
        return f"{_quote(source_alias)}.{_quote(field)}"


def _quote(value: str) -> str:
    return f"`{value.replace('`', '``')}`"
