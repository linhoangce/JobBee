"""FastAPI entry point.

`uvicorn app.main:app --reload` runs the dev server.
"""

from __future__ import annotations

import asyncio
import sys

# Playwright spawns a browser subprocess; uvicorn's default Windows loop cannot do that.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import (
    agent as agent_router,
)
from .api import (
    applications as applications_router,
)
from .api import (
    apply_runs as apply_runs_router,
)
from .api import (
    documents as documents_router,
)
from .api import (
    email as email_router,
)
from .api import (
    health,
    llm,
)
from .api import (
    home as home_router,
)
from .api import (
    messaging as messaging_router,
)
from .api import (
    preferences as preferences_router,
)
from .api import (
    profile as profile_router,
)
from .api import (
    settings as settings_router,
)
from .api import (
    setup as setup_router,
)
from .api import (
    site_accounts as site_accounts_router,
)
from .api import (
    watchlist as watchlist_router,
)
from .apply.runs import run_manager
from .config import get_settings
from .db import init_db
from .messaging.service import messaging
from .pipeline.discovery import discovery
from .pipeline.flow import job_flow
from .pipeline.scheduler import scheduler


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    job_flow.register()
    run_manager.register_chat_actions(messaging)
    await run_manager.startup()
    await messaging.start()
    if get_settings().app_env != "test":
        scheduler.start()
    yield
    await scheduler.stop()
    await discovery.stop()
    await messaging.stop()
    await run_manager.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="JobBee API",
        version=__version__,
        lifespan=lifespan,
    )

    # In dev the Vite proxy handles same-origin, but allow direct browser calls too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"]
        if settings.is_dev
        else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(settings_router.router, prefix="/api")
    app.include_router(llm.router, prefix="/api")
    app.include_router(profile_router.router, prefix="/api")
    app.include_router(documents_router.router, prefix="/api")
    app.include_router(applications_router.router, prefix="/api")
    app.include_router(apply_runs_router.router, prefix="/api")
    app.include_router(email_router.router, prefix="/api")
    app.include_router(messaging_router.router, prefix="/api")
    app.include_router(home_router.router, prefix="/api")
    app.include_router(agent_router.router, prefix="/api")
    app.include_router(setup_router.router, prefix="/api")
    app.include_router(preferences_router.router, prefix="/api")
    app.include_router(watchlist_router.router, prefix="/api")
    app.include_router(site_accounts_router.router, prefix="/api")

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "name": "JobBee API",
            "version": __version__,
            "docs": "/docs",
            "health": "/api/health",
        }

    return app


app = create_app()
