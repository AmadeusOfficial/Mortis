import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mortis.db.engine import create_db_engine
from mortis.db.migrate import upgrade_database
from mortis.db.models import (
    Base,
    DataType,
    Item,
    ItemExternalId,
    Library,
    LibraryPath,
    utc_now,
)

EXPECTED_TABLES = {
    "data_types",
    "type_fields",
    "libraries",
    "library_paths",
    "items",
    "item_external_ids",
    "item_source_records",
    "people",
    "person_external_ids",
    "item_people",
    "tags",
    "item_tags",
    "item_images",
    "item_ratings",
    "source_orders",
    "media_files",
    "tasks",
    "schedules",
}


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def test_upgrade_creates_all_tables_in_non_ascii_dir(tmp_path: Path) -> None:
    db_path = tmp_path / "数据" / "mortis.db"
    assert upgrade_database(db_path) is None
    engine = create_db_engine(db_path)
    try:
        assert _tables(engine) == EXPECTED_TABLES
    finally:
        engine.dispose()


def test_migration_matches_models(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_upgrade_twice_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "mortis.db"
    upgrade_database(db_path)
    assert upgrade_database(db_path) is None
    assert list(tmp_path.glob("*.bak")) == []


def test_backup_before_upgrading_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "mortis.db"
    conn = sqlite3.connect(db_path)
    conn.execute("create table legacy (x integer)")
    conn.commit()
    conn.close()

    backup = upgrade_database(db_path)

    assert backup is not None and backup.is_file()
    assert backup.parent == tmp_path
    engine = create_db_engine(db_path)
    try:
        assert EXPECTED_TABLES <= _tables(engine)
    finally:
        engine.dispose()


def test_unknown_revision_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "mortis.db"
    upgrade_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("update alembic_version set version_num = 'ffffffff'")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="高于当前代码"):
        upgrade_database(db_path)
    assert list(tmp_path.glob("*.bak")) == []


def test_connection_pragmas(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30000


def _movie_library(session: Session) -> Library:
    now = utc_now()
    session.add(DataType(id="movie", name="电影", structure="item_group"))
    library = Library(data_type_id="movie", name="电影", created_at=now, updated_at=now)
    session.add(library)
    session.flush()
    return library


def test_check_constraint_rejects_unknown_kind(session: Session) -> None:
    library = _movie_library(session)
    now = utc_now()
    session.add(
        Item(
            library_id=library.id,
            data_type_id="movie",
            kind="bogus",
            title="x",
            created_at=now,
            updated_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_item_defaults_and_cascade(session: Session) -> None:
    library = _movie_library(session)
    now = utc_now()
    item = Item(
        library_id=library.id,
        data_type_id="movie",
        kind="work",
        title="教父",
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    session.flush()
    session.add(ItemExternalId(item_id=item.id, provider="tmdb", external_id="238", is_primary=1))
    session.commit()
    session.refresh(item)
    assert (item.fields, item.field_sources, item.locked_fields) == ("{}", "{}", "[]")

    session.execute(text("delete from items where id = :id"), {"id": item.id})
    session.commit()
    assert session.scalars(select(ItemExternalId)).all() == []


def test_library_paths_cascade_and_unique(session: Session) -> None:
    library = _movie_library(session)
    library.paths.append(LibraryPath(path=r"M:\plex\movie"))
    session.commit()

    now = utc_now()
    other = Library(data_type_id="movie", name="另一个", created_at=now, updated_at=now)
    other.paths.append(LibraryPath(path=r"M:\plex\movie"))
    session.add(other)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    session.delete(session.get(Library, library.id))
    session.commit()
    assert session.scalars(select(LibraryPath)).all() == []


def test_utc_now_format() -> None:
    value = utc_now()
    assert value.endswith("+00:00")
    assert len(value) == len("2026-11-15T09:20:00+00:00")
