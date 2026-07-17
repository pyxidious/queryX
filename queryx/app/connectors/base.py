from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from queryx.app.catalog.models import DatabaseType, SourceMetadata


class ConnectorError(RuntimeError):
    pass


class MetadataConnector(ABC):
    source: str
    database_type: DatabaseType
    source_id: str

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def scan(self) -> SourceMetadata:
        raise NotImplementedError
