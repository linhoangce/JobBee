"""Health endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from .. import __version__
from ..config import get_settings
from ..db import engine
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    return HealthResponse(
        app_env=settings.app_env,
        version=__version__,
        db=db_ok,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
    )
