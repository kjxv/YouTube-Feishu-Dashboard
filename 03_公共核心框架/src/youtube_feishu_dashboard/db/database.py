"""Engine 与 Session 生命周期只在公共层管理。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from youtube_feishu_dashboard.db.base import Base


class Database:
    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        url = make_url(database_url)
        if url.drivername == "sqlite" and url.database not in (None, "", ":memory:"):
            database_path = url.database
            assert database_path is not None
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)

        connect_args = {"check_same_thread": False} if url.drivername == "sqlite" else {}
        self.engine: Engine = create_engine(
            database_url,
            echo=echo,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        if url.drivername == "sqlite":
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        self._session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_schema_for_tests(self) -> None:
        """只用于测试；正式环境必须执行 Alembic 迁移。"""
        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()
