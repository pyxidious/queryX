from __future__ import annotations

import logging

from fastapi import FastAPI

from queryx.app.api.routes import router
from queryx.app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.include_router(router)
    return app


app = create_app()
