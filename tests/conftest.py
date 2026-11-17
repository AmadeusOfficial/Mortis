from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from mortis.db.engine import create_db_engine, make_session_factory
from mortis.db.migrate import upgrade_database
from mortis.sources import imdb


@pytest.fixture(autouse=True)
def no_imdb_download_on_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试里起的应用启动时不去下载 IMDb 数据集；要测这一步的测试直接导入原函数。"""
    monkeypatch.setattr(imdb, "queue_refresh_if_stale", lambda *_args, **_kwargs: None)


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
