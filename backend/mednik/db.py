"""Database engines / sessions (SQLite by default, PostgreSQL-ready).

Two stores:
* main  — domain data (indications, drivers, assets, label sections, studies, endpoints, documents …)
* aux   — live-API payload cache, LLM response cache, extraction audit log, ingest-job log

They are separate SQLite files so that cache / audit writes made *during* an ingest transaction never
contend for the main database's write lock. With PostgreSQL (e.g. Supabase) both stores share one database;
tables are created in a dedicated schema (MEDNIK_DB_SCHEMA, default ``mednik``) so they are not exposed by
Supabase's auto-generated REST API, which serves the ``public`` schema.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import String, create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


class AuxBase(DeclarativeBase):
    pass


_engines: dict[str, Engine] = {}
_makers: dict[str, sessionmaker[Session]] = {}


@compiles(String, "postgresql")
def _pg_varchar(type_, compiler, **kw):  # pragma: no cover - DDL rendering
    # SQLite ignores VARCHAR(n) limits and the models were written against it; on PostgreSQL an over-long
    # sponsor name or title would abort an ingest, so declared lengths are not enforced there.
    return "VARCHAR"


def normalize_url(url: str) -> str:
    """postgres:// and postgresql:// (as copied from Supabase / Render) → the psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _strip_nul(session: Session, *_):  # PostgreSQL text cannot hold NUL bytes (seen in some PDF text)
    for obj in list(session.new) + list(session.dirty):
        for attr in obj.__mapper__.column_attrs:
            v = getattr(obj, attr.key, None)
            if isinstance(v, str) and "\x00" in v:
                setattr(obj, attr.key, v.replace("\x00", ""))


def _pg_connect_args(url: str) -> dict:
    args: dict = {"connect_timeout": 20, "application_name": "mednik"}
    if ":6543/" in url:  # Supabase transaction pooler: no server-side prepared statements
        args["prepare_threshold"] = None
    if "supabase." in url and "sslmode=" not in url:
        args["sslmode"] = "require"
    return args


def _make_engine(url: str) -> Engine:
    url = normalize_url(url)
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 60}
    if url.startswith("postgresql"):
        s = get_settings()
        kwargs.update(pool_size=s.db_pool_size, max_overflow=s.db_max_overflow, pool_pre_ping=True, pool_recycle=300,
                      execution_options={"schema_translate_map": {None: s.db_schema}},
                      connect_args=_pg_connect_args(url))
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
        if _engines[kind].dialect.name == "postgresql":
            event.listen(_makers[kind], "before_flush", _strip_nul)
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

    for eng in (get_engine(), get_aux_engine()):
        if eng.dialect.name == "postgresql":
            schema = get_settings().db_schema
            with eng.begin() as conn:
                conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
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
