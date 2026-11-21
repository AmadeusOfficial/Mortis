"""发现新文件就整理：用 ReadDirectoryChangesW 等目录变动通知，不轮询文件。

莫提斯自己整理、重命名、写 NFO 和图片也会让目录变动。这里不按时间窗口忽略，而是对账：
防抖到点后把变动过的路径和库里记的文件比，只有出现库里没有的视频或字幕、
或者库里记的文件不见了，才排扫描。整理把文件改名后会同时改库里的路径，所以不会再触发；
库里还有相关任务在排队或在跑时先不判断，等它们结束再对账，免得读到改了一半的库。
"""

import ctypes
import json
import logging
import os
import struct
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mortis.config.models import PluginTypeConfig
from mortis.config.store import ConfigStore
from mortis.config.types import type_config
from mortis.db.models import DataType, Item, Library, MediaFile, Task
from mortis.organize.scan import MAX_GAME_SCAN_DEPTH, SkipRules, directory_is_game
from mortis.tasks import repo

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 30.0
RETRY_SECONDS = 600.0
_BUFFER_SIZE = 64 * 1024  # 网络盘上 ReadDirectoryChangesW 的缓冲不能超过 64 KB

ChangeCallback = Callable[[list[str] | None], None]
StateCallback = Callable[[str | None], None]

_ITEM_TASKS = ("scrape", "arrange")
_LIBRARY_TASKS = ("organize", "scrape_library", "arrange_library", "refresh_running_shows")


if sys.platform == "win32":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _CreateFileW = _kernel32.CreateFileW
    _CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _CreateFileW.restype = wintypes.HANDLE
    _ReadDirectoryChangesW = _kernel32.ReadDirectoryChangesW
    _ReadDirectoryChangesW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(_Overlapped),
        wintypes.LPVOID,
    ]
    _ReadDirectoryChangesW.restype = wintypes.BOOL
    _GetOverlappedResult = _kernel32.GetOverlappedResult
    _GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_Overlapped),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    _GetOverlappedResult.restype = wintypes.BOOL
    _CancelIoEx = _kernel32.CancelIoEx
    _CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped)]
    _CancelIoEx.restype = wintypes.BOOL
    _CreateEventW = _kernel32.CreateEventW
    _CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _CreateEventW.restype = wintypes.HANDLE
    _SetEvent = _kernel32.SetEvent
    _SetEvent.argtypes = [wintypes.HANDLE]
    _SetEvent.restype = wintypes.BOOL
    _ResetEvent = _kernel32.ResetEvent
    _ResetEvent.argtypes = [wintypes.HANDLE]
    _ResetEvent.restype = wintypes.BOOL
    _WaitForMultipleObjects = _kernel32.WaitForMultipleObjects
    _WaitForMultipleObjects.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    _WaitForMultipleObjects.restype = wintypes.DWORD
    _WaitForSingleObject = _kernel32.WaitForSingleObject
    _WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _WaitForSingleObject.restype = wintypes.DWORD
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL

    _INVALID_HANDLE = wintypes.HANDLE(-1).value

_FILE_LIST_DIRECTORY = 0x0001
_FILE_SHARE_ALL = 0x0001 | 0x0002 | 0x0004
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OVERLAPPED = 0x40000000
_NOTIFY_FILTER = 0x0001 | 0x0002 | 0x0008 | 0x0010  # 文件名、目录名、大小、写入时间
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_INFINITE = 0xFFFFFFFF
_ERROR_NOTIFY_ENUM_DIR = 1022


def _win_error(code: int | None = None) -> OSError:
    number = ctypes.get_last_error() if code is None else code
    return OSError(number, ctypes.FormatError(number).strip())


def _reason(error: OSError) -> str:
    return error.strerror or str(error)


def _parse_notifications(raw: bytes, size: int, root: str) -> list[str]:
    paths: list[str] = []
    offset = 0
    while offset + 12 <= size:
        next_offset, _action, length = struct.unpack_from("<III", raw, offset)
        name = raw[offset + 12 : offset + 12 + length].decode("utf-16-le", errors="replace")
        paths.append(os.path.join(root, name))
        if next_offset == 0:
            break
        offset += next_offset
    return paths


class PathWatch:
    """一个目录一个线程。停的时候设事件，线程取消挂着的读取后退出，不会卡在等待里。"""

    def __init__(
        self,
        path: str,
        on_change: ChangeCallback,
        on_state: StateCallback,
        *,
        retry_seconds: float = RETRY_SECONDS,
    ) -> None:
        self.path = path
        self._on_change = on_change
        self._on_state = on_state
        self._retry_ms = max(1, int(retry_seconds * 1000))
        self._stop_event = _CreateEventW(None, True, False, None)
        if not self._stop_event:
            raise _win_error()
        self._thread = threading.Thread(target=self._run, name="mortis-watch", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        _SetEvent(self._stop_event)

    def join(self, timeout: float = 10) -> bool:
        self._thread.join(timeout)
        if self._thread.is_alive():
            return False
        _CloseHandle(self._stop_event)
        self._stop_event = None
        return True

    def _stopping(self, wait_ms: int = 0) -> bool:
        return _WaitForSingleObject(self._stop_event, wait_ms) == _WAIT_OBJECT_0

    def _run(self) -> None:
        while not self._stopping():
            handle = _CreateFileW(
                self.path,
                _FILE_LIST_DIRECTORY,
                _FILE_SHARE_ALL,
                None,
                _OPEN_EXISTING,
                _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OVERLAPPED,
                None,
            )
            if handle in (None, _INVALID_HANDLE):
                self._on_state(_reason(_win_error()))
                self._stopping(self._retry_ms)
                continue
            try:
                self._read(handle)
            except OSError as error:
                self._on_state(_reason(error))
                _CloseHandle(handle)
                handle = None
                self._stopping(self._retry_ms)
            finally:
                if handle is not None:
                    _CloseHandle(handle)

    def _read(self, handle: Any) -> None:
        buffer = ctypes.create_string_buffer(_BUFFER_SIZE)
        overlapped = _Overlapped()
        overlapped.hEvent = _CreateEventW(None, True, False, None)
        if not overlapped.hEvent:
            raise _win_error()
        waits = (wintypes.HANDLE * 2)(overlapped.hEvent, self._stop_event)
        transferred = wintypes.DWORD()
        first = True
        try:
            while True:
                _ResetEvent(overlapped.hEvent)
                if not _ReadDirectoryChangesW(
                    handle,
                    buffer,
                    _BUFFER_SIZE,
                    True,
                    _NOTIFY_FILTER,
                    None,
                    ctypes.byref(overlapped),
                    None,
                ):
                    raise _win_error()
                if first:
                    # 第一次读取挂上以后系统才开始记变动，这时才算监听上了。
                    self._on_state(None)
                    first = False
                result = _WaitForMultipleObjects(2, waits, False, _INFINITE)
                if result == _WAIT_OBJECT_0 + 1:
                    _CancelIoEx(handle, ctypes.byref(overlapped))
                    # 等内核真正放下缓冲区再返回，之后才能释放它。
                    _GetOverlappedResult(handle, ctypes.byref(overlapped), transferred, True)
                    return
                if result != _WAIT_OBJECT_0:
                    raise _win_error()
                if not _GetOverlappedResult(handle, ctypes.byref(overlapped), transferred, False):
                    code = ctypes.get_last_error()
                    if code != _ERROR_NOTIFY_ENUM_DIR:
                        raise _win_error(code)
                    self._on_change(None)
                elif transferred.value == 0:
                    self._on_change(None)  # 变动太多缓冲区装不下，只知道有变动
                else:
                    self._on_change(_parse_notifications(buffer.raw, transferred.value, self.path))
        finally:
            _CloseHandle(overlapped.hEvent)


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
    ) -> None:
        self.session_factory = session_factory
        self.config_store = config_store
        self.debounce = debounce
        self.retry = retry
        self._wake = wake
        self._cond = threading.Condition()
        self._libraries: dict[int, _LibraryWatch] = {}
        self._started = False
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._sync_lock = threading.Lock()

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

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
            if not self.supported:
                watch.errors[path] = "这台系统不支持监听目录变动"
                continue
            path_watch = PathWatch(
                path,
                lambda changed, w=watch: self._on_change(w, changed),
                lambda error, w=watch, p=path: self._on_state(w, p, error),
                retry_seconds=self.retry,
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
