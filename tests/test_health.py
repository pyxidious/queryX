from __future__ import annotations

from queryx.app.api import routes


class _HealthyOrchestrator:
    def health_checks(self) -> dict[str, dict[str, bool]]:
        return {"mysql": {"ok": True}, "mongodb": {"ok": False}}


def test_health_returns_degraded_when_one_source_is_down() -> None:
    original = routes._build_orchestrator
    routes._build_orchestrator = lambda settings=None: _HealthyOrchestrator()  # type: ignore[assignment]
    try:
        response = routes.health()
    finally:
        routes._build_orchestrator = original

    assert response["status"] == "degraded"
    assert response["checks"]["mysql"]["ok"] is True
    assert response["checks"]["mongodb"]["ok"] is False
