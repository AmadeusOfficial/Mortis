import os
import re
from collections import deque
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mortis.plugin_api import PlayerError, PlayerHost, PlayerPlugin

MAX_DEPTH = 3
MAX_FOUND = 200
MAX_DIRS = 500
# lib 是 Ren'Py 放 python.exe 的地方。
SKIPPED_DIRS = {"_commonredist", "redist", "directx", "__macosx", "lib"}
HELPER_PREFIXES = (
    "unins",
    "setup",
    "install",
    "config",
    "vc_redist",
    "vcredist",
    "dxsetup",
    "dxwebsetup",
    "dotnetfx",
    "oalinst",
    "physx",
    "7z",
    "unitycrashhandler",
    "notification_helper",
)
HELPER_WORDS = (
    "prereqsetup",
    "crash",
    "uninstall",
    "設定",
    "環境設定",
    "セットアップ",
    "アンインストール",
    "设置",
    "配置",
    "卸载",
)
HELPER_NAMES = {"python", "pythonw"}
CHINESE_WORDS = ("chs", "cht", "_cn", "中文", "汉化")
CHINESE_PARTS = {"sc"}
# 太短的程序名会被文件夹名偶然包含，比如 b.exe 和「White Album 2」。
MIN_MATCH = 3
# Magpie 自启动用的参数：只在托盘里运行，不弹主窗口。
MAGPIE_ARGS = ["-t"]
SANDBOXIE_SERVICE = r"SYSTEM\CurrentControlSet\Services\SbieSvc"
# Sandboxie 的沙箱名只能是字母、数字和下划线，最长 32 个字符。
SANDBOX_NAME = re.compile(r"[A-Za-z0-9_]{1,32}")


class LauncherSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    magpie_path: str = Field(
        default="",
        title="Magpie 路径",
        description="Magpie.exe 的完整路径，留空不用 Magpie",
        json_schema_extra={"format": "file-path"},
    )
    ensure_magpie: bool = Field(
        default=False,
        title="启动游戏时确保 Magpie 在运行",
        description="先启动 Magpie 再启动游戏；Magpie 已在运行时不会多开",
    )
    use_sandbox: bool = Field(
        default=False,
        title="在 Sandboxie 沙箱里启动游戏",
        description="经 Sandboxie-Plus 在下面的沙箱里启动游戏，Magpie 仍在沙箱外运行；"
        "游戏存档会写进沙箱目录",
    )
    sandboxie_path: str = Field(
        default="",
        title="Sandboxie Start.exe 路径",
        description="Sandboxie-Plus 安装目录里的 Start.exe，留空时从 Sandboxie 服务自动查找",
        json_schema_extra={"format": "file-path"},
    )
    sandbox_name: str = Field(
        default="DefaultBox",
        title="沙箱名",
        description="在 Sandboxie-Plus 里建好的沙箱名，只能用字母、数字和下划线",
    )
    executables: dict[str, str] = Field(default_factory=dict)

    @field_validator("sandbox_name")
    @classmethod
    def _check_sandbox_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not SANDBOX_NAME.fullmatch(cleaned):
            raise ValueError("沙箱名只能用字母、数字和下划线，最长 32 个字符")
        return cleaned


def _service_image_path() -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, SANDBOXIE_SERVICE) as key:
            value, _kind = winreg.QueryValueEx(key, "ImagePath")
    except OSError:
        return None
    return value if isinstance(value, str) else None


def find_start_exe() -> Path | None:
    """从 Sandboxie 服务登记的 SbieSvc.exe 找同目录的 Start.exe。"""
    raw = (_service_image_path() or "").strip()
    if raw.startswith('"'):
        raw = raw[1:].split('"', 1)[0]
    else:
        end = raw.lower().find(".exe")
        raw = raw[: end + 4] if end >= 0 else raw
    if not raw:
        return None
    start = Path(os.path.expandvars(raw)).parent / "Start.exe"
    return start if start.is_file() else None


def _normalized(name: str) -> str:
    return "".join(char for char in name.lower() if char.isalnum())


def _is_helper(stem: str) -> bool:
    lowered = stem.lower()
    return (
        lowered in HELPER_NAMES
        or lowered.startswith(HELPER_PREFIXES)
        or any(word in lowered for word in HELPER_WORDS)
    )


def _is_chinese(stem: str) -> bool:
    lowered = stem.lower()
    if any(word in lowered for word in CHINESE_WORDS):
        return True
    parts = re.split(r"[\s_\-.()\[\]]+", lowered)
    return any(part in CHINESE_PARTS for part in parts)


def _scan(folder: Path) -> list[tuple[Path, int]]:
    found: list[tuple[Path, int]] = []
    pending: deque[tuple[Path, int]] = deque([(folder, 0)])
    visited = 0
    while pending and len(found) < MAX_FOUND and visited < MAX_DIRS:
        current, depth = pending.popleft()
        visited += 1
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                name = entry.name.lower()
                if depth < MAX_DEPTH and not name.startswith(".") and name not in SKIPPED_DIRS:
                    pending.append((Path(entry.path), depth + 1))
            elif entry.name.lower().endswith(".exe") and len(found) < MAX_FOUND:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                found.append((Path(entry.path), size))
    return found


def _matches(folder_name: str, stem: str) -> bool:
    exe = _normalized(stem)
    if not exe or not folder_name:
        return False
    return folder_name in exe or (len(exe) >= MIN_MATCH and exe in folder_name)


class GalgameLauncher(PlayerPlugin):
    id: ClassVar[str] = "galgame_launcher"
    name: ClassVar[str] = "Galgame 启动"
    file_kinds: ClassVar[tuple[str, ...]] = ("game",)
    Settings: ClassVar[type[BaseModel]] = LauncherSettings

    def targets(self, host: PlayerHost, folder: str) -> list[str]:
        root = Path(folder)
        kept = [(path, size) for path, size in _scan(root) if not _is_helper(path.stem)]
        folder_name = _normalized(root.name)

        def rank(entry: tuple[Path, int]) -> tuple[bool, bool, bool, int, str]:
            path, size = entry
            relative = path.relative_to(root)
            nested = len(relative.parts) > 1
            matched = _matches(folder_name, path.stem)
            chinese = matched and _is_chinese(path.stem)
            return (not matched, not chinese, nested, -size, str(relative).lower())

        return [str(path.relative_to(root)) for path, _ in sorted(kept, key=rank)]

    def play(self, host: PlayerHost, path: str) -> None:
        settings = host.settings
        if isinstance(settings, LauncherSettings) and settings.ensure_magpie:
            raw = settings.magpie_path.strip()
            magpie = Path(raw) if raw else None
            if magpie is None or not magpie.is_file():
                host.log(f"Magpie 路径无效，没有启动 Magpie：{raw or '没有填路径'}")
            else:
                try:
                    host.launch(str(magpie), MAGPIE_ARGS, cwd=str(magpie.parent))
                except PlayerError as error:
                    host.log(f"Magpie 没能启动，照常启动游戏：{error.message}")
        cwd = str(Path(path).parent)
        if isinstance(settings, LauncherSettings) and settings.use_sandbox:
            start = _start_exe(settings)
            host.launch(str(start), [f"/box:{settings.sandbox_name}", path], cwd=cwd)
            return
        host.launch(path, [], cwd=cwd)


def _start_exe(settings: LauncherSettings) -> Path:
    """沙箱开着时找不到 Start.exe 就报错，不退回到沙箱外启动。"""
    raw = settings.sandboxie_path.strip()
    if raw:
        start = Path(raw)
        if not start.is_file():
            raise PlayerError(f"Sandboxie 的 Start.exe 路径无效，没有启动游戏：{raw}")
        return start
    found = find_start_exe()
    if found is None:
        raise PlayerError("没找到 Sandboxie 的 Start.exe，没有启动游戏；请在设置里填写路径")
    return found
