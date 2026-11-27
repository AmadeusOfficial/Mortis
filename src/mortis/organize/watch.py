"""发现新文件就整理：用 watchdog 监听目录变动，网络盘和收不到系统通知的挂载改轮询。

莫提斯自己整理、重命名、写 NFO 和图片也会让目录变动。这里不按时间窗口忽略，而是对账：
防抖到点后把变动过的路径和库里记的文件比，只有出现库里没有的视频或字幕、
或者库里记的文件不见了，才排扫描。整理把文件改名后会同时改库里的路径，所以不会再触发；
库里还有相关任务在排队或在跑时先不判断，等它们结束再对账，免得读到改了一半的库。

Windows 走 ReadDirectoryChangesW，Linux 走 inotify，macOS 走 FSEvents，都是 watchdog 里现成的；
NFS/CIFS 网络盘和 Docker Desktop 的绑定挂载收不到系统通知，这时用 PollingObserver 按间隔比对。
"""

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from watchdog.events import FileSystemEvent, FileSystemEventHandler, FileSystemMovedEvent
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver
from watchdog.observers.polling import PollingObserver

from mortis.config.models import PluginTypeConfig
from mortis.config.store import ConfigStore
from mortis.config.types import type_config
from mortis.db.models import DataType, Item, Library, MediaFile, Task
from mortis.organize.scan import MAX_GAME_SCAN_DEPTH, SkipRules, directory_is_game
from mortis.tasks import repo

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 30.0
RETRY_SECONDS = 600.0
_WAIT_SECONDS = 1.0
"""等停止信号时的一次等待长度；用事件等待，停止能立刻打断。"""
OBSERVER_TIMEOUT_SECONDS = 0.5
"""系统通知这一路的读超时，也是停止时要等的上限。"""
POLL_SECONDS = 5.0
"""轮询这一路的比对间隔。网络盘上目录多时打开它，别调得太小。"""

ChangeCallback = Callable[[list[str] | None], None]
StateCallback = Callable[[str | None], None]

_ITEM_TASKS = ("scrape", "arrange")
_LIBRARY_TASKS = ("organize", "scrape_library", "arrange_library", "refresh_running_shows")


def _reason(error: OSError) -> str:
    return error.strerror or str(error)


class _Handler(FileSystemEventHandler):
    """把一个目录的变动交给 PathWatch。只报路径，要不要扫描由对账决定。"""

    def __init__(
        self,
        root: str,
        on_change: ChangeCallback,
        on_lost_root: Callable[[], None],
    ) -> None:
        super().__init__()
        self._root = os.path.normcase(os.path.normpath(root))
        self._on_change = on_change
        self._on_lost_root = on_lost_root

    def on_created(self, event: FileSystemEvent) -> None:
        self._on_change([str(event.src_path)])

    def on_modified(self, event: FileSystemEvent) -> None:
        self._on_change([str(event.src_path)])

    def on_attrib(self, event: FileSystemEvent) -> None:
        self._on_change([str(event.src_path)])

    def on_moved(self, event: FileSystemEvent) -> None:
        paths = [str(event.src_path)]
        if isinstance(event, FileSystemMovedEvent):
            paths.append(str(event.dest_path))
        self._on_change(paths)

    def on_deleted(self, event: FileSystemEvent) -> None:
        path = os.path.normcase(os.path.normpath(str(event.src_path)))
        if path == self._root:
            # 库根被删不是库里的文件没了，对账也等不到，交给外面重试。
            self._on_lost_root()
            return
        self._on_change([str(event.src_path)])

    def on_events_lost(self, _event: FileSystemEvent) -> None:
        """队列或缓冲区溢出，丢了多少不知道，只知道这个目录有变动。"""
        self._on_change(None)


class PathWatch:
    """一个目录一个监听线程。系统通知收不到时，线程里换成轮询，外面看不出来。"""

    def __init__(
        self,
        path: str,
        on_change: ChangeCallback,
        on_state: StateCallback,
        *,
        retry_seconds: float = RETRY_SECONDS,
        polling: bool = False,
    ) -> None:
        self.path = path
        self._on_change = on_change
        self._on_state = on_state
        self._retry = max(0.001, retry_seconds)
        self._polling = polling
        self._stopping = threading.Event()
        self._lost_root = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mortis-watch", daemon=True)
        self._observer: BaseObserver | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._lost_root.set()  # 打断正在等的时间片

    def join(self, timeout: float = 10) -> bool:
        """等线程退出；按时退出返回真。退出时的清理在 _run 里做完。"""
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _loss(self) -> None:
        """监听自己发现根目录没了：置位后不再重新打开，交给外面重试。"""
        self._lost_root.set()

    def _paused(self, seconds: float) -> None:
        """等一会儿再重试；停止或根目录被删都能提前打断。"""
        deadline = time.monotonic() + seconds
        while not self._stopping.is_set() and not self._lost_root.is_set():
            left = deadline - time.monotonic()
            if left <= 0:
                return
            self._lost_root.wait(min(left, _WAIT_SECONDS))

    def _run(self) -> None:
        while not self._stopping.is_set():
            self._lost_root.clear()
            if not os.path.isdir(self.path):
                self._on_state(f"目录不存在：{self.path}")
                self._paused(self._retry)
                continue
            observer = self._build()
            try:
                observer.start()
            except OSError as error:
                self._stop_observer(observer)
                self._on_state(_reason(error))
                self._paused(self._retry)
                continue
            self._on_state(None)
            self._watch_until_done(observer)
            self._stop_observer(observer)
            # 根目录被删时不在这里重试，回到开头报「目录不存在」，再等它回来。

    def _build(self) -> BaseObserver:
        if self._polling:
            observer: BaseObserver = PollingObserver(timeout=POLL_SECONDS)
        else:
            observer = Observer(timeout=OBSERVER_TIMEOUT_SECONDS)
        self._observer = observer
        handler = _Handler(self.path, self._on_change, self._loss)
        observer.schedule(handler, self.path, recursive=True)
        return observer

    def _watch_until_done(self, observer: BaseObserver) -> None:
        """等到要停、根目录被删，或者监听线程自己退了。"""
        while not self._stopping.is_set() and not self._lost_root.is_set():
            if not observer.is_alive():
                self._on_state("监听线程意外退出")
                self._paused(self._retry)
                return
            self._lost_root.wait(_WAIT_SECONDS)

    def _stop_observer(self, observer: BaseObserver) -> None:
        observer.stop()
        observer.join(10)
        self._observer = None


@dataclass(frozen=True)
class WatchStatus:
    state: Literal["watching", "failed"]
    error: str | None = None
    opening: bool = False  # 还有目录没打开过，这期间的变动收不到


@dataclass
class _LibraryWatch:
    library_id: int
    paths: tuple[str, ...]
    watches: list[PathWatch] = field(default_factory=list)
    errors: dict[str, str | None] = field(default_factory=dict)
    changed: set[str] = field(default_factory=set)
    overflow: bool = False
    deadline: float | None = None


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _skipped(path: str, roots: Iterable[str], skip: SkipRules) -> bool:
    if skip.covers(path):
        return True
    for root in roots:
        if _norm(path).startswith(_norm(root) + os.sep):
            relative = os.path.relpath(path, root)
            return any(part.casefold() in skip.names for part in relative.split(os.sep)[:-1])
    return False


def _has_unknown_file(
    folder: str, known: set[str], extensions: frozenset[str], skip: SkipRules
) -> bool:
    for current, dirs, files in os.walk(folder):
        dirs[:] = [name for name in dirs if not skip.folder(os.path.join(current, name))]
        for name in files:
            path = os.path.join(current, name)
            if os.path.splitext(name)[1].lower() in extensions and not skip.path(path):
                if _norm(path) not in known:
                    return True
    return False


def _depth_under_root(path: str, roots: Iterable[str]) -> int | None:
    norm = _norm(path)
    for root in roots:
        base = _norm(root)
        if norm == base:
            return 0
        prefix = base + os.sep
        if norm.startswith(prefix):
            return norm[len(prefix) :].count(os.sep) + 1
    return None


def _inside_known_game(path: str, known: set[str]) -> bool:
    norm = _norm(path)
    return any(norm == game or norm.startswith(game + os.sep) for game in known)


def _has_new_game(folder: str, known: set[str], skip: SkipRules, depth: int) -> bool:
    if depth > MAX_GAME_SCAN_DEPTH or _norm(folder) in known:
        return False
    if directory_is_game(folder):
        return True
    try:
        with os.scandir(folder) as handle:
            entries = []
            for entry in handle:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        entries.append(entry)
                except OSError:
                    continue
    except OSError:
        return False
    for entry in entries:
        if skip.folder(entry.path) or entry.name.startswith((".", "$")):
            continue
        if _has_new_game(entry.path, known, skip, depth + 1):
            return True
    return False


def _recursive_game_change(
    changed: set[str], roots: list[str], known: set[str], skip: SkipRules
) -> bool:
    """递归打开时，更深的新游戏要扫；已经入库的游戏里写存档不算。"""
    for path in changed:
        norm = _norm(path)
        if norm in known and not os.path.isdir(path):
            return True
        if _inside_known_game(path, known):
            continue
        if _skipped(path, roots, skip):
            continue
        depth = _depth_under_root(path, roots)
        if depth is None or depth == 0:
            continue
        if os.path.isdir(path):
            if _has_new_game(path, known, skip, depth):
                return True
        elif os.path.isfile(path):
            parent = os.path.dirname(path)
            if depth - 1 >= 1 and _norm(parent) not in known and directory_is_game(parent):
                return True
    return False


def needs_scan(
    session: Session, config_store: ConfigStore, library_id: int, changed: set[str]
) -> bool:
    """变动过的路径里有库里没记的视频或字幕，或者记过的文件不见了。"""
    library = session.get(Library, library_id)
    if library is None:
        return False
    data_type = session.get(DataType, library.data_type_id)
    config = config_store.load_config()
    roots = [row.path for row in library.paths]
    section = type_config(config, library.data_type_id)
    skip = SkipRules.build(section.skip_folders)
    known = {
        _norm(path)
        for path in session.scalars(
            select(MediaFile.path).where(MediaFile.library_id == library_id)
        )
    }
    if data_type is not None and data_type.plugin_id is not None:
        recursive = isinstance(section, PluginTypeConfig) and section.recursive_scan
        if recursive:
            return _recursive_game_change(changed, roots, known, skip)
        # 游戏库一个子目录一款游戏，游戏自己写存档不算。
        top = {_norm(root) for root in roots}
        for path in changed:
            if os.path.dirname(_norm(path)) not in top:
                continue
            exists = os.path.isdir(path) and not skip.folder(path)
            if exists != (_norm(path) in known):
                return True
        return False
    extensions = frozenset(
        ext.lower() for ext in (*config.files.video_extensions, *config.files.subtitle_extensions)
    )
    for path in changed:
        if _skipped(path, roots, skip):
            continue
        key = _norm(path)
        if os.path.isfile(path):
            if os.path.splitext(path)[1].lower() in extensions and key not in known:
                return True
        elif os.path.isdir(path):
            if not skip.folder(path) and _has_unknown_file(path, known, extensions, skip):
                return True
        elif key in known or any(other.startswith(key + os.sep) for other in known):
            return True
    return False


def library_busy(session: Session, library_id: int) -> tuple[bool, bool]:
    """（这个库有扫描在排队，这个库有会动文件或库的任务在排队或在跑）。"""
    queued_scan = False
    busy = False
    item_ids: list[int] = []
    rows = session.execute(
        select(Task.task_type, Task.status, Task.payload).where(
            Task.status.in_(repo.ACTIVE), Task.task_type.in_((*_ITEM_TASKS, *_LIBRARY_TASKS))
        )
    ).all()
    for task_type, status, payload_text in rows:
        payload = json.loads(payload_text or "{}")
        if task_type in _LIBRARY_TASKS:
            if payload.get("library_id") == library_id:
                busy = True
                if task_type == "organize" and status == repo.QUEUED:
                    queued_scan = True
        elif isinstance(payload.get("item_id"), int):
            item_ids.append(payload["item_id"])
    if item_ids and not busy:
        busy = (
            session.scalar(
                select(Item.id).where(Item.id.in_(item_ids), Item.library_id == library_id).limit(1)
            )
            is not None
        )
    return queued_scan, busy


class WatchManager:
    """管所有「发现新文件就整理」的库。sync() 按库表的现状开关监听线程。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        config_store: ConfigStore,
        wake: Callable[[], None],
        *,
        debounce: float = DEBOUNCE_SECONDS,
        retry: float = RETRY_SECONDS,
        polling: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.config_store = config_store
        self.debounce = debounce
        self.retry = retry
        self.polling = polling
        """收不到系统通知的挂载（NFS/CIFS、Docker Desktop 的绑定目录）按间隔比对文件。"""
        self._wake = wake
        self._cond = threading.Condition()
        self._libraries: dict[int, _LibraryWatch] = {}
        self._started = False
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._sync_lock = threading.Lock()

    def start(self) -> None:
        with self._cond:
            if self._started:
                return
            self._started = True
            self._stopping = False
        self._thread = threading.Thread(
            target=self._loop, name="mortis-watch-debounce", daemon=True
        )
        self._thread.start()
        self.sync()

    def stop(self, timeout: float = 10) -> None:
        with self._sync_lock:
            with self._cond:
                if not self._started:
                    return
                self._started = False
                self._stopping = True
                dropped = list(self._libraries.values())
                self._libraries.clear()
                self._cond.notify_all()
            self._stop_watches(dropped, timeout)
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def status(self, library_id: int) -> WatchStatus | None:
        with self._cond:
            watch = self._libraries.get(library_id)
            if watch is None:
                return None
            failures = [f"{path}：{error}" for path, error in watch.errors.items() if error]
            opening = any(path not in watch.errors for path in watch.paths)
        if failures:
            return WatchStatus("failed", "；".join(failures), opening)
        return WatchStatus("watching", None, opening)

    def sync(self) -> None:
        if not self._started:
            return
        with self.session_factory() as session:
            wanted = {
                library.id: tuple(row.path for row in library.paths)
                for library in session.scalars(select(Library).where(Library.automation == "watch"))
            }
        with self._sync_lock:
            with self._cond:
                if not self._started:
                    return
                dropped = [
                    watch
                    for library_id, watch in self._libraries.items()
                    if wanted.get(library_id) != watch.paths
                ]
                for watch in dropped:
                    del self._libraries[watch.library_id]
                added = [
                    _LibraryWatch(library_id, paths)
                    for library_id, paths in wanted.items()
                    if library_id not in self._libraries
                ]
                for watch in added:
                    self._libraries[watch.library_id] = watch
            self._stop_watches(dropped, 10)
            for watch in added:
                self._start_watch(watch)

    def _start_watch(self, watch: _LibraryWatch) -> None:
        for path in watch.paths:
            path_watch = PathWatch(
                path,
                lambda changed, w=watch: self._on_change(w, changed),
                lambda error, w=watch, p=path: self._on_state(w, p, error),
                retry_seconds=self.retry,
                polling=self.polling,
            )
            watch.watches.append(path_watch)
            path_watch.start()

    @staticmethod
    def _stop_watches(watches: list[_LibraryWatch], timeout: float) -> None:
        threads = [path_watch for watch in watches for path_watch in watch.watches]
        for path_watch in threads:
            path_watch.stop()
        for path_watch in threads:
            if not path_watch.join(timeout):
                logger.warning("监听线程没有按时退出：%s", path_watch.path)

    def _on_state(self, watch: _LibraryWatch, path: str, error: str | None) -> None:
        with self._cond:
            if watch.errors.get(path) != error and error is not None:
                logger.warning("监听失败：%s：%s", path, error)
            watch.errors[path] = error

    def _on_change(self, watch: _LibraryWatch, changed: list[str] | None) -> None:
        with self._cond:
            if changed is None:
                watch.overflow = True
            else:
                watch.changed.update(changed)
            watch.deadline = time.monotonic() + self.debounce
            self._cond.notify_all()

    def _loop(self) -> None:
        while True:
            with self._cond:
                if self._stopping:
                    return
                now = time.monotonic()
                due: list[tuple[_LibraryWatch, set[str], bool]] = []
                wait: float | None = None
                for watch in self._libraries.values():
                    if watch.deadline is None:
                        continue
                    if watch.deadline <= now:
                        due.append((watch, watch.changed, watch.overflow))
                        watch.changed, watch.overflow, watch.deadline = set(), False, None
                    else:
                        left = watch.deadline - now
                        wait = left if wait is None else min(wait, left)
                if not due:
                    self._cond.wait(wait)
                    continue
            for watch, changed, overflow in due:
                try:
                    self._settle(watch, changed, overflow)
                except Exception:
                    logger.exception("处理「%s」的目录变动出错", watch.library_id)

    def _settle(self, watch: _LibraryWatch, changed: set[str], overflow: bool) -> None:
        with self.session_factory() as session:
            queued_scan, busy = library_busy(session, watch.library_id)
            if queued_scan:
                return
            if busy:
                with self._cond:
                    if self._libraries.get(watch.library_id) is watch:
                        watch.changed.update(changed)
                        watch.overflow = watch.overflow or overflow
                        watch.deadline = time.monotonic() + self.debounce
                        self._cond.notify_all()
                return
            if not overflow and not needs_scan(
                session, self.config_store, watch.library_id, changed
            ):
                return
            repo.enqueue(session, "organize", {"library_id": watch.library_id, "scrape": None})
            session.commit()
        self._wake()
