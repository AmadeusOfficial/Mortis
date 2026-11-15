from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from mortis.db.engine import create_db_engine, make_session_factory
from mortis.db.migrate import upgrade_database


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db_path = tmp_path / "mortis.db"
    upgrade_database(db_path)
    created = create_db_engine(db_path)
    yield created
    created.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with make_session_factory(engine)() as opened:
        yield opened
