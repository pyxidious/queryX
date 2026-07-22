from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import URLError

import pytest

from queryx.app.core.config import Settings
from queryx.app.llm import ollama_client as client_module
from queryx.app.llm.ollama_client import (
    OllamaClient,
    OllamaTimeoutError,
    OllamaUnavailableError,
)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_chat_payload_places_think_at_top_level_and_discards_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_urlopen(request: Any, timeout: int) -> _Response:
        assert timeout == 300
        if request.full_url.endswith("/api/tags"):
            return _Response({"models": [{"name": "qwen3.5:9b"}]})
        payload = json.loads(request.data)
        captured.append(payload)
        return _Response({
            "message": {
                "content": '{"plan":"ok"}',
                "thinking": "internal reasoning must not be exposed",
            },
            "eval_count": 2,
        })

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)
    client = OllamaClient(
        "http://ollama:11434",
        "qwen3.5:9b",
        temperature=0,
        think=False,
        keep_alive="10m",
    )
    response = client.chat_json(
        [{"role": "user", "content": "return JSON"}],
        {"type": "object"},
    )

    assert captured[0]["think"] is False
    assert "think" not in captured[0]["options"]
    assert captured[0]["options"]["temperature"] == 0
    assert captured[0]["keep_alive"] == "10m"
    assert response.content == {"plan": "ok"}
    assert "thinking" not in response.content and "thinking" not in response.metrics
    assert "internal reasoning" not in repr(response)
    assert Settings().ollama_timeout_seconds == OllamaClient("http://ollama", "model").timeout_seconds == 300


def test_text_chat_extracts_content_and_ignores_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_urlopen(request: Any, timeout: int) -> _Response:
        if request.full_url.endswith("/api/tags"):
            return _Response({"models": [{"name": "qwen3.5:9b"}]})
        captured.append(json.loads(request.data))
        return _Response({
            "message": {
                "content": "  Risposta basata sul risultato.  ",
                "thinking": "reasoning riservato",
            }
        })

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)
    response = OllamaClient(
        "http://ollama:11434", "qwen3.5:9b", think=False
    ).chat_text([{"role": "user", "content": "spiega"}])

    assert response.content == "  Risposta basata sul risultato.  "
    assert "thinking" not in response.metrics
    assert "reasoning riservato" not in repr(response)
    assert captured[0]["think"] is False
    assert "format" not in captured[0]


@pytest.mark.parametrize(
    "failure",
    [TimeoutError("slow"), URLError(socket.timeout("slow"))],
)
def test_timeout_is_classified_separately_from_unavailability(
    monkeypatch: pytest.MonkeyPatch, failure: Exception,
) -> None:
    def fail_urlopen(request: Any, timeout: int) -> _Response:
        raise failure

    monkeypatch.setattr(client_module, "urlopen", fail_urlopen)
    client = OllamaClient("http://ollama:11434", "model", timeout_seconds=300)

    with pytest.raises(OllamaTimeoutError, match="300 seconds"):
        client.tags()


def test_non_timeout_network_failure_remains_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_urlopen(request: Any, timeout: int) -> _Response:
        raise URLError("connection refused")

    monkeypatch.setattr(client_module, "urlopen", fail_urlopen)
    with pytest.raises(OllamaUnavailableError):
        OllamaClient("http://ollama:11434", "model").tags()
