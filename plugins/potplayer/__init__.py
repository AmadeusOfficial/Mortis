"""PotPlayer 播放插件。路径留空时按常见安装位置和注册表找。"""

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from mortis.plugin_api import PlayerError, PlayerHost, PlayerPlugin

EXECUTABLES = ("PotPlayerMini64.exe", "PotPlayerMini.exe")
INSTALL_FOLDERS = (r"DAUM\PotPlayer", "PotPlayer", r"Kakao\PotPlayer")
PROGRAM_FILES_VARS = ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")
REGISTRY_KEYS = (
    ("HKEY_CURRENT_USER", r"Software\DAUM\PotPlayer64", "ProgramPath"),
    ("HKEY_CURRENT_USER", r"Software\DAUM\PotPlayer", "ProgramPath"),
    (
        "HKEY_LOCAL_MACHINE",
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\PotPlayerMini64.exe",
        "",
    ),
    (
        "HKEY_LOCAL_MACHINE",
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\PotPlayerMini.exe",
        "",
    ),
)
NOT_FOUND = "没找到 PotPlayer，在设置里填路径"


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    executable: str = Field(
        default="",
        title="播放器路径",
        description="PotPlayerMini64.exe 的完整路径。留空时自动找常见安装位置和注册表。",
    )


def install_candidates() -> list[Path]:
    found: list[Path] = []
    for variable in PROGRAM_FILES_VARS:
        root = os.environ.get(variable)
        if not root:
            continue
        for folder in INSTALL_FOLDERS:
            for name in EXECUTABLES:
                candidate = Path(root) / folder / name
                if candidate not in found:
                    found.append(candidate)
    return found


def registry_candidates() -> list[Path]:
    try:
        import winreg
    except ImportError:
        return []
    found: list[Path] = []
    for hive_name, key_path, value_name in REGISTRY_KEYS:
        hive = getattr(winreg, hive_name)
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _kind = winreg.QueryValueEx(key, value_name)
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            found.append(Path(value.strip().strip('"')))
    return found


def find_executable(configured: str) -> Path | None:
    if configured.strip():
        path = Path(configured.strip().strip('"'))
        return path if path.is_file() else None
    for candidate in [*install_candidates(), *registry_candidates()]:
        if candidate.is_file():
            return candidate
    return None


class PotPlayer(PlayerPlugin):
    id = "potplayer"
    name = "PotPlayer"
    Settings = Settings

    @staticmethod
    def _configured(host: PlayerHost) -> str:
        value = getattr(host.settings, "executable", "")
        return value if isinstance(value, str) else ""

    def unavailable_reason(self, host: PlayerHost) -> str | None:
        configured = self._configured(host)
        if find_executable(configured) is not None:
            return None
        if configured.strip():
            return f"填的路径找不到：{configured.strip()}"
        return NOT_FOUND

    def detected_path(self, host: PlayerHost) -> str | None:
        found = find_executable(self._configured(host))
        return str(found) if found is not None else None

    def play(self, host: PlayerHost, path: str) -> None:
        executable = find_executable(self._configured(host))
        if executable is None:
            raise PlayerError(self.unavailable_reason(host) or NOT_FOUND)
        host.launch(str(executable), [path])


PLUGIN = PotPlayer()
