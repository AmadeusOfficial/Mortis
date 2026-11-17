import os
from collections import deque
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from mortis.plugin_api import PlayerError, PlayerHost, PlayerPlugin

MAX_DEPTH = 3
MAX_FOUND = 200
SKIPPED_DIRS = {"_commonredist", "redist", "directx", "__macosx"}
HELPER_PREFIXES = (
    "unins",
    "setup",
    "install",
    "config",
    "vc_redist",
    "dxsetup",
    "dxwebsetup",
    "unitycrashhandler",
    "notification_helper",
)
# 太短的程序名会被文件夹名偶然包含，比如 b.exe 和「White Album 2」。
MIN_MATCH = 3
# Magpie 自启动用的参数：只在托盘里运行，不弹主窗口。
MAGPIE_ARGS = ["-t"]


class LauncherSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    magpie_path: str = Field(
        default="", title="Magpie 路径", description="Magpie.exe 的完整路径，留空不用 Magpie"
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
    return lowered.startswith(HELPER_PREFIXES) or "crash" in lowered


def _scan(folder: Path) -> list[tuple[Path, int]]:
    found: list[tuple[Path, int]] = []
    pending: deque[tuple[Path, int]] = deque([(folder, 0)])
    while pending and len(found) < MAX_FOUND:
        current, depth = pending.popleft()
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

    def detected_path(self, host: PlayerHost) -> str | None:
        magpie = self._magpie(host)
        return str(magpie) if magpie is not None else None

    def targets(self, host: PlayerHost, folder: str) -> list[str]:
        root = Path(folder)
        found = _scan(root)
        kept = [(path, size) for path, size in found if not _is_helper(path.stem)] or found
        folder_name = _normalized(root.name)

        def rank(entry: tuple[Path, int]) -> tuple[bool, bool, int, str]:
            path, size = entry
            relative = path.relative_to(root)
            nested = len(relative.parts) > 1
            return (not _matches(folder_name, path.stem), nested, -size, str(relative).lower())

        return [str(path.relative_to(root)) for path, _ in sorted(kept, key=rank)]

    def play(self, host: PlayerHost, path: str) -> None:
        magpie = self._magpie(host)
        if magpie is not None:
            try:
                host.launch(str(magpie), MAGPIE_ARGS, cwd=str(magpie.parent))
            except PlayerError as error:
                host.log(f"Magpie 没能启动，照常启动游戏：{error.message}")
        host.launch(path, [], cwd=str(Path(path).parent))

    def _magpie(self, host: PlayerHost) -> Path | None:
        settings = host.settings
        if not isinstance(settings, LauncherSettings) or not settings.ensure_magpie:
            return None
        raw = settings.magpie_path.strip()
        if not raw:
            return None
        magpie = Path(raw)
        return magpie if magpie.is_file() else None
