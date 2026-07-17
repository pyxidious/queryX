from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OllamaClient:
    base_url: str
    model: str

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)
