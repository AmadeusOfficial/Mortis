import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.config.models import PluginTypeConfig
from mortis.db.models import MediaFile, Task, utc_now
from mortis.libraries import create_library, delete_library, update_library
from mortis.organize.watch import PathWatch, WatchManager, needs_scan
from mortis.settings import Settings
from mortis.tasks import repo

DEBOUNCE = 0.3


def _wait(predicate: Callable[[], bool], timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.changes: list[list[str] | None] = []
        self.states: list[str | None] = []

    def change(self, paths: list[str] | None) -> None:
        with self.lock:
            self.changes.append(paths)

    def state(self, error: str | None) -> None:
        with self.lock:
            self.states.append(error)

    def seen(self) -> set[str]:
        with self.lock:
            return {path for batch in self.changes if batch for path in batch}


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


def _stop_quickly(watch: PathWatch) -> None:
    started = time.monotonic()
    watch.stop()
    assert watch.join(timeout=3)
    assert time.monotonic() - started < 1


def test_path_watch_reports_new_files_recursively(tmp_path: Path, recorder: Recorder) -> None:
    (tmp_path / "子目录").mkdir()
    watch = PathWatch(str(tmp_path), recorder.change, recorder.state)
    watch.start()
    try:
        assert _wait(lambda: recorder.states == [None])
        target = tmp_path / "子目录" / "新片.mkv"
        target.write_bytes(b"x")
        assert _wait(lambda: str(target) in recorder.seen())
    finally:
        _stop_quickly(watch)


def test_path_watch_open_failure_retries(tmp_path: Path, recorder: Recorder) -> None:
    missing = tmp_path / "断开的网络盘"
    watch = PathWatch(str(missing), recorder.change, recorder.state, retry_seconds=0.1)
    watch.start()
    try:
        assert _wait(lambda: bool(recorder.states) and recorder.states[0] is not None)
        missing.mkdir()
        assert _wait(lambda: recorder.states[-1] is None)
    finally:
        _stop_quickly(watch)


def test_path_watch_stops_while_waiting_to_retry(tmp_path: Path, recorder: Recorder) -> None:
    watch = PathWatch(str(tmp_path / "没有"), recorder.change, recorder.state, retry_seconds=600)
    watch.start()
    assert _wait(lambda: bool(recorder.states))
    _stop_quickly(watch)


# ---- 按库防抖和排扫描 ----


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return create_app(Settings(data_dir=tmp_path / "data"), start_worker=False)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "电影"
    path.mkdir()
    return path


@pytest.fixture
def library_id(app: FastAPI, folder: Path) -> int:
    with app.state.session_factory() as session:
        library = create_library(
            session, name="电影", data_type_id="movie", paths=[str(folder)], automation="watch"
        )
        return library.id


def _wait_open(manager: WatchManager, library_id: int) -> None:
    def opened() -> bool:
        status = manager.status(library_id)
        return status is not None and not status.opening

    assert _wait(opened)


class Wakes:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


@pytest.fixture
def manager(app: FastAPI, library_id: int) -> Iterator[WatchManager]:
    created = WatchManager(
        app.state.session_factory, app.state.config_store, Wakes(), debounce=DEBOUNCE, retry=0.2
    )
    created.start()
    _wait_open(created, library_id)
    yield created
    started = time.monotonic()
    created.stop(timeout=3)
    assert time.monotonic() - started < 2


def _scans(app: FastAPI) -> list[tuple[str, dict]]:
    with app.state.session_factory() as session:
        return [
            (task.status, json.loads(task.payload or "{}"))
            for task in session.scalars(
                select(Task).where(Task.task_type == "organize").order_by(Task.id)
            )
        ]


def _record(app: FastAPI, library_id: int, path: Path) -> None:
    with app.state.session_factory() as session:
        now = utc_now()
        session.add(
            MediaFile(
                library_id=library_id,
                path=str(path),
                file_kind="video",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


def test_burst_of_changes_queues_one_scan(
    app: FastAPI, manager: WatchManager, folder: Path, library_id: int
) -> None:
    assert manager.status(library_id).state == "watching"  # type: ignore[union-attr]
    movie = folder / "黑客帝国 (1999)"
    movie.mkdir()
    for index in range(5):
        (movie / f"The.Matrix.part{index}.mkv").write_bytes(b"x" * 1024)
        time.sleep(DEBOUNCE / 5)
    assert _wait(lambda: len(_scans(app)) == 1)
    time.sleep(DEBOUNCE * 3)
    assert _scans(app) == [("queued", {"library_id": library_id, "scrape": None})]


def test_own_files_do_not_queue_scans(
    app: FastAPI, manager: WatchManager, folder: Path, library_id: int
) -> None:
    old = folder / "The.Matrix.1999.mkv"
    _record(app, library_id, old)
    old.write_bytes(b"x")
    time.sleep(DEBOUNCE * 3)
    assert _scans(app) == []

    # 整理：建目录、把视频移进去改名（库里的路径同时改好），再写 NFO 和图片
    movie = folder / "黑客帝国 (1999)"
    new = movie / "黑客帝国 (1999).mkv"
    with app.state.session_factory() as session:
        row = session.scalars(select(MediaFile).where(MediaFile.path == str(old))).one()
        row.path = str(new)
        session.commit()
    movie.mkdir()
    old.rename(new)
    (movie / "黑客帝国 (1999).nfo").write_text("<movie/>", encoding="utf-8")
    (movie / "poster.jpg").write_bytes(b"x")
    (movie / "extrafanart").mkdir()
    (movie / "extrafanart" / "fanart1.jpg").write_bytes(b"x")
    time.sleep(DEBOUNCE * 4)
    assert _scans(app) == []

    (movie / "黑客帝国 (1999).chs.ass").write_bytes(b"x")
    assert _wait(lambda: len(_scans(app)) == 1)


def test_queued_scan_is_not_repeated(
    app: FastAPI, manager: WatchManager, folder: Path, library_id: int
) -> None:
    with app.state.session_factory() as session:
        repo.enqueue(session, "organize", {"library_id": library_id, "scrape": None})
        session.commit()
    (folder / "新片.mp4").write_bytes(b"x")
    time.sleep(DEBOUNCE * 4)
    assert len(_scans(app)) == 1


def test_running_task_defers_until_it_finishes(
    app: FastAPI, manager: WatchManager, folder: Path, library_id: int
) -> None:
    with app.state.session_factory() as session:
        task = repo.enqueue(session, "arrange_library", {"library_id": library_id})
        task.status = repo.RUNNING
        session.commit()
        task_id = task.id
    (folder / "新片.mp4").write_bytes(b"x")
    time.sleep(DEBOUNCE * 4)
    assert _scans(app) == []
    with app.state.session_factory() as session:
        session.get(Task, task_id).status = repo.SUCCEEDED  # type: ignore[union-attr]
        session.commit()
    assert _wait(lambda: len(_scans(app)) == 1)


def test_library_changes_restart_or_stop_threads(
    app: FastAPI, manager: WatchManager, folder: Path, library_id: int, tmp_path: Path
) -> None:
    other = tmp_path / "另一个目录"
    other.mkdir()
    with app.state.session_factory() as session:
        update_library(session, library_id, paths=[str(other)])
    manager.sync()
    _wait_open(manager, library_id)
    (other / "新片.mp4").write_bytes(b"x")
    assert _wait(lambda: len(_scans(app)) == 1)

    with app.state.session_factory() as session:
        update_library(session, library_id, automation="manual")
    manager.sync()
    assert manager.status(library_id) is None
    alive = [thread for thread in threading.enumerate() if thread.name == "mortis-watch"]
    assert alive == []

    with app.state.session_factory() as session:
        update_library(session, library_id, automation="watch")
    manager.sync()
    assert manager.status(library_id) is not None
    with app.state.session_factory() as session:
        delete_library(session, library_id)
    manager.sync()
    assert manager.status(library_id) is None


def test_open_failure_shows_reason_and_recovers(
    app: FastAPI, folder: Path, library_id: int
) -> None:
    folder.rmdir()
    created = WatchManager(
        app.state.session_factory, app.state.config_store, Wakes(), debounce=DEBOUNCE, retry=0.2
    )
    created.start()
    try:
        assert _wait(
            lambda: (status := created.status(library_id)) is not None and status.state == "failed"
        )
        status = created.status(library_id)
        assert status is not None and status.error is not None
        assert status.error.startswith(f"{folder}：")
        folder.mkdir()
        assert _wait(lambda: created.status(library_id).state == "watching")  # type: ignore[union-attr]
    finally:
        created.stop(timeout=3)


def test_needs_scan_rules(app: FastAPI, folder: Path, library_id: int) -> None:
    known = folder / "已有" / "已有.mkv"
    known.parent.mkdir()
    known.write_bytes(b"x")
    _record(app, library_id, known)
    store = app.state.config_store
    with app.state.session_factory() as session:

        def check(*paths: Path) -> bool:
            return needs_scan(session, store, library_id, {str(path) for path in paths})

        assert check(known) is False
        assert check(known.parent) is False
        (folder / "海报.jpg").write_bytes(b"x")
        assert check(folder / "海报.jpg") is False
        (folder / "字幕.chs.srt").write_bytes(b"x")
        assert check(folder / "字幕.chs.srt") is True
        sample = folder / "已有" / "sample"
        sample.mkdir()
        (sample / "sample.mkv").write_bytes(b"x")
        assert check(sample / "sample.mkv") is False
        assert check(folder / "早就不在的.mkv") is False
        known.unlink()
        assert check(known) is True
        assert check(known.parent) is False


def test_recursive_game_library_notices_nested_games(app: FastAPI, tmp_path: Path) -> None:
    root = tmp_path / "adv"
    root.mkdir()
    with app.state.session_factory() as session:
        library = create_library(
            session, name="游戏", data_type_id="galgame", paths=[str(root)]
        )
        library_id = library.id
    store = app.state.config_store
    series = root / "炎孕系列"
    game = series / "炎孕"
    game.mkdir(parents=True)
    (game / "炎孕.exe").write_bytes(b"x")
    (game / "data.xp3").write_bytes(b"x")
    save = game / "save.dat"

    def check(*paths: Path) -> bool:
        with app.state.session_factory() as session:
            return needs_scan(session, store, library_id, {str(path) for path in paths})

    store.save_section("types.galgame", PluginTypeConfig(recursive_scan=False))
    assert check(game) is False
    store.save_section("types.galgame", PluginTypeConfig(recursive_scan=True))
    assert check(series) is True
    assert check(game / "炎孕.exe") is True

    with app.state.session_factory() as session:
        now = utc_now()
        session.add(
            MediaFile(
                library_id=library_id,
                path=str(game),
                file_kind="game",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    save.write_bytes(b"x")
    assert check(save) is False
    assert check(game) is False
    for child in game.iterdir():
        child.unlink()
    game.rmdir()
    assert check(game) is True
