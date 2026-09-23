"""Database engine, session factory and the declarative base.

Deliberate choice: synchronous SQLAlchemy. The 1 Hz simulation loop performs one
short transaction per tick, which Phase 2 runs via `asyncio.to_thread` rather than
blocking the event loop. Sync keeps Alembic, tests and the loop simple, and the write
volume here never justifies an async driver. Recorded in docs/decisions.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config.settings import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    future=True,
    # A bounded connect timeout matters: without it the API hangs indefinitely when the
    # database is unreachable. The process must start, report an explicit storage error
    # and keep /health answering instead of silently never becoming ready.
    connect_args={"connect_timeout": _settings.db_connect_timeout_seconds},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Short transaction boundary. A write failure must surface, never be swallowed."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as session:
        yield session
