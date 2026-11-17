import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx2 as httpx

from mortis.config.store import ConfigStore
from mortis.net.http import build_client
from mortis.plugins.launch import Launcher, SystemLauncher
from mortis.plugins.registry import PluginRegistry


class KeyedLocks:
    """按名字给出进程内的锁，同名的操作串行。"""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            yield


@dataclass(frozen=True)
class Services:
    """任务和接口共用的宿主能力：数据目录、设置、网络客户端和锁。"""

    data_dir: Path
    config_store: ConfigStore
    http_transport: httpx.BaseTransport | None = None
    locks: KeyedLocks = field(default_factory=KeyedLocks)
    plugins: PluginRegistry = field(default_factory=PluginRegistry)
    launcher: Launcher = field(default_factory=SystemLauncher)

    def http_client(self) -> httpx.Client:
        return build_client(
            self.config_store.load_config().network,
            self.config_store.load_secrets(),
            transport=self.http_transport,
        )
