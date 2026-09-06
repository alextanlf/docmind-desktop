from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Batch confirmation must keep all child reservations in one supplied
# SQLAlchemy session.  This module-scoped context marker lets every Database
# instance reject accidental nested sessions opened by a reservation callback
# before they can commit outside the confirmation transaction.
_confirmation_session: ContextVar[Session | None] = ContextVar(
    "database_confirmation_session",
    default=None,
)


class Database:
    def __init__(self, url: str) -> None:
        engine_options: dict[str, object] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url:
            engine_options["poolclass"] = StaticPool
        self.url = url
        self.engine = create_engine(url, **engine_options)
        self._configure_sqlite()
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def _configure_sqlite(self) -> None:
        if not self.url.startswith("sqlite"):
            return

        is_memory_database = ":memory:" in self.url

        @event.listens_for(self.engine, "connect")
        def enable_sqlite_constraints(connection, _) -> None:  # type: ignore[no-untyped-def]
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            if not is_memory_database:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        if _confirmation_session.get() is not None:
            raise RuntimeError(
                "database sessions cannot be nested during batch confirmation"
            )
        session = self._sessions()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def bind_confirmation_session(self, session: Session) -> Generator[None, None, None]:
        """Mark the supplied session as the only session allowed in a confirmation callback."""
        token = _confirmation_session.set(session)
        try:
            yield
        finally:
            _confirmation_session.reset(token)

    def upgrade(self) -> None:
        backend_dir = Path(__file__).resolve().parents[2]
        config = Config(str(backend_dir / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", self.url)
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
