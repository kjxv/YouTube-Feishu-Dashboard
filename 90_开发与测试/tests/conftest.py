from collections.abc import Iterator
from pathlib import Path

import pytest
from youtube_feishu_dashboard.db.database import Database
from youtube_feishu_dashboard.db.repositories import SqlAlchemyStorage


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[SqlAlchemyStorage]:
    database_path = tmp_path / "test.db"
    database = Database(f"sqlite:///{database_path}")
    database.create_schema_for_tests()
    try:
        yield SqlAlchemyStorage(database)
    finally:
        database.dispose()
