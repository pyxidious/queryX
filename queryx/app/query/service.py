from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from queryx.app.catalog.storage import CatalogStorage
from queryx.app.core.config import Settings
from queryx.app.ingestion.storage import IngestionStorage
from queryx.app.processing.storage import ProcessingStorage
from queryx.app.query.compiler import DuckDBQueryCompiler
from queryx.app.query.executor import DuckDBQueryExecutor, QueryExecutionError
from queryx.app.query.mysql_catalog import MySQLCatalogAssets
from queryx.app.query.mysql_compiler import MySQLQueryCompiler
from queryx.app.query.mysql_executor import MySQLQueryExecutor
from queryx.app.query.models import (
    AssetRelationship,
    AssetRelationshipCreate,
    LogicalQueryPlan,
    QueryExecutionResult,
    QueryRunStatus,
    QueryValidationResult,
    RelationshipSource,
)
from queryx.app.query.storage import DuplicateRelationshipError, QueryStorage
from queryx.app.query.validation import PlanValidator, QueryValidationError, types_compatible
from queryx.app.sources.registry import SourceRegistry


class RelationshipService:
    def __init__(self, settings: Settings) -> None:
        self.ingestion = IngestionStorage(settings.catalog_db_path)
        self.storage = QueryStorage(settings.catalog_db_path)

    def create(self, payload: AssetRelationshipCreate) -> AssetRelationship:
        if payload.source != RelationshipSource.DECLARED:
            raise QueryValidationError(
                "relationship_source_not_allowed",
                "Relationships can only be declared manually in this milestone",
            )
        left_type = self._observed_field_type(payload.left_asset_id, payload.left_field, "left")
        right_type = self._observed_field_type(payload.right_asset_id, payload.right_field, "right")
        if not types_compatible(left_type, right_type):
            raise QueryValidationError(
                "relationship_type_mismatch", "Relationship fields have incompatible observed types"
            )
        try:
            return self.storage.create_relationship(payload)
        except DuplicateRelationshipError as exc:
            raise QueryValidationError(
                "relationship_duplicate", str(exc), status_code=409
            ) from exc

    def list(self) -> list[AssetRelationship]:
        return self.storage.list_relationships()

    def get(self, relationship_id: str) -> AssetRelationship | None:
        return self.storage.get_relationship(relationship_id)

    def disable(self, relationship_id: str) -> AssetRelationship | None:
        return self.storage.disable_relationship(relationship_id)

    def _observed_field_type(self, asset_id: str, field_name: str, side: str) -> str:
        asset = self.ingestion.get_asset(asset_id)
        if asset is None:
            raise QueryValidationError(
                "asset_not_found", f"{side.capitalize()} asset does not exist",
                details={"asset_id": asset_id},
            )
        versions = self.ingestion.list_versions(asset_id) or []
        version = next((item for item in versions if str(item.status) == "ready"), None)
        inspection = self.ingestion.get_version_inspection(version.id) if version else None
        if inspection is not None:
            field = next((item for item in inspection.fields if item.name == field_name), None)
            if field is not None:
                return field.data_type
        raise QueryValidationError(
            "field_not_found", f"{side.capitalize()} field is not present in the observed schema",
            details={"asset_id": asset_id, "field": field_name},
        )


class QueryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ingestion = IngestionStorage(settings.catalog_db_path)
        self.processing = ProcessingStorage(settings.catalog_db_path)
        self.storage = QueryStorage(settings.catalog_db_path)
        self.mysql_catalog = MySQLCatalogAssets(
            CatalogStorage(settings.catalog_db_path), SourceRegistry(settings)
        )
        self.validator = PlanValidator(
            self.ingestion, self.processing, self.storage,
            settings.query_default_limit, settings.query_max_limit,
            self.mysql_catalog,
        )
        self.compiler = DuckDBQueryCompiler(settings.duckdb_schema)
        self.executor = DuckDBQueryExecutor(
            settings.duckdb_path, settings.duckdb_lock_path,
            settings.duckdb_lock_timeout_seconds, settings.query_timeout_seconds,
        )
        self.mysql_compiler = MySQLQueryCompiler()
        self.mysql_executor = MySQLQueryExecutor(
            settings.mysql_url, settings.mysql_query_timeout_seconds
        )

    def parse(self, payload: LogicalQueryPlan | dict[str, Any]) -> LogicalQueryPlan:
        if isinstance(payload, LogicalQueryPlan):
            return payload
        try:
            return LogicalQueryPlan.model_validate(payload)
        except ValidationError as exc:
            raise QueryValidationError(
                "invalid_logical_query_plan", "Logical query plan is invalid",
                details={"issues": [
                    {"path": ".".join(str(value) for value in item["loc"]), "message": item["msg"]}
                    for item in exc.errors(include_url=False, include_input=False)
                ]},
            ) from exc

    def validate(self, payload: LogicalQueryPlan | dict[str, Any]) -> QueryValidationResult:
        validated = self.validator.validate(self.parse(payload))
        return QueryValidationResult(
            normalized_plan=validated.plan,
            output_schema=validated.output_schema,
            plan_fingerprint=validated.fingerprint,
            warnings=validated.warnings,
        )

    def execute(self, payload: LogicalQueryPlan | dict[str, Any]) -> QueryExecutionResult:
        validated = self.validator.validate(self.parse(payload))
        backend = next(iter(validated.sources.values())).backend
        if backend == "mysql":
            compiled = self.mysql_compiler.compile(validated)
            executor = self.mysql_executor
        else:
            compiled = self.compiler.compile(validated)
            executor = self.executor
        run = self.storage.create_query_run(
            validated.fingerprint,
            self._audit_plan(validated.plan),
            [source.asset_version_id for source in validated.sources.values()],
            backend=backend,
            source_ids=[
                source.source_id
                for source in validated.sources.values()
                if source.source_id is not None
            ],
        )
        try:
            columns, rows, truncated, execution_time_ms = executor.execute(compiled)  # type: ignore[arg-type]
        except QueryExecutionError as exc:
            self.storage.finish_query_run(
                run.id, status=QueryRunStatus.FAILED,
                error={"code": exc.code, "message": exc.message},
            )
            raise
        self.storage.finish_query_run(
            run.id, status=QueryRunStatus.COMPLETED, rows_returned=len(rows),
            truncated=truncated, execution_time_ms=execution_time_ms,
        )
        return QueryExecutionResult(
            columns=columns, rows=rows, row_count=len(rows), truncated=truncated,
            execution_time_ms=execution_time_ms,
            plan_fingerprint=validated.fingerprint, warnings=validated.warnings,
        )

    @staticmethod
    def _audit_plan(plan: LogicalQueryPlan) -> dict[str, Any]:
        payload = plan.model_dump(mode="json", exclude_none=True)
        for query_filter in payload.get("filters", []):
            if "value" in query_filter:
                query_filter["value"] = "<redacted>"
        return payload
