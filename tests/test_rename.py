from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.catalog.install import ensure_builtin_types
from mortis.config.models import MovieOutputConfig
from mortis.db.models import Item, Library, LibraryPath, MediaFile, utc_now
from mortis.items.identity import MovieRef, find_or_create_movie
from mortis.rename.apply import RenameError, apply_rename
from mortis.rename.plan import plan_rename


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "lib"
    root.mkdir()
    return root


def _library(session: Session, root: Path) -> Library:
    ensure_builtin_types(session)
    now = utc_now()
    library = Library(data_type_id="movie", name="电影", created_at=now, updated_at=now)
    session.add(library)
    session.flush()
    session.add(LibraryPath(library_id=library.id, path=str(root)))
    return library


def _movie(session: Session, library: Library, tmdb_id: int, title: str, year: str) -> Item:
    item, _ = find_or_create_movie(session, library, MovieRef(tmdb_id, title))
    item.title, item.release_date = title, f"{year}-01-01"
    return item


def _file(session: Session, library: Library, item: Item | None, path: Path, kind: str = "video"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(path.name.encode())
    now = utc_now()
    row = MediaFile(
        library_id=library.id,
        item_id=item.id if item else None,
        path=str(path),
        file_kind=kind,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def _paths(session: Session, item: Item) -> list[str]:
    return sorted(session.scalars(select(MediaFile.path).where(MediaFile.item_id == item.id)))


def test_single_folder_is_renamed_with_subtitles(session: Session, root: Path) -> None:
    library = _library(session, root)
    item = _movie(session, library, 680, "低俗小说", "1994")
    _file(session, library, item, root / "Pulp Fiction" / "Pulp.Fiction.1994.mkv")
    _file(session, library, item, root / "Pulp Fiction" / "Pulp.Fiction.1994.zh.srt", "subtitle")
    (root / "Pulp Fiction" / "foreign.txt").write_text("x")
    plan = plan_rename(session, item, MovieOutputConfig())
    assert not plan.problems
    apply_rename(session, plan)
    folder = root / "低俗小说 (1994)"
    assert _paths(session, item) == [str(folder / "低俗小说.mkv"), str(folder / "低俗小说.zh.srt")]
    assert (folder / "低俗小说.mkv").is_file() and (folder / "foreign.txt").is_file()
    assert not (root / "Pulp Fiction").exists()
    assert not plan_rename(session, item, MovieOutputConfig()).changes


def test_root_video_gets_its_own_folder(session: Session, root: Path) -> None:
    library = _library(session, root)
    item = _movie(session, library, 680, "低俗小说", "1994")
    _file(session, library, item, root / "pulp.mkv")
    other = _movie(session, library, 13, "阿甘正传", "1994")
    _file(session, library, other, root / "forrest.mkv")
    apply_rename(session, plan_rename(session, item, MovieOutputConfig()))
    assert (root / "低俗小说 (1994)" / "低俗小说.mkv").is_file()
    assert (root / "forrest.mkv").is_file()


def test_shared_folder_renames_files_only(session: Session, root: Path) -> None:
    library = _library(session, root)
    item = _movie(session, library, 680, "低俗小说", "1994")
    _file(session, library, item, root / "合集" / "pulp.cd1.mkv")
    _file(session, library, item, root / "合集" / "pulp.cd2.mkv")
    _file(session, library, None, root / "合集" / "unknown.mkv")
    apply_rename(session, plan_rename(session, item, MovieOutputConfig()))
    assert _paths(session, item) == [
        str(root / "合集" / "低俗小说.cd1.mkv"),
        str(root / "合集" / "低俗小说.cd2.mkv"),
    ]


def test_nested_movie_blocks_folder_rename(session: Session, root: Path) -> None:
    library = _library(session, root)
    item = _movie(session, library, 2344, "黑客帝国", "1999")
    _file(session, library, item, root / "Matrix" / "matrix.mkv")
    nested = _movie(session, library, 604, "黑客帝国2", "2003")
    _file(session, library, nested, root / "Matrix" / "Reloaded" / "reloaded.mkv")
    plan = plan_rename(session, item, MovieOutputConfig())
    assert plan.folder_to is None
    assert plan.moves[0].target == str(root / "Matrix" / "黑客帝国.mkv")


def test_conflict_skips_the_whole_item(session: Session, root: Path) -> None:
    library = _library(session, root)
    item = _movie(session, library, 680, "低俗小说", "1994")
    _file(session, library, item, root / "Pulp" / "pulp.mkv")
    (root / "Pulp" / "低俗小说.mkv").write_bytes(b"theirs")
    plan = plan_rename(session, item, MovieOutputConfig())
    assert plan.problems
    with pytest.raises(RenameError):
        apply_rename(session, plan)
    assert (root / "Pulp" / "pulp.mkv").is_file()

    (root / "Pulp" / "低俗小说.mkv").unlink()
    (root / "低俗小说 (1994)").mkdir()
    assert any(
        "目标目录已存在" in p for p in plan_rename(session, item, MovieOutputConfig()).problems
    )


def test_failure_rolls_back(session: Session, root: Path, monkeypatch) -> None:
    library = _library(session, root)
    item = _movie(session, library, 680, "低俗小说", "1994")
    _file(session, library, item, root / "Pulp" / "pulp.mkv")
    _file(session, library, item, root / "Pulp" / "pulp.srt", "subtitle")
    plan = plan_rename(session, item, MovieOutputConfig())
    import mortis.rename.apply as module

    real = module.os.rename
    calls = []

    def flaky(source, target):
        calls.append(source)
        if len(calls) == 3:
            raise OSError(5, "拒绝访问")
        real(source, target)

    monkeypatch.setattr(module.os, "rename", flaky)
    with pytest.raises(RenameError):
        apply_rename(session, plan)
    monkeypatch.setattr(module.os, "rename", real)
    assert (root / "Pulp" / "pulp.mkv").is_file() and (root / "Pulp" / "pulp.srt").is_file()
    assert _paths(session, item) == [
        str(root / "Pulp" / "pulp.mkv"),
        str(root / "Pulp" / "pulp.srt"),
    ]
