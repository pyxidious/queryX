from __future__ import annotations

from typing import Any

from queryx.app.ingestion.models import InspectionResult


def inspection_to_technical_metadata(inspection: InspectionResult) -> dict[str, Any]:
    """Map deterministic file inspection to a catalog-neutral technical schema.

    The adapter deliberately emits no semantic annotations and does not pretend
    that a managed file is an external database source.
    """
    return {
        "entity_kind": "file",
        "format": inspection.format.value,
        "fields": [field.model_dump(mode="json") for field in inspection.fields],
    }
