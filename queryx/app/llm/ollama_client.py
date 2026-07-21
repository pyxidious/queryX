from __future__ import annotations

import json
import logging
import socket
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


class OllamaUnavailableError(OllamaError):
    pass


class OllamaModelNotFoundError(OllamaError):
    pass


class OllamaTimeoutError(OllamaError):
    pass


class OllamaInvalidResponseError(OllamaError):
    pass


@dataclass(frozen=True)
class OllamaResponse:
    content: dict[str, Any]
    metrics: dict[str, Any]
    retries: int = 0


@dataclass(frozen=True)
class OllamaClient:
    base_url: str
    model: str
    timeout_seconds: int = 300
    num_ctx: int = 8192
    temperature: float = 0
    think: bool = False
    keep_alive: str = "10m"

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    def safe_base_url(self) -> str:
        parsed = urlparse(self.base_url)
        if not parsed.netloc:
            return self.base_url
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}" if parsed.port else f"{parsed.scheme}://{parsed.hostname}"

    def health(self) -> dict[str, Any]:
        try:
            tags = self.tags()
        except OllamaUnavailableError:
            return {
                "status": "unavailable",
                "reachable": False,
                "model": self.model,
                "model_present": False,
                "base_url": self.safe_base_url(),
            }
        model_present = any(model.get("name") == self.model for model in tags.get("models", []))
        return {
            "status": "ok" if model_present else "degraded",
            "reachable": True,
            "model": self.model,
            "model_present": model_present,
            "base_url": self.safe_base_url(),
        }

    def tags(self) -> dict[str, Any]:
        return self._request("GET", "/api/tags")

    def ensure_model(self) -> None:
        tags = self.tags()
        if not any(model.get("name") == self.model for model in tags.get("models", [])):
            raise OllamaModelNotFoundError(f"Ollama model '{self.model}' not found")

    def chat_json(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> OllamaResponse:
        self.ensure_model()
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": json_schema,
            "keep_alive": self.keep_alive,
            "think": self.think,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
            },
        }
        started = monotonic()
        response = self._request("POST", "/api/chat", payload)
        duration_ms = int((monotonic() - started) * 1000)
        logger.info("Ollama chat completed in %sms", duration_ms)
        message = response.get("message", {})
        raw_content = message.get("content")
        if not isinstance(raw_content, str):
            raise OllamaInvalidResponseError("Ollama response did not include message.content")
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise OllamaInvalidResponseError("Ollama returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise OllamaInvalidResponseError("Ollama JSON response must be an object")
        metrics = {
            "total_duration": response.get("total_duration"),
            "load_duration": response.get("load_duration"),
            "prompt_eval_count": response.get("prompt_eval_count"),
            "eval_count": response.get("eval_count"),
            "prompt_eval_duration": response.get("prompt_eval_duration"),
            "generation_duration": response.get("eval_duration"),
            "request_duration_ms": duration_ms,
        }
        return OllamaResponse(content=parsed, metrics=metrics)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except (TimeoutError, socket.timeout) as exc:
            raise OllamaTimeoutError(
                f"Ollama request timed out after {self.timeout_seconds} seconds"
            ) from exc
        except HTTPError as exc:
            raise OllamaUnavailableError(f"Ollama HTTP error {exc.code}") from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise OllamaTimeoutError(
                    f"Ollama request timed out after {self.timeout_seconds} seconds"
                ) from exc
            raise OllamaUnavailableError("Ollama is not reachable") from exc
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise OllamaInvalidResponseError("Ollama returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise OllamaInvalidResponseError("Ollama response must be a JSON object")
        return parsed
