"""Database engines / sessions (SQLite by default, PostgreSQL-ready).

Two stores:
* main  — domain data (indications, drivers, assets, label sections, studies, endpoints, documents …)
* aux   — live-API payload cache, LLM response cache, extraction audit log, ingest-job log

They are separate SQLite files so that cache / audit writes made *during* an ingest transaction never
contend for the main database's write lock. With PostgreSQL both URLs can point at the same database.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


class AuxBase(DeclarativeBase):
    pass


_engines: dict[str, Engine] = {}
_makers: dict[str, sessionmaker[Session]] = {}


def _make_engine(url: str) -> Engine:
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 60}
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    return eng


def _engine(kind: str) -> Engine:
    if kind not in _engines:
        s = get_settings()
        url = s.database_url if kind == "main" else s.aux_url
        _engines[kind] = _make_engine(url)
        _makers[kind] = sessionmaker(bind=_engines[kind], expire_on_commit=False, future=True)
    return _engines[kind]


def get_engine() -> Engine:
    return _engine("main")


def get_aux_engine() -> Engine:
    return _engine("aux")


def reset_engines() -> None:
    for e in _engines.values():
        e.dispose()
    _engines.clear()
    _makers.clear()


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(get_engine())
    AuxBase.metadata.create_all(get_aux_engine())


@contextmanager
def _scope(kind: str) -> Iterator[Session]:
    _engine(kind)
    s = _makers[kind]()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def session_scope():
    return _scope("main")


def aux_scope():
    return _scope("aux")


def get_session() -> Iterator[Session]:
    """FastAPI dependency (main store)."""
    with _scope("main") as s:
        yield s


def get_aux_session() -> Iterator[Session]:
    with _scope("aux") as s:
        yield s
