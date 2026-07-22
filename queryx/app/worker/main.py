from __future__ import annotations

import logging
import signal

from queryx.app.catalog.bootstrap import backfill_virtual_assets
from queryx.app.core.config import get_settings
from queryx.app.worker.service import WorkerService


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    backfill_virtual_assets(settings)
    worker = WorkerService(settings)

    def request_shutdown(signum: int, frame: object) -> None:
        del signum, frame
        worker.request_shutdown()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    worker.run()


if __name__ == "__main__":
    main()
