from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from queryx.app.query.models import AggregationFunction, FilterOperator, OutputField
from queryx.app.query.validation import ValidatedPlan, _aggregation_name


@dataclass(frozen=True)
class MongoDBCompiledQuery:
    database: str
    collection: str
    pipeline: list[dict[str, Any]]
    output_schema: list[OutputField]
    result_limit: int


class MongoDBQueryCompiler:
    def compile(self, validated: ValidatedPlan) -> MongoDBCompiledQuery:
        plan = validated.plan
        source = validated.sources[plan.sources[0].alias]
        if source.backend != "mongodb" or source.schema is None:
            raise ValueError("MongoDB compiler requires one resolved MongoDB source")

        pipeline: list[dict[str, Any]] = []
        predicates = [self._predicate(item.field, item.operator, item.value) for item in plan.filters]
        if predicates:
            pipeline.append({"$match": predicates[0] if len(predicates) == 1 else {"$and": predicates}})

        if plan.aggregations or plan.group_by:
            pipeline.extend(self._aggregate_stages(validated))
        else:
            project = {"_id": 0}
            for projection in plan.projections:
                project[projection.alias or projection.field] = f"${projection.field}"
            pipeline.append({"$project": project})

        if plan.order_by:
            pipeline.append({
                "$sort": {
                    item.field: 1 if item.direction == "asc" else -1
                    for item in plan.order_by
                }
            })
        assert plan.limit is not None
        pipeline.append({"$limit": plan.limit + 1})
        return MongoDBCompiledQuery(
            database=source.schema,
            collection=source.relation,
            pipeline=pipeline,
            output_schema=validated.output_schema,
            result_limit=plan.limit,
        )

    @staticmethod
    def _aggregate_stages(validated: ValidatedPlan) -> list[dict[str, Any]]:
        plan = validated.plan
        group_names: dict[tuple[str, str], str] = {}
        for group in plan.group_by:
            projection = next(
                (
                    item
                    for item in plan.projections
                    if item.source_alias == group.source_alias and item.field == group.field
                ),
                None,
            )
            group_names[(group.source_alias, group.field)] = (
                projection.alias or projection.field if projection else group.field
            )
        group_id: Any = None
        if group_names:
            group_id = {
                output_name: f"${field}"
                for (_, field), output_name in group_names.items()
            }
        group_stage: dict[str, Any] = {"_id": group_id}
        distinct_temporaries: dict[str, str] = {}
        for index, aggregation in enumerate(plan.aggregations):
            output_name = aggregation.alias or _aggregation_name(aggregation)
            field = f"${aggregation.field}" if aggregation.field else None
            if aggregation.function == AggregationFunction.COUNT:
                group_stage[output_name] = {"$sum": 1}
            elif aggregation.function == AggregationFunction.COUNT_DISTINCT:
                temporary = f"__distinct_{index}"
                group_stage[temporary] = {"$addToSet": field}
                distinct_temporaries[output_name] = temporary
            else:
                group_stage[output_name] = {
                    f"${aggregation.function.value}": field
                }
        project_stage: dict[str, Any] = {"_id": 0}
        for output_name in group_names.values():
            project_stage[output_name] = f"$_id.{output_name}"
        for aggregation in plan.aggregations:
            output_name = aggregation.alias or _aggregation_name(aggregation)
            if output_name in distinct_temporaries:
                project_stage[output_name] = {
                    "$size": f"${distinct_temporaries[output_name]}"
                }
            else:
                project_stage[output_name] = f"${output_name}"
        return [{"$group": group_stage}, {"$project": project_stage}]

    @staticmethod
    def _predicate(field: str, operator: FilterOperator, value: Any) -> dict[str, Any]:
        operators = {
            FilterOperator.NEQ: "$ne",
            FilterOperator.GT: "$gt",
            FilterOperator.GTE: "$gte",
            FilterOperator.LT: "$lt",
            FilterOperator.LTE: "$lte",
            FilterOperator.IN: "$in",
            FilterOperator.NOT_IN: "$nin",
            FilterOperator.IS_NOT_NULL: "$ne",
        }
        if operator in {FilterOperator.EQ, FilterOperator.IS_NULL}:
            return {field: value}
        return {field: {operators[operator]: value}}
