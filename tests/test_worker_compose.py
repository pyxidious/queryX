from pathlib import Path


def test_compose_defines_one_worker_with_shared_data_volume() -> None:
    compose = Path("compose.yaml").read_text(encoding="utf-8")
    assert "queryx-worker:" in compose
    assert '["python", "-m", "queryx.app.worker"]' in compose
    assert compose.count("queryx-worker:") == 1
    assert "QUERYX_EXECUTION_MODE: worker" in compose
    assert compose.count("queryx_catalog:/app/data") >= 2
