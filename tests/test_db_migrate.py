import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mortis.db.engine import create_db_engine
from mortis.db.migrate import _alembic_config, upgrade_database
from mortis.db.models import (
    Base,
    DataType,
    Item,
    ItemExternalId,
    Library,
    LibraryPath,
    Task,
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


def test_upgrade_from_0001_keeps_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    command.upgrade(_alembic_config(db_path), "0001")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into data_types (id, name, structure) values ('movie', '电影', 'item_group')"
        )
        conn.execute(
            "insert into libraries (data_type_id, name, created_at, updated_at) "
            "values ('movie', '电影', 'x', 'x')"
        )
        conn.execute(
            "insert into tasks (task_type, status, created_at) values ('organize', 'queued', 'x')"
        )
    conn.close()
    upgrade_database(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select is_auto_scrape from libraries").fetchone() == (1,)
        assert conn.execute(
            "select priority, parent_task_id, root_task_id from tasks"
        ).fetchone() == (0, None, None)
    conn.close()


def test_upgrade_from_0003_defaults_wall_collections_to_collapse(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    command.upgrade(_alembic_config(db_path), "0003")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into data_types (id, name, structure) values ('movie', '电影', 'item_group')"
        )
        conn.execute(
            "insert into libraries (data_type_id, name, created_at, updated_at) "
            "values ('movie', '电影', 'x', 'x')"
        )
    conn.close()
    upgrade_database(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select wall_collections from libraries").fetchone() == ("collapse",)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("update libraries set wall_collections = 'bogus'")
    conn.close()

    command.downgrade(_alembic_config(db_path), "0003")
    with sqlite3.connect(db_path) as conn:
        columns = [row[1] for row in conn.execute("pragma table_info(libraries)")]
        assert "wall_collections" not in columns
        assert conn.execute("select name from libraries").fetchall() == [("电影",)]
    conn.close()


def test_deleting_parent_task_deletes_children(session: Session) -> None:
    parent = Task(task_type="organize", status="succeeded", created_at=utc_now())
    session.add(parent)
    session.flush()
    session.add(
        Task(
            task_type="scrape",
            status="queued",
            created_at=utc_now(),
            parent_task_id=parent.id,
            root_task_id=parent.id,
        )
    )
    session.commit()
    session.execute(text("delete from tasks where id = :id"), {"id": parent.id})
    session.commit()
    assert session.scalar(select(func.count()).select_from(Task)) == 0


def test_utc_now_format() -> None:
    value = utc_now()
    assert value.endswith("+00:00")
    assert len(value) == len("2026-11-15T09:20:00+00:00")


def _insert_game_rows(db_path: Path, *kinds: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("insert into data_types (id, name, structure) values ('game', '游戏', 'item')")
        conn.execute(
            "insert into libraries (data_type_id, name, created_at, updated_at) "
            "values ('game', '游戏', 'x', 'x')"
        )
        for index, kind in enumerate(kinds):
            conn.execute(
                "insert into media_files (library_id, path, file_kind, created_at, updated_at) "
                "values (1, :path, :kind, 'x', 'x')",
                {"path": f"M:/game{index}", "kind": kind},
            )


def _media_files_constraints(db_path: Path) -> tuple[list[str], str]:
    engine = create_db_engine(db_path)
    try:
        inspector = inspect(engine)
        checks = inspector.get_check_constraints("media_files")
        indexes = inspector.get_indexes("media_files")
        uniques = inspector.get_unique_constraints("media_files")
    finally:
        engine.dispose()
    sqltext = next(item["sqltext"] for item in checks if "file_kind" in item["sqltext"])
    kept = [item["name"] for item in indexes if item["name"] == "ix_media_files_item"] + [
        item["name"] for item in uniques if item["name"] == "uq_media_files_path"
    ]
    return kept, sqltext


def test_upgrade_accepts_game_file_kind(tmp_path: Path) -> None:
    db_path = tmp_path / "mortis.db"
    upgrade_database(db_path)
    _insert_game_rows(db_path, "game", "video")

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select path from media_files order by id").fetchall() == [
            ("M:/game0",),
            ("M:/game1",),
        ]
    kept, sqltext = _media_files_constraints(db_path)
    assert kept == ["ix_media_files_item", "uq_media_files_path"]
    assert "'game'" in sqltext


def test_downgrade_from_0003_drops_game_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "mortis.db"
    upgrade_database(db_path)
    _insert_game_rows(db_path, "game", "video")

    command.downgrade(_alembic_config(db_path), "0002")

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select path from media_files").fetchall() == [("M:/game1",)]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into media_files (library_id, path, file_kind, created_at, updated_at) "
                "values (1, 'M:/again', 'game', 'x', 'x')"
            )
    kept, sqltext = _media_files_constraints(db_path)
    assert kept == ["ix_media_files_item", "uq_media_files_path"]
    assert "'game'" not in sqltext
