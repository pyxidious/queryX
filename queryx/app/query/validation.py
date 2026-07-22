from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from queryx.app.ingestion.models import BindingRole, BindingStatus, StorageBinding
from queryx.app.ingestion.storage import IngestionStorage
from queryx.app.processing.storage import ProcessingStorage
from queryx.app.query.models import (
    Aggregation,
    AggregationFunction,
    AssetRelationship,
    LogicalQueryPlan,
    OutputField,
    FilterOperator,
)
from queryx.app.query.mongodb_catalog import MongoDBCatalogAssetError, MongoDBCatalogAssets
from queryx.app.query.mysql_catalog import MySQLCatalogAssetError, MySQLCatalogAssets
from queryx.app.query.storage import QueryStorage


class QueryValidationError(ValueError):
    def __init__(
        self, code: str, message: str, *, status_code: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.details}


@dataclass(frozen=True)
class ResolvedSource:
    alias: str
    asset_id: str
    asset_version_id: str
    binding: StorageBinding | None
    relation: str
    fields: dict[str, dict[str, Any]]
    backend: str = "duckdb"
    schema: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class ValidatedPlan:
    plan: LogicalQueryPlan
    sources: dict[str, ResolvedSource]
    relationships: dict[str, AssetRelationship]
    output_schema: list[OutputField]
    fingerprint: str
    warnings: list[str]


class PlanValidator:
    def __init__(
        self, ingestion: IngestionStorage, processing: ProcessingStorage,
        query_storage: QueryStorage, default_limit: int, max_limit: int,
        mysql_assets: MySQLCatalogAssets | None = None,
        mongodb_assets: MongoDBCatalogAssets | None = None,
    ) -> None:
        self.ingestion = ingestion
        self.processing = processing
        self.query_storage = query_storage
        self.default_limit = default_limit
        self.max_limit = max_limit
        self.mysql_assets = mysql_assets
        self.mongodb_assets = mongodb_assets

    def validate(self, plan: LogicalQueryPlan) -> ValidatedPlan:
        aliases = [source.alias for source in plan.sources]
        if len(aliases) != len(set(aliases)):
            raise QueryValidationError("duplicate_source_alias", "Source aliases must be unique")
        limit = plan.limit if plan.limit is not None else self.default_limit
        if limit > self.max_limit:
            raise QueryValidationError(
                "query_limit_exceeded", f"Query limit cannot exceed {self.max_limit}",
                details={"max_limit": self.max_limit},
            )
        normalized = plan.model_copy(update={"limit": limit})
        resolved = {source.alias: self._resolve_source(source) for source in normalized.sources}
        backends = {source.backend for source in resolved.values()}
        if len(backends) > 1:
            raise QueryValidationError(
                "federation_not_supported",
                "A logical query plan cannot mix execution backends",
            )
        backend = next(iter(backends))
        if backend == "mysql":
            self._validate_mysql_scope(normalized)
            relationships: dict[str, AssetRelationship] = {}
        elif backend == "mongodb":
            self._validate_mongodb_scope(normalized)
            relationships = {}
        else:
            relationships = self._validate_joins(normalized, resolved)
        self._validate_backend_operators(normalized, backend)
        self._validate_filters(normalized, resolved)
        output_schema, output_names = self._validate_outputs(normalized, resolved)
        self._validate_group_by(normalized, resolved)
        for order in normalized.order_by:
            if order.field not in output_names:
                raise QueryValidationError(
                    "invalid_order_by", "Order field must reference an output field or aggregation alias",
                    details={"field": order.field},
                )
        payload = normalized.model_dump(mode="json", exclude_none=True)
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ValidatedPlan(normalized, resolved, relationships, output_schema, fingerprint, [])

    def _resolve_source(self, source: Any) -> ResolvedSource:
        if self.mysql_assets is not None:
            try:
                mysql_asset = self.mysql_assets.resolve(
                    source.asset_id, source.asset_version_id
                )
            except MySQLCatalogAssetError as exc:
                raise QueryValidationError(exc.code, exc.message) from exc
            if mysql_asset is not None:
                return ResolvedSource(
                    alias=source.alias,
                    asset_id=mysql_asset.asset_id,
                    asset_version_id=mysql_asset.asset_version_id,
                    binding=None,
                    relation=mysql_asset.table,
                    fields=mysql_asset.fields,
                    backend="mysql",
                    schema=mysql_asset.schema,
                    source_id=mysql_asset.source_id,
                )
        if self.mongodb_assets is not None:
            try:
                mongodb_asset = self.mongodb_assets.resolve(
                    source.asset_id, source.asset_version_id
                )
            except MongoDBCatalogAssetError as exc:
                raise QueryValidationError(exc.code, exc.message) from exc
            if mongodb_asset is not None:
                return ResolvedSource(
                    alias=source.alias,
                    asset_id=mongodb_asset.asset_id,
                    asset_version_id=mongodb_asset.asset_version_id,
                    binding=None,
                    relation=mongodb_asset.collection,
                    fields=mongodb_asset.fields,
                    backend="mongodb",
                    schema=mongodb_asset.database,
                    source_id=mongodb_asset.source_id,
                )
        asset = self.ingestion.get_asset(source.asset_id)
        if asset is None:
            raise QueryValidationError(
                "asset_not_found", "Query source asset does not exist",
                details={"asset_id": source.asset_id},
            )
        if str(asset.asset_kind) == "mysql_table":
            raise QueryValidationError(
                "mysql_source_not_ready",
                "The MySQL catalog source is not current and ready",
                details={"asset_id": source.asset_id},
            )
        if str(asset.asset_kind) == "mongodb_collection":
            raise QueryValidationError(
                "mongodb_source_not_ready",
                "The MongoDB catalog source is not current and ready",
                details={"asset_id": source.asset_id},
            )
        if source.asset_version_id:
            version = self.ingestion.get_version(source.asset_id, source.asset_version_id)
            candidates = [version] if version else []
        else:
            candidates = self.ingestion.list_versions(source.asset_id) or []
        candidates = [version for version in candidates if version is not None and str(version.status) == "ready"]
        if not candidates:
            raise QueryValidationError(
                "asset_version_not_ready", "A ready asset version is required",
                details={"asset_id": source.asset_id},
            )
        for version in candidates:
            bindings = self.processing.list_bindings(
                version.id, BindingRole.SERVING, BindingStatus.READY
            )
            binding = next(
                (item for item in reversed(bindings) if str(item.backend_type) == "duckdb"), None
            )
            if binding is None:
                continue
            relation = binding.metadata.get("relation")
            schema = binding.metadata.get("serving_schema")
            if not isinstance(relation, str) or not isinstance(schema, list):
                continue
            fields = {
                str(field.get("name")): field for field in schema
                if isinstance(field, dict) and field.get("name")
            }
            return ResolvedSource(
                source.alias, source.asset_id, version.id, binding, relation, fields
            )
        raise QueryValidationError(
            "serving_binding_not_ready", "A ready DuckDB serving binding is required",
            details={"asset_id": source.asset_id},
        )

    @staticmethod
    def _validate_mysql_scope(plan: LogicalQueryPlan) -> None:
        if len(plan.sources) != 1:
            raise QueryValidationError(
                "mysql_multi_source_not_supported",
                "MySQL queries currently support exactly one source",
            )
        if plan.joins:
            raise QueryValidationError(
                "mysql_joins_not_supported", "MySQL joins are not supported yet"
            )
        if any(item.transform is not None for item in [*plan.projections, *plan.group_by]):
            raise QueryValidationError(
                "mysql_transform_not_supported",
                "Transforms are not supported for MySQL queries yet",
            )

    @staticmethod
    def _validate_mongodb_scope(plan: LogicalQueryPlan) -> None:
        if len(plan.sources) != 1:
            raise QueryValidationError(
                "mongodb_multi_source_not_supported",
                "MongoDB queries currently support exactly one source",
            )
        if plan.joins:
            raise QueryValidationError(
                "mongodb_joins_not_supported", "MongoDB joins are not supported yet"
            )
        if any(item.transform is not None for item in [*plan.projections, *plan.group_by]):
            raise QueryValidationError(
                "mongodb_transform_not_supported",
                "Transforms are not supported for MongoDB queries yet",
            )

    @staticmethod
    def _validate_backend_operators(plan: LogicalQueryPlan, backend: str) -> None:
        if backend == "mongodb":
            allowed = {
                FilterOperator.EQ,
                FilterOperator.NEQ,
                FilterOperator.GT,
                FilterOperator.GTE,
                FilterOperator.LT,
                FilterOperator.LTE,
                FilterOperator.IN,
                FilterOperator.NOT_IN,
                FilterOperator.IS_NULL,
                FilterOperator.IS_NOT_NULL,
            }
            for query_filter in plan.filters:
                if query_filter.operator not in allowed:
                    raise QueryValidationError(
                        "mongodb_operator_not_supported",
                        "Filter operator is not supported by MongoDB queries",
                    )
                if not _safe_mongodb_value(query_filter.value):
                    raise QueryValidationError(
                        "invalid_mongodb_filter_value",
                        "MongoDB filter values must be scalar or lists of scalars",
                    )
        elif any(item.operator == FilterOperator.NOT_IN for item in plan.filters):
            raise QueryValidationError(
                "operator_not_supported",
                "Filter operator is not supported by this query backend",
            )

    def _validate_joins(
        self, plan: LogicalQueryPlan, sources: dict[str, ResolvedSource]
    ) -> dict[str, AssetRelationship]:
        if len(sources) == 1 and plan.joins:
            raise QueryValidationError("invalid_join", "A single-source plan cannot contain joins")
        if len(plan.joins) != max(0, len(sources) - 1):
            raise QueryValidationError(
                "join_required", "Every additional source requires one declared relationship join"
            )
        joined = {plan.sources[0].alias}
        relationships: dict[str, AssetRelationship] = {}
        for join in plan.joins:
            if join.left_alias not in sources or join.right_alias not in sources:
                raise QueryValidationError("unknown_source_alias", "Join references an unknown source alias")
            if join.left_alias not in joined or join.right_alias in joined:
                raise QueryValidationError(
                    "invalid_join_order", "Joins must connect a new right source to an existing left source"
                )
            relationship = self.query_storage.get_relationship(join.relationship_id)
            if relationship is None:
                raise QueryValidationError("relationship_not_found", "Declared relationship does not exist")
            if str(relationship.status) != "active":
                raise QueryValidationError("relationship_disabled", "Declared relationship is disabled")
            left = sources[join.left_alias]
            right = sources[join.right_alias]
            if relationship.left_asset_id != left.asset_id or relationship.right_asset_id != right.asset_id:
                raise QueryValidationError(
                    "relationship_alias_mismatch", "Relationship assets do not match the join aliases"
                )
            left_field = self._field(left, relationship.left_field)
            right_field = self._field(right, relationship.right_field)
            if not types_compatible(_type(left_field), _type(right_field)):
                raise QueryValidationError(
                    "relationship_type_mismatch", "Relationship fields have incompatible serving types"
                )
            relationships[join.relationship_id] = relationship
            joined.add(join.right_alias)
        return relationships

    def _validate_filters(self, plan: LogicalQueryPlan, sources: dict[str, ResolvedSource]) -> None:
        for query_filter in plan.filters:
            source = sources.get(query_filter.source_alias)
            if source is None:
                raise QueryValidationError("unknown_source_alias", "Filter references an unknown source alias")
            self._field(source, query_filter.field)

    def _validate_outputs(
        self, plan: LogicalQueryPlan, sources: dict[str, ResolvedSource]
    ) -> tuple[list[OutputField], set[str]]:
        if not plan.projections and not plan.aggregations:
            raise QueryValidationError("empty_projection", "At least one projection or aggregation is required")
        output: list[OutputField] = []
        names: set[str] = set()
        for projection in plan.projections:
            source = sources.get(projection.source_alias)
            if source is None:
                raise QueryValidationError("unknown_source_alias", "Projection references an unknown source alias")
            field = self._field(source, projection.field)
            data_type = _type(field)
            if projection.transform == "date_trunc_month" and not is_temporal(data_type):
                raise QueryValidationError(
                    "invalid_transform_type", "date_trunc_month requires a date or timestamp field",
                    details={"field": projection.field},
                )
            name = projection.alias or projection.field
            self._add_output_name(names, name)
            output.append(OutputField(
                name=name,
                data_type="DATE" if projection.transform == "date_trunc_month" else data_type,
                nullable=bool(field.get("nullable", True)),
            ))
        for aggregation in plan.aggregations:
            field = self._aggregation_field(aggregation, sources)
            if aggregation.function in {AggregationFunction.SUM, AggregationFunction.AVG} and not is_numeric(_type(field)):
                raise QueryValidationError(
                    "aggregation_type_mismatch",
                    f"{aggregation.function} requires a numeric field",
                    details={"field": aggregation.field},
                )
            name = aggregation.alias or _aggregation_name(aggregation)
            self._add_output_name(names, name)
            output.append(OutputField(
                name=name,
                data_type=_aggregation_type(aggregation, _type(field)),
                nullable=aggregation.function not in {
                    AggregationFunction.COUNT, AggregationFunction.COUNT_DISTINCT
                },
            ))
        return output, names

    def _validate_group_by(
        self, plan: LogicalQueryPlan, sources: dict[str, ResolvedSource]
    ) -> None:
        group_keys = {(item.source_alias, item.field, item.transform) for item in plan.group_by}
        for item in plan.group_by:
            source = sources.get(item.source_alias)
            if source is None:
                raise QueryValidationError("unknown_source_alias", "Group by references an unknown source alias")
            field = self._field(source, item.field)
            if item.transform == "date_trunc_month" and not is_temporal(_type(field)):
                raise QueryValidationError("invalid_transform_type", "date_trunc_month requires a temporal field")
        projection_keys = {
            (item.source_alias, item.field, item.transform) for item in plan.projections
        }
        if plan.aggregations and projection_keys != group_keys:
            raise QueryValidationError(
                "invalid_group_by", "Every non-aggregated projection must match group_by exactly"
            )
        if not plan.aggregations and group_keys and not group_keys.issubset(projection_keys):
            raise QueryValidationError("invalid_group_by", "Group by fields must be projected")

    @staticmethod
    def _field(source: ResolvedSource, field_name: str) -> dict[str, Any]:
        field = source.fields.get(field_name)
        if field is None:
            raise QueryValidationError(
                "field_not_found", "Field is not present in the cataloged serving schema",
                details={"source_alias": source.alias, "field": field_name},
            )
        return field

    def _aggregation_field(
        self, aggregation: Aggregation, sources: dict[str, ResolvedSource]
    ) -> dict[str, Any]:
        if aggregation.source_alias is None:
            return {"data_type": "BIGINT", "nullable": False}
        source = sources.get(aggregation.source_alias)
        if source is None:
            raise QueryValidationError("unknown_source_alias", "Aggregation references an unknown source alias")
        assert aggregation.field is not None
        return self._field(source, aggregation.field)

    @staticmethod
    def _add_output_name(names: set[str], name: str) -> None:
        if name in names:
            raise QueryValidationError("duplicate_output_alias", "Output aliases must be unique", details={"alias": name})
        names.add(name)


def _type(field: dict[str, Any]) -> str:
    return str(field.get("data_type", "UNKNOWN"))


def _family(data_type: str) -> str:
    value = data_type.upper()
    if any(token in value for token in ("INT", "DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL")):
        return "numeric"
    if any(token in value for token in ("TIMESTAMP", "DATE", "TIME")):
        return "temporal"
    if any(token in value for token in ("CHAR", "VARCHAR", "STRING", "TEXT")):
        return "string"
    if "BOOL" in value:
        return "boolean"
    return value


def types_compatible(left: str, right: str) -> bool:
    return _family(left) == _family(right)


def is_numeric(data_type: str) -> bool:
    return _family(data_type) == "numeric"


def is_temporal(data_type: str) -> bool:
    return _family(data_type) == "temporal"


def _aggregation_name(aggregation: Aggregation) -> str:
    suffix = aggregation.field or "all"
    return f"{aggregation.function}_{suffix}"


def _aggregation_type(aggregation: Aggregation, field_type: str) -> str:
    if aggregation.function in {AggregationFunction.COUNT, AggregationFunction.COUNT_DISTINCT}:
        return "BIGINT"
    if aggregation.function == AggregationFunction.AVG:
        return "DOUBLE"
    return field_type


def _safe_mongodb_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return bool(value) and all(_safe_mongodb_value(item) for item in value)
    return False
