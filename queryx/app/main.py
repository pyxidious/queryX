from __future__ import annotations

import logging

from fastapi import FastAPI

from queryx.app.api.routes import router
from queryx.app.catalog.bootstrap import backfill_virtual_assets
from queryx.app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    backfill_virtual_assets(settings)

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.state.settings = settings
    app.include_router(router)
    if settings.queryx_ui_enabled:
        from queryx.app.ui.routes import install_exception_handlers, router as ui_router

        app.include_router(ui_router)
        install_exception_handlers(app)
    return app


app = create_app()
