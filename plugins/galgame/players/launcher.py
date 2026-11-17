import os
import re
from collections import deque
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

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
    executables: dict[str, str] = Field(default_factory=dict)


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
        host.launch(path, [], cwd=str(Path(path).parent))
