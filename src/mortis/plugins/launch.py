"""启动本机程序。播放插件经宿主走这里，测试换成假的，不真的打开播放器。"""

import os
import subprocess
import threading
from typing import Protocol

_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
)


class Launcher(Protocol):
    def launch(self, executable: str, args: list[str]) -> None: ...

    def open_default(self, path: str) -> None: ...


class SystemLauncher:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        # Popen 对象在进程没退出时被回收会报 ResourceWarning，留着引用直到它退出。
        self._children: list[subprocess.Popen[bytes]] = []

    def launch(self, executable: str, args: list[str]) -> None:
        child = subprocess.Popen(
            [executable, *args],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=_DETACHED,
        )
        with self._guard:
            self._children = [old for old in self._children if old.poll() is None]
            self._children.append(child)

    def open_default(self, path: str) -> None:
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise OSError("这个系统不能用默认程序打开文件")
        startfile(path)
