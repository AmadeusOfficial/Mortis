"""启动本机程序。播放插件经宿主走这里，测试换成假的，不真的打开播放器。"""

import os
import subprocess
import threading
from typing import Protocol

_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
)
ERROR_ELEVATION_REQUIRED = 740
"""程序清单要求管理员权限时 CreateProcess 报的错，只有经 ShellExecute 才会弹出提权确认。"""


class Launcher(Protocol):
    def launch(self, executable: str, args: list[str], cwd: str | None = None) -> None: ...

    def open_default(self, path: str) -> None: ...

    def reveal(self, path: str, *, select: bool) -> None: ...


class SystemLauncher:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        # Popen 对象在进程没退出时被回收会报 ResourceWarning，留着引用直到它退出。
        self._children: list[subprocess.Popen[bytes]] = []

    def launch(self, executable: str, args: list[str], cwd: str | None = None) -> None:
        try:
            child = subprocess.Popen(
                [executable, *args],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=_DETACHED,
                cwd=cwd,
            )
        except OSError as error:
            if getattr(error, "winerror", None) != ERROR_ELEVATION_REQUIRED:
                raise
            self._start_elevated(executable, args, cwd)
            return
        with self._guard:
            self._children = [old for old in self._children if old.poll() is None]
            self._children.append(child)

    @staticmethod
    def _start_elevated(executable: str, args: list[str], cwd: str | None) -> None:
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise OSError("这个程序要管理员权限，这个系统不能提权启动")
        startfile(executable, arguments=subprocess.list2cmdline(args), cwd=cwd)

    def open_default(self, path: str) -> None:
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise OSError("这个系统不能用默认程序打开文件")
        startfile(path)

    def reveal(self, path: str, *, select: bool) -> None:
        """select 时在资源管理器里选中 path，否则打开 path 这个目录。"""
        if os.name != "nt":
            raise OSError("这个系统没有资源管理器")
        # explorer 按逗号拆参数，路径必须整个加引号；Windows 路径里不会有引号。
        command = f'explorer.exe /select,"{path}"' if select else f'explorer.exe "{path}"'
        child = subprocess.Popen(command, close_fds=True, creationflags=_DETACHED)
        with self._guard:
            self._children = [old for old in self._children if old.poll() is None]
            self._children.append(child)
