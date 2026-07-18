from __future__ import annotations

import threading
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as parquet
import pytest

from queryx.app.processing.serving.duckdb import DuckDBLockTimeout, DuckDBServingAdapter
from queryx.app.worker.coordination import SharedFileLock


def test_duckdb_shared_lock_times_out_and_preview_waits_for_ddl_lock(tmp_path: Path) -> None:
    parquet_path = tmp_path / "data.parquet"
    parquet.write_table(pa.table({"id": [1, 2]}), parquet_path)
    lock_path = tmp_path / "queryx.duckdb.lock"
    adapter = DuckDBServingAdapter(tmp_path / "queryx.duckdb", "managed", lock_path, 0.1)
    adapter.register_view("asset_safe_v1_hash", parquet_path)

    entered = threading.Event()

    def hold_lock() -> None:
        with SharedFileLock(lock_path, 1):
            entered.set()
            time.sleep(0.25)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert entered.wait(1)
    with pytest.raises(DuckDBLockTimeout):
        adapter.preview("asset_safe_v1_hash", 1)
    thread.join()

    schema, rows = adapter.preview("asset_safe_v1_hash", 1)
    assert schema[0]["name"] == "id"
    assert len(rows) == 1


def test_preview_and_ddl_share_the_same_lock(tmp_path: Path) -> None:
    parquet_path = tmp_path / "data.parquet"
    parquet.write_table(pa.table({"id": [1]}), parquet_path)
    adapter = DuckDBServingAdapter(
        tmp_path / "queryx.duckdb",
        "managed",
        tmp_path / "queryx.duckdb.lock",
        1,
    )
    adapter.register_view("asset_safe_v1_hash", parquet_path)

    with SharedFileLock(adapter.lock_path, 1):
        result: list[object] = []
        thread = threading.Thread(
            target=lambda: result.append(adapter.preview("asset_safe_v1_hash", 1)),
        )
        thread.start()
        time.sleep(0.05)
        assert result == []
    thread.join()
    assert len(result) == 1

