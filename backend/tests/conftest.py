"""Test setup: a throwaway database, a stub UI build, no real credentials.

Settings are read when the app is imported, so the environment is prepared
here, before any test module imports `app`.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="finance-tests-"))

DIST = TMP / "dist"
(DIST / "assets").mkdir(parents=True)
(DIST / "index.html").write_text(
    "<!doctype html>\n<html>\n  <head>\n    <title>t</title>\n  </head>\n  <body></body>\n</html>\n",
    encoding="utf-8",
)
(DIST / "assets" / "index-abc123.js").write_text("console.log('ui')", encoding="utf-8")

os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{(TMP / 'finance.db').as_posix()}",
        "OPTIONS_FILE": str(TMP / "no-options.json"),
        "FRONTEND_DIST": str(DIST),
        "SYNC_INTERVAL_HOURS": "0",
        "ALLOWED_CLIENT_IPS": "",
        # Environment beats backend/.env, so a real token there can't leak in.
        "STARLING_ACCESS_TOKEN": "",
        "TRADING212_API_KEY": "",
    }
)
sys.path.insert(0, str(BACKEND))


@pytest.fixture(scope="session")
def seeded() -> None:
    import seed_demo

    seed_demo.seed(reset_first=True, verbose=False)


@pytest.fixture(scope="session")
def client(seeded):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
