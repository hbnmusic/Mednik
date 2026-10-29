import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="mednik-test-")
os.environ["MEDNIK_DATABASE_URL"] = f"sqlite:///{Path(_TMP) / 'test.db'}"
os.environ["MEDNIK_LLM_PROVIDER"] = "none"
os.environ["MEDNIK_UPLOAD_DIR"] = str(Path(_TMP) / "uploads")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="session", autouse=True)
def _db():
    from mednik.db import init_db

    init_db()
    yield
