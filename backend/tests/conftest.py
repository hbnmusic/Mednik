import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="mednik-test-")
# MEDNIK_TEST_DATABASE_URL=postgresql://… runs the suite against PostgreSQL in a throwaway schema.
_PG = os.environ.get("MEDNIK_TEST_DATABASE_URL")
os.environ["MEDNIK_DATABASE_URL"] = _PG or f"sqlite:///{Path(_TMP) / 'test.db'}"
if _PG:
    os.environ["MEDNIK_DB_SCHEMA"] = f"mednik_test_{os.getpid()}"
os.environ["MEDNIK_LLM_PROVIDER"] = "none"
os.environ["MEDNIK_UPLOAD_DIR"] = str(Path(_TMP) / "uploads")
os.environ.pop("MEDNIK_APP_PASSWORD", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="session", autouse=True)
def _db():
    from mednik.db import get_engine, init_db, reset_engines

    init_db()
    yield
    if _PG:
        from sqlalchemy import text

        with get_engine().begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{os.environ["MEDNIK_DB_SCHEMA"]}" CASCADE'))
    reset_engines()
