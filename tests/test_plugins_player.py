import subprocess
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel

from mortis.config.models import AppConfig
from mortis.plugin_api import PlayerError, PlayerPlugin
from mortis.plugins import launch as launch_module
from mortis.plugins.launch import SystemLauncher
from mortis.plugins.loader import PluginLoadError, import_plugin, read_manifest
from mortis.plugins.player import PluginPlayerHost
from mortis.plugins.registry import PluginRegistry
from tests.fakes import FakeLauncher

BUNDLED = Path(__file__).resolve().parents[1] / "plugins"

PLAYER = """
from pydantic import BaseModel

from mortis.plugin_api import PlayerPlugin


class Settings(BaseModel):
    executable: str = ""


class Fake(PlayerPlugin):
    id = "fake_player"
    name = "假播放器"
    Settings = Settings

    def unavailable_reason(self, host):
        return None if host.settings.executable else "没填路径"

    def play(self, host, path):
        host.launch(host.settings.executable, ["/new", path])


PLUGIN = Fake()
"""


def _write(folder: Path, plugin_id: str, kind: str, code: str) -> None:
    target = folder / plugin_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "plugin.toml").write_text(
        f'id = "{plugin_id}"\nname = "{plugin_id}"\nversion = "1.0"\nkind = "{kind}"\n',
        encoding="utf-8",
    )
    (target / "__init__.py").write_text(textwrap.dedent(code), encoding="utf-8")


def _config(plugin_id: str, values: dict[str, Any]) -> AppConfig:
    return AppConfig(sources={plugin_id: values})


def _bundled(plugin_id: str) -> ModuleType:
    return import_plugin(read_manifest(BUNDLED / plugin_id))


def test_registry_loads_player_plugins(tmp_path: Path) -> None:
    _write(tmp_path, "fake_player", "player", PLAYER)
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    info = registry.get("fake_player")
    assert info is not None and info.kind == "player" and info.error is None
    assert [plugin.id for plugin in registry.players()] == ["fake_player"]
    assert registry.player("fake_player") is info.plugin
    assert registry.source("fake_player") is None

    registry.load(disabled=["fake_player"])
    assert registry.players() == []
    assert registry.player("fake_player") is None
    disabled = registry.get("fake_player")
    assert disabled is not None and not disabled.enabled


def test_player_kind_requires_player_plugin(tmp_path: Path) -> None:
    _write(tmp_path, "wrong", "player", "PLUGIN = object()\n")
    _write(tmp_path, "badkind", "playback", PLAYER)
    _write(tmp_path, "sneaky", "player", "import subprocess\nPLUGIN = None\n")
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    wrong = registry.get("wrong")
    assert wrong is not None and wrong.error == "PLUGIN 不是 PlayerPlugin"
    badkind = registry.get("badkind")
    assert badkind is not None and badkind.error is not None and "player" in badkind.error
    sneaky = registry.get("sneaky")
    assert sneaky is not None and sneaky.error == "__init__.py 用了不允许的模块 subprocess"


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("import ctypes", "的模块 ctypes"),
        ("import importlib.util", "的模块 importlib.util"),
        ("from urllib.request import urlopen", "的模块 urllib.request"),
        ("import socket", "的模块 socket"),
        ("__import__('subprocess')", "的 __import__"),
        ("eval('1')", "的 eval"),
        ("import os\nos.system('calc')", "的 os.system"),
        ("import os\nos.spawnv(0, 'a', [])", "的 os.spawnv"),
        ("import os\nos.startfile('a')", "的 os.startfile"),
        ("from os import popen", "的 os.popen"),
    ],
)
def test_loader_rejects_process_and_network_escapes(tmp_path: Path, code: str, reason: str) -> None:
    _write(tmp_path, "sneaky", "player", f"{code}\nPLUGIN = None\n")
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    sneaky = registry.get("sneaky")
    assert sneaky is not None and sneaky.error is not None
    assert f"用了不允许{reason}" in sneaky.error


def test_loader_allows_ordinary_stdlib(tmp_path: Path) -> None:
    code = (
        "import os, re, platform\nfrom urllib.parse import quote\n"
        "re.compile('a'); platform.system(); os.path.isfile('a'); quote('a')\n"
    )
    _write(tmp_path, "fake_player", "player", code + PLAYER)
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    fine = registry.get("fake_player")
    assert fine is not None and fine.error is None


def test_host_launch_passes_list_and_checks_args(tmp_path: Path) -> None:
    _write(tmp_path, "fake_player", "player", PLAYER)
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    plugin = registry.player("fake_player")
    assert plugin is not None
    launcher = FakeLauncher()
    logs: list[str] = []
    config = _config("fake_player", {"executable": r"C:\播放器\play.exe"})
    host = PluginPlayerHost(plugin, launcher, config, logs.append)
    plugin.play(host, r"D:\电影\黑客帝国 (1999)\m & n.mkv")
    assert launcher.launched == [
        (r"C:\播放器\play.exe", ["/new", r"D:\电影\黑客帝国 (1999)\m & n.mkv"])
    ]
    assert logs and logs[0].startswith("[假播放器] 启动")

    with pytest.raises(PlayerError, match="字符串列表"):
        host.launch("play.exe", "not a list")  # type: ignore[arg-type]
    with pytest.raises(PlayerError, match="没有要启动的程序"):
        host.launch("  ", [])
    launcher.fail = FileNotFoundError(2, "系统找不到指定的文件。")
    with pytest.raises(PlayerError, match="启动 play.exe 失败：系统找不到指定的文件。"):
        host.launch(r"C:\播放器\play.exe", ["x"])


def test_system_launcher_never_uses_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    class FakePopen:
        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            calls.append((argv, kwargs))

        def poll(self) -> int | None:
            return None

    monkeypatch.setattr(launch_module.subprocess, "Popen", FakePopen)
    SystemLauncher().launch(r"C:\p\PotPlayerMini64.exe", [r"D:\a b\c.mkv"])
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [r"C:\p\PotPlayerMini64.exe", r"D:\a b\c.mkv"]
    assert kwargs["shell"] is False
    assert kwargs["stdout"] is subprocess.DEVNULL and kwargs["stderr"] is subprocess.DEVNULL


def test_system_launcher_open_default_uses_startfile(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(launch_module.os, "startfile", opened.append, raising=False)
    SystemLauncher().open_default(r"D:\电影\m.mkv")
    assert opened == [r"D:\电影\m.mkv"]


@pytest.fixture
def potplayer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    module = _bundled("potplayer")
    for variable in module.PROGRAM_FILES_VARS:
        monkeypatch.setenv(variable, str(tmp_path / "nowhere" / variable))
    monkeypatch.setattr(module, "registry_candidates", lambda: [])
    return module


def _host(plugin: PlayerPlugin, values: dict[str, Any], launcher: FakeLauncher) -> PluginPlayerHost:
    return PluginPlayerHost(plugin, launcher, _config(plugin.id, values), lambda _m: None)


def test_potplayer_not_found(potplayer: ModuleType) -> None:
    plugin = potplayer.PLUGIN
    host = _host(plugin, {}, FakeLauncher())
    assert not plugin.available(host)
    assert plugin.unavailable_reason(host) == "没找到 PotPlayer，在设置里填路径"
    assert plugin.detected_path(host) is None
    with pytest.raises(PlayerError, match="没找到 PotPlayer"):
        plugin.play(host, r"D:\m.mkv")


def test_potplayer_found_in_program_files(
    potplayer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    program_files = tmp_path / "Program Files"
    exe = program_files / "DAUM" / "PotPlayer" / "PotPlayerMini64.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    plugin = potplayer.PLUGIN
    launcher = FakeLauncher()
    host = _host(plugin, {}, launcher)
    assert plugin.available(host)
    assert plugin.detected_path(host) == str(exe)
    plugin.play(host, r"D:\电影\m.mkv")
    assert launcher.launched == [(str(exe), [r"D:\电影\m.mkv"])]


def test_potplayer_found_in_registry(
    potplayer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exe = tmp_path / "自定义" / "PotPlayerMini64.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(potplayer, "registry_candidates", lambda: [exe])
    host = _host(potplayer.PLUGIN, {}, FakeLauncher())
    assert potplayer.PLUGIN.detected_path(host) == str(exe)


def test_potplayer_configured_path_wins(
    potplayer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    installed = tmp_path / "Program Files" / "DAUM" / "PotPlayer" / "PotPlayerMini64.exe"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    mine = tmp_path / "绿色版" / "PotPlayerMini64.exe"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"")
    plugin = potplayer.PLUGIN
    launcher = FakeLauncher()
    plugin.play(_host(plugin, {"executable": f'"{mine}"'}, launcher), "a.mkv")
    assert launcher.launched == [(str(mine), ["a.mkv"])]

    missing = _host(plugin, {"executable": str(tmp_path / "没有.exe")}, launcher)
    assert plugin.unavailable_reason(missing) == f"填的路径找不到：{tmp_path / '没有.exe'}"


def test_system_player_opens_with_default_program() -> None:
    plugin = _bundled("system_player").PLUGIN
    launcher = FakeLauncher()
    host = _host(plugin, {}, launcher)
    assert plugin.available(host)
    plugin.play(host, r"D:\电影\m.mkv")
    assert launcher.opened == [r"D:\电影\m.mkv"]
    assert launcher.launched == []


def test_bundled_players_pass_import_check() -> None:
    registry = PluginRegistry([(BUNDLED, "builtin")])
    registry.load()
    for plugin_id in ("potplayer", "system_player"):
        info = registry.get(plugin_id)
        assert info is not None and info.kind == "player", plugin_id
        assert info.error is None, info.error
        assert isinstance(info.plugin, PlayerPlugin)
        assert issubclass(info.plugin.Settings, BaseModel)


def test_unknown_kind_message(tmp_path: Path) -> None:
    _write(tmp_path, "odd", "stream", PLAYER)
    with pytest.raises(PluginLoadError, match="data_type、source 或 player"):
        read_manifest(tmp_path / "odd")
