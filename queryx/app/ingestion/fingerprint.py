from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def file_fingerprint(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def configuration_fingerprint(configuration: dict[str, Any]) -> str:
    payload = json.dumps(configuration, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def technical_schema_fingerprint(schema: list[dict[str, Any]]) -> str:
    normalized = sorted(schema, key=lambda field: field.get("name", ""))
    return configuration_fingerprint({"schema": normalized})
