from __future__ import annotations

import fcntl
import threading
import time
from pathlib import Path
from types import TracebackType


class SharedLockTimeout(RuntimeError):
    pass


class ExecutionInterruptedError(RuntimeError):
    pass


_PROCESS_LOCK = threading.RLock()


class SharedFileLock:
    def __init__(self, path: Path, timeout_seconds: float) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._handle: object | None = None

    def __enter__(self) -> SharedFileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        if not _PROCESS_LOCK.acquire(timeout=self.timeout_seconds):
            raise SharedLockTimeout("Shared storage lock timed out")
        try:
            handle = self.path.open("a+b")
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._handle = handle
                    return self
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        handle.close()
                        raise SharedLockTimeout("Shared storage lock timed out")
                    time.sleep(min(0.05, max(deadline - time.monotonic(), 0)))
        except Exception:
            _PROCESS_LOCK.release()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self._handle
        self._handle = None
        try:
            if handle is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
        finally:
            _PROCESS_LOCK.release()
