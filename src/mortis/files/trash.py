"""把文件和文件夹移到回收站。删除条目经宿主走这里，测试换成假的，不碰系统回收站。"""

import ctypes
import os
import stat
import sys
from pathlib import Path
from typing import Protocol

DRIVE_FIXED = 3
DRIVE_REMOTE = 4
FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FOF_WANTNUKEWARNING = 0x4000
FLAGS = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING
"""放不进回收站时 FOF_WANTNUKEWARNING 让 Shell 弹框问，用户取消就是中途取消，不会悄悄永久删除。"""
COINIT_APARTMENTTHREADED = 0x2
S_OK = 0
S_FALSE = 1
RPC_E_CHANGED_MODE = 0x80010106


class TrashError(OSError):
    pass


class Trash(Protocol):
    def is_permanent(self, path: Path) -> bool:
        """这个路径所在的盘没有回收站，删除就是永久删除。"""
        ...

    def remove(self, path: Path) -> None:
        """删掉一个文件或整个文件夹；失败时抛 OSError。链接只删链接本身。"""
        ...


def double_null(path: str) -> str:
    """SHFileOperationW 的 pFrom 是一串以 \\0 分隔、以两个 \\0 结尾的路径。"""
    return path + "\0\0"


def plain(path: str) -> str:
    """去掉 \\\\?\\ 和 \\\\?\\UNC\\ 前缀，Shell 和盘符判断都不认它们。"""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def location(path: Path) -> str:
    """路径实际在哪个盘上：上级目录 resolve，最后一段原样保留，不跟随它本身的链接。"""
    try:
        parent = path.parent.resolve()
    except OSError:
        parent = Path(os.path.abspath(path.parent))
    return plain(str(parent / path.name))


def drive_type(root: str) -> int:
    return ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))


def dos_device(drive: str) -> str | None:
    """盘符背后的设备名；subst 出来的盘是 \\??\\ 开头的路径。查不到返回 None。"""
    buffer = ctypes.create_unicode_buffer(1024)
    length = ctypes.windll.kernel32.QueryDosDeviceW(ctypes.c_wchar_p(drive), buffer, len(buffer))
    return buffer.value if length else None


if sys.platform == "win32":
    from ctypes import wintypes

    class _FileOp(ctypes.Structure):
        # shellapi.h 在 32 位下按 1 字节对齐，64 位用默认对齐。
        if ctypes.sizeof(ctypes.c_void_p) == 4:
            _pack_ = 1
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", ctypes.c_void_p),
            ("pTo", ctypes.c_void_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]


def shell_delete(path: str) -> tuple[int, bool]:
    """调 SHFileOperationW 移到回收站，返回（返回值，是否中途取消）。"""
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = ctypes.c_long
    result = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED) & 0xFFFFFFFF
    initialized = result in (S_OK, S_FALSE)
    if not initialized and result != RPC_E_CHANGED_MODE:
        raise TrashError(f"没能初始化 COM（{result:#x}），不删：{path}")
    try:
        buffer = ctypes.create_unicode_buffer(double_null(path))
        operation = _FileOp()
        operation.wFunc = FO_DELETE
        operation.pFrom = ctypes.cast(buffer, ctypes.c_void_p)
        operation.fFlags = FLAGS
        function = ctypes.windll.shell32.SHFileOperationW
        function.argtypes = [ctypes.POINTER(_FileOp)]
        function.restype = ctypes.c_int
        code = function(ctypes.byref(operation))
        return code, bool(operation.fAnyOperationsAborted)
    finally:
        if initialized:
            ole32.CoUninitialize()


def _is_link(path: str) -> bool:
    return os.path.islink(path) or os.path.isjunction(path)


def _unlink(path: str) -> None:
    """只删链接本身：目录联接和目录符号链接用 rmdir，文件链接用 unlink。"""
    if os.path.isjunction(path) or os.path.isdir(path):
        os.rmdir(path)
    else:
        os.unlink(path)


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)


def _remove_tree(folder: str) -> None:
    with os.scandir(folder) as scanned:
        entries = list(scanned)
    for entry in entries:
        if entry.is_junction() or entry.is_symlink():
            _unlink(entry.path)
        elif entry.is_dir(follow_symlinks=False):
            _remove_tree(entry.path)
        else:
            _remove_file(entry.path)
    os.rmdir(folder)


def remove_permanently(path: Path) -> None:
    """自己递归删，遇到联接和符号链接只删链接，不进到链接指向的地方。"""
    target = str(path)
    if _is_link(target):
        _unlink(target)
    elif os.path.isdir(target):
        _remove_tree(target)
    else:
        _remove_file(target)


class SystemTrash:
    """Windows 本地固定盘移到回收站；其余情况没有可靠的回收站，直接删，不经 Shell。"""

    def is_permanent(self, path: Path) -> bool:
        if sys.platform != "win32":
            return True
        # resolve 会把 subst 盘换成它背后的真实盘，所以先按原路径的盘符查一次。
        given = os.path.splitdrive(plain(os.path.abspath(path)))[0]
        if given and not given.startswith("\\\\"):
            device = dos_device(given)
            if device is None or device.startswith("\\??\\"):
                return True
        drive = os.path.splitdrive(location(path))[0]
        if not drive or drive.startswith("\\\\"):
            return True
        if drive_type(drive + "\\") != DRIVE_FIXED:
            return True
        device = dos_device(drive)
        return device is None or device.startswith("\\??\\")

    def remove(self, path: Path) -> None:
        if self.is_permanent(path):
            remove_permanently(path)
            return
        target = plain(os.path.abspath(path))
        code, aborted = shell_delete(target)
        if code != 0:
            raise TrashError(f"没能移到回收站（错误码 {code:#x}）：{target}")
        if aborted:
            raise TrashError(f"移到回收站时被中止：{target}")
        if os.path.lexists(target):
            raise TrashError(f"移到回收站后文件还在：{target}")
