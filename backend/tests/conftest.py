"""Shared pytest setup.

We point the backend at an isolated SQLite file and uploads dir per test
session so the real `backend/data/*` isn't touched. This must run BEFORE any
test module imports `app.main` (which constructs the engine at import time),
so the env-var wiring lives at conftest module load — not inside a fixture.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# 1) Tempdir for DB + uploads. We keep a session-lived directory; pytest's
#    `tmp_path_factory` would also work but only inside a fixture.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="jobbee-test-"))
_DB_PATH = _TEST_ROOT / "test.db"
_UPLOADS_DIR = _TEST_ROOT / "uploads"
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# 2) Override config from the environment before `Settings()` is instantiated.
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH.as_posix()}"
os.environ["APP_ENV"] = "test"
os.environ["SECRET_STORE"] = "memory"
# Never reach a real model from tests, whatever backend/.env says.
os.environ["LLM_PROVIDER"] = "openai"
os.environ["LLM_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["APPLY_BROWSER_ENGINE"] = "playwright"
os.environ["BROWSERUSE_API_KEY"] = ""
os.environ["BROWSER_USE_API_KEY"] = ""

# 3) Import after env vars are set, then repoint the uploads dir constant.
from app import db as _db_module  # noqa: E402

_db_module.UPLOADS_DIR = _UPLOADS_DIR
_db_module.init_db()


import pytest  # noqa: E402

from app.services.secrets import MemorySecretStore, get_secret_store  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_secret_store():
    """Secrets written by one test (e.g. a fake Gmail token) must not leak into the next."""

    store = get_secret_store()
    assert isinstance(store, MemorySecretStore)
    yield
    store._values.clear()
