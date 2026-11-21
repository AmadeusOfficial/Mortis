"""Galgame 自带的启动插件：清单、挑选启动程序、先开 Magpie 再开游戏。"""

import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from mortis.api.app import create_app
from mortis.config.store import ConfigStore
from mortis.db.models import Item, MediaFile, utc_now
from mortis.plugin_api import PlayerError, PlayerPlugin
from mortis.plugins.registry import PluginRegistry
from mortis.settings import Settings
from tests.fakes import FakeLauncher

ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "plugins"
LOCAL = "http://127.0.0.1"
PLAYER_ID = "galgame_launcher"


@pytest.fixture(scope="module")
def plugin() -> PlayerPlugin:
    registry = PluginRegistry([(BUNDLED, "builtin")])
    registry.load()
    found = registry.player(PLAYER_ID)
    assert found is not None, registry.get(PLAYER_ID)
    return found


class FakeHost:
    """记下插件要启动的程序、参数和工作目录，不真的启动任何东西。"""

    def __init__(self, settings: BaseModel, fail: set[str] | None = None) -> None:
        self.settings = settings
        self.fail = fail or set()
        self.launched: list[tuple[str, list[str], str | None]] = []
        self.opened: list[str] = []
        self.messages: list[str] = []

    def launch(self, executable: str, args: list[str], cwd: str | None = None) -> None:
        if executable in self.fail:
            raise PlayerError("拒绝访问。")
        self.launched.append((executable, args, cwd))

    def open_default(self, path: str) -> None:
        self.opened.append(path)

    def log(self, message: str) -> None:
        self.messages.append(message)


def _host(plugin: PlayerPlugin, **values: Any) -> FakeHost:
    return FakeHost(plugin.Settings(**values))


def _touch(path: Path, size: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"M" * size)
    return path


def _rel(*parts: str) -> str:
    return str(Path(*parts))


# 清单和注册


def test_manifest_lists_launcher() -> None:
    manifest = tomllib.loads((BUNDLED / "galgame" / "plugin.toml").read_text(encoding="utf-8"))
    assert manifest["players"] == [{"id": PLAYER_ID, "name": "Galgame 启动"}]


def test_launcher_loads_under_galgame(plugin: PlayerPlugin) -> None:
    registry = PluginRegistry([(BUNDLED, "builtin")])
    registry.load()
    galgame = registry.get("galgame")
    assert galgame is not None and galgame.error is None
    assert [p.id for p in galgame.plugin.players] == [PLAYER_ID]
    info = registry.get(PLAYER_ID)
    assert info is not None and info.error is None and info.active
    assert info.capabilities == ("player",) and info.parent == "galgame"
    assert info.name == "Galgame 启动"
    assert plugin.id == PLAYER_ID and plugin.name == "Galgame 启动"
    assert plugin.file_kinds == ("game",)


def test_disabling_galgame_disables_launcher() -> None:
    registry = PluginRegistry([(BUNDLED, "builtin")])
    registry.load(disabled=["galgame"])
    info = registry.get(PLAYER_ID)
    assert info is not None and info.parent == "galgame"
    assert not info.enabled and not info.active
    assert registry.player(PLAYER_ID) is None
    assert PLAYER_ID not in [p.id for p in registry.players()]


def test_settings_schema(plugin: PlayerPlugin) -> None:
    settings = plugin.Settings()
    assert settings.magpie_path == ""
    assert settings.ensure_magpie is False
    assert settings.executables == {}
    properties = plugin.Settings.model_json_schema()["properties"]
    assert properties["magpie_path"]["title"] == "Magpie 路径"
    assert properties["ensure_magpie"]["title"] == "启动游戏时确保 Magpie 在运行"
    assert properties["magpie_path"]["description"]
    assert properties["magpie_path"]["format"] == "file-path"
    assert settings.use_sandbox is False
    assert settings.sandboxie_path == ""
    assert settings.sandbox_name == "DefaultBox"
    assert properties["use_sandbox"]["title"] == "在 Sandboxie 沙箱里启动游戏"
    assert properties["sandboxie_path"]["title"] == "Sandboxie Start.exe 路径"
    assert properties["sandboxie_path"]["format"] == "file-path"
    assert properties["sandboxie_path"]["description"]
    assert properties["sandbox_name"]["title"] == "沙箱名"


@pytest.mark.parametrize("name", ["", "我的沙箱", "Gal game", "a/b", "x" * 33])
def test_sandbox_name_rejects_bad_names(plugin: PlayerPlugin, name: str) -> None:
    with pytest.raises(ValidationError):
        plugin.Settings(sandbox_name=name)


def test_sandbox_name_is_stripped(plugin: PlayerPlugin) -> None:
    assert plugin.Settings(sandbox_name="  Galgame_2 ").sandbox_name == "Galgame_2"


def test_always_available(plugin: PlayerPlugin, tmp_path: Path) -> None:
    for values in (
        {},
        {"ensure_magpie": True},
        {"ensure_magpie": True, "magpie_path": str(tmp_path / "没有.exe")},
    ):
        host = _host(plugin, **values)
        assert plugin.unavailable_reason(host) is None
        assert plugin.available(host)


def test_detected_path_is_none(plugin: PlayerPlugin, tmp_path: Path) -> None:
    magpie = _touch(tmp_path / "Magpie" / "Magpie.exe")
    assert plugin.detected_path(_host(plugin)) is None
    assert plugin.detected_path(_host(plugin, magpie_path=str(magpie))) is None
    missing = _host(plugin, ensure_magpie=True, magpie_path=str(tmp_path / "x.exe"))
    assert plugin.detected_path(missing) is None
    host = _host(plugin, ensure_magpie=True, magpie_path=str(magpie))
    assert plugin.detected_path(host) is None


# 挑选启动程序


def test_targets_exclude_helpers(plugin: PlayerPlugin, tmp_path: Path) -> None:
    folder = tmp_path / "游戏"
    _touch(folder / "game.exe")
    for name in (
        "unins000.exe",
        "Setup.exe",
        "install.exe",
        "config.exe",
        "GameCrashReporter.exe",
        "vc_redist.x64.exe",
        "DXSETUP.exe",
        "dxwebsetup.exe",
        "UnityCrashHandler64.exe",
        "notification_helper.exe",
        "vcredist_x86.exe",
        "dotnetfx35.exe",
        "oalinst.exe",
        "PhysX_SystemSoftware.exe",
        "7z.exe",
        "7za.exe",
        "PrereqSetup.exe",
        "GameUninstall.exe",
        "python.exe",
        "pythonw.exe",
        "環境設定.exe",
        "設定.exe",
        "セットアップ.exe",
        "アンインストール.exe",
        "游戏设置.exe",
        "配置工具.exe",
        "卸载.exe",
        "readme.txt",
        "game.dll",
    ):
        _touch(folder / name)
    assert plugin.targets(_host(plugin), str(folder)) == ["game.exe"]


def test_targets_keep_names_that_only_look_like_helpers(
    plugin: PlayerPlugin, tmp_path: Path
) -> None:
    folder = tmp_path / "游戏"
    for name in ("game.exe", "python_game.exe", "MyConfigurator2.exe"):
        _touch(folder / name)
    assert sorted(plugin.targets(_host(plugin), str(folder))) == [
        "MyConfigurator2.exe",
        "game.exe",
        "python_game.exe",
    ]


def test_targets_skip_redist_and_hidden_folders(plugin: PlayerPlugin, tmp_path: Path) -> None:
    folder = tmp_path / "游戏"
    _touch(folder / "game.exe")
    for sub in ("_CommonRedist", "redist", "DirectX", "__MACOSX", ".git", "lib"):
        _touch(folder / sub / "tool.exe")
    _touch(folder / "lib" / "py3-windows-x86_64" / "renpy.exe")
    _touch(folder / "data" / "tool.exe")
    assert plugin.targets(_host(plugin), str(folder)) == ["game.exe", _rel("data", "tool.exe")]


def test_targets_empty_when_everything_excluded(plugin: PlayerPlugin, tmp_path: Path) -> None:
    folder = tmp_path / "游戏"
    _touch(folder / "setup.exe", size=10)
    _touch(folder / "unins000.exe", size=5)
    assert plugin.targets(_host(plugin), str(folder)) == []


def test_targets_prefer_chinese_build_among_matches(plugin: PlayerPlugin, tmp_path: Path) -> None:
    folder = tmp_path / "Summer Pockets"
    _touch(folder / "SummerPockets.exe", size=900)
    _touch(folder / "SummerPockets_chs.exe", size=10)
    _touch(folder / "SummerPocketsCHT.exe", size=20)
    _touch(folder / "SummerPockets_CN.exe", size=30)
    _touch(folder / "SummerPockets-SC.exe", size=40)
    _touch(folder / "SummerPockets中文版.exe", size=50)
    _touch(folder / "汉化补丁.exe", size=5000)
    _touch(folder / "SummerPocketsScene.exe", size=800)
    assert plugin.targets(_host(plugin), str(folder)) == [
        "SummerPockets中文版.exe",
        "SummerPockets-SC.exe",
        "SummerPockets_CN.exe",
        "SummerPocketsCHT.exe",
        "SummerPockets_chs.exe",
        "SummerPockets.exe",
        "SummerPocketsScene.exe",
        "汉化补丁.exe",
    ]


def test_targets_stop_after_max_dirs(
    plugin: PlayerPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 别的测试会重新加载插件，sys.modules 里的模块不一定是这个对象用的那个。
    module = type(plugin).targets.__globals__
    assert module["MAX_DIRS"] == 500
    monkeypatch.setitem(module, "MAX_DIRS", 3)
    folder = tmp_path / "游戏"
    _touch(folder / "game.exe")
    for index in range(10):
        _touch(folder / f"d{index:02}" / "x.exe")
    assert plugin.targets(_host(plugin), str(folder)) == [
        "game.exe",
        _rel("d00", "x.exe"),
        _rel("d01", "x.exe"),
    ]


def test_targets_ranking(plugin: PlayerPlugin, tmp_path: Path) -> None:
    folder = tmp_path / "White Album 2"
    _touch(folder / "big.exe", size=500)
    _touch(folder / "small.exe", size=10)
    _touch(folder / "b.exe", size=10)
    _touch(folder / "bin" / "huge.exe", size=900)
    _touch(folder / "bin" / "WhiteAlbum2.exe", size=1)
    _touch(folder / "WA2.exe", size=1)
    _touch(folder / "white_album_2_main.exe", size=1)
    assert plugin.targets(_host(plugin), str(folder)) == [
        "white_album_2_main.exe",
        _rel("bin", "WhiteAlbum2.exe"),
        "big.exe",
        "b.exe",
        "small.exe",
        "WA2.exe",
        _rel("bin", "huge.exe"),
    ]


def test_targets_folder_name_inside_exe_name(plugin: PlayerPlugin, tmp_path: Path) -> None:
    folder = tmp_path / "[Key] 星之梦 v1.02"
    _touch(folder / "a.exe", size=100)
    _touch(folder / "星之梦.exe", size=1)
    assert plugin.targets(_host(plugin), str(folder))[0] == "星之梦.exe"


def test_targets_depth_limit(plugin: PlayerPlugin, tmp_path: Path) -> None:
    folder = tmp_path / "游戏"
    _touch(folder / "a" / "b" / "c" / "ok.exe")
    _touch(folder / "a" / "b" / "c" / "d" / "deep.exe")
    assert plugin.targets(_host(plugin), str(folder)) == [_rel("a", "b", "c", "ok.exe")]


def test_targets_count_limit(plugin: PlayerPlugin, tmp_path: Path) -> None:
    folder = tmp_path / "游戏"
    for index in range(210):
        _touch(folder / f"g{index:03}.exe")
    _touch(folder / "sub" / "later.exe")
    found = plugin.targets(_host(plugin), str(folder))
    assert len(found) == 200
    assert _rel("sub", "later.exe") not in found


def test_targets_extension_case_and_empty(plugin: PlayerPlugin, tmp_path: Path) -> None:
    folder = tmp_path / "游戏"
    _touch(folder / "GAME.EXE")
    assert plugin.targets(_host(plugin), str(folder)) == ["GAME.EXE"]
    empty = tmp_path / "空"
    empty.mkdir()
    assert plugin.targets(_host(plugin), str(empty)) == []


# 启动


def test_play_without_magpie(plugin: PlayerPlugin, tmp_path: Path) -> None:
    exe = _touch(tmp_path / "游戏" / "sub" / "game.exe")
    magpie = _touch(tmp_path / "Magpie" / "Magpie.exe")
    host = _host(plugin, magpie_path=str(magpie), ensure_magpie=False)
    plugin.play(host, str(exe))
    assert host.launched == [(str(exe), [], str(exe.parent))]


def test_play_starts_magpie_first(plugin: PlayerPlugin, tmp_path: Path) -> None:
    exe = _touch(tmp_path / "游戏" / "game.exe")
    magpie = _touch(tmp_path / "Magpie" / "Magpie.exe")
    host = _host(plugin, magpie_path=str(magpie), ensure_magpie=True)
    plugin.play(host, str(exe))
    assert host.launched == [
        (str(magpie), ["-t"], str(magpie.parent)),
        (str(exe), [], str(exe.parent)),
    ]


def test_play_skips_missing_magpie(plugin: PlayerPlugin, tmp_path: Path) -> None:
    exe = _touch(tmp_path / "游戏" / "game.exe")
    for magpie_path in ("", "  ", str(tmp_path / "没有" / "Magpie.exe"), str(tmp_path)):
        host = _host(plugin, magpie_path=magpie_path, ensure_magpie=True)
        plugin.play(host, str(exe))
        assert host.launched == [(str(exe), [], str(exe.parent))], magpie_path
        assert len(host.messages) == 1, magpie_path
        assert host.messages[0].startswith("Magpie 路径无效，没有启动 Magpie：")
        if magpie_path.strip():
            assert host.messages[0].endswith(magpie_path)


def test_play_without_magpie_switch_logs_nothing(plugin: PlayerPlugin, tmp_path: Path) -> None:
    exe = _touch(tmp_path / "游戏" / "game.exe")
    host = _host(plugin, magpie_path=str(tmp_path / "没有.exe"), ensure_magpie=False)
    plugin.play(host, str(exe))
    assert host.messages == []


def test_play_continues_when_magpie_fails(plugin: PlayerPlugin, tmp_path: Path) -> None:
    exe = _touch(tmp_path / "游戏" / "game.exe")
    magpie = _touch(tmp_path / "Magpie" / "Magpie.exe")
    host = FakeHost(
        plugin.Settings(magpie_path=str(magpie), ensure_magpie=True), fail={str(magpie)}
    )
    plugin.play(host, str(exe))
    assert host.launched == [(str(exe), [], str(exe.parent))]
    assert any("Magpie" in message for message in host.messages)


def test_play_game_failure_propagates(plugin: PlayerPlugin, tmp_path: Path) -> None:
    exe = _touch(tmp_path / "游戏" / "game.exe")
    host = FakeHost(plugin.Settings(), fail={str(exe)})
    with pytest.raises(PlayerError):
        plugin.play(host, str(exe))


# 在 Sandboxie 沙箱里启动


def _module(plugin: PlayerPlugin) -> dict[str, Any]:
    # 别的测试会重新加载插件，sys.modules 里的模块不一定是这个对象用的那个。
    return type(plugin).play.__globals__


def test_play_in_sandbox(plugin: PlayerPlugin, tmp_path: Path) -> None:
    exe = _touch(tmp_path / "游戏" / "game.exe")
    start = _touch(tmp_path / "Sandboxie-Plus" / "Start.exe")
    host = _host(plugin, use_sandbox=True, sandboxie_path=str(start), sandbox_name="Galgame")
    plugin.play(host, str(exe))
    assert host.launched == [(str(start), ["/box:Galgame", str(exe)], str(exe.parent))]


def test_play_in_sandbox_with_magpie_outside(plugin: PlayerPlugin, tmp_path: Path) -> None:
    exe = _touch(tmp_path / "游戏" / "game.exe")
    start = _touch(tmp_path / "Sandboxie-Plus" / "Start.exe")
    magpie = _touch(tmp_path / "Magpie" / "Magpie.exe")
    host = _host(
        plugin,
        use_sandbox=True,
        sandboxie_path=str(start),
        sandbox_name="Galgame",
        magpie_path=str(magpie),
        ensure_magpie=True,
    )
    plugin.play(host, str(exe))
    assert host.launched == [
        (str(magpie), ["-t"], str(magpie.parent)),
        (str(start), ["/box:Galgame", str(exe)], str(exe.parent)),
    ]


def test_play_in_sandbox_refuses_bad_path(plugin: PlayerPlugin, tmp_path: Path) -> None:
    exe = _touch(tmp_path / "游戏" / "game.exe")
    for path in (str(tmp_path / "没有" / "Start.exe"), str(tmp_path)):
        host = _host(plugin, use_sandbox=True, sandboxie_path=path)
        with pytest.raises(PlayerError, match="Sandboxie"):
            plugin.play(host, str(exe))
        assert host.launched == [], path


def test_play_in_sandbox_finds_start_exe(
    plugin: PlayerPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = _touch(tmp_path / "游戏" / "game.exe")
    start = _touch(tmp_path / "Sandboxie-Plus" / "Start.exe")
    monkeypatch.setitem(_module(plugin), "find_start_exe", lambda: start)
    host = _host(plugin, use_sandbox=True, sandboxie_path="  ")
    plugin.play(host, str(exe))
    assert host.launched == [(str(start), ["/box:DefaultBox", str(exe)], str(exe.parent))]


def test_play_in_sandbox_without_sandboxie(
    plugin: PlayerPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = _touch(tmp_path / "游戏" / "game.exe")
    monkeypatch.setitem(_module(plugin), "find_start_exe", lambda: None)
    host = _host(plugin, use_sandbox=True)
    with pytest.raises(PlayerError, match="Sandboxie"):
        plugin.play(host, str(exe))
    assert host.launched == []


def test_sandbox_off_ignores_sandboxie_path(plugin: PlayerPlugin, tmp_path: Path) -> None:
    exe = _touch(tmp_path / "游戏" / "game.exe")
    host = _host(plugin, use_sandbox=False, sandboxie_path=str(tmp_path / "没有.exe"))
    plugin.play(host, str(exe))
    assert host.launched == [(str(exe), [], str(exe.parent))]


def test_find_start_exe_reads_service(
    plugin: PlayerPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module(plugin)
    start = _touch(tmp_path / "Sandboxie-Plus" / "Start.exe")
    service = f'"{tmp_path / "Sandboxie-Plus" / "SbieSvc.exe"}"'
    monkeypatch.setitem(module, "_service_image_path", lambda: service)
    assert module["find_start_exe"]() == start
    monkeypatch.setitem(module, "_service_image_path", lambda: None)
    assert module["find_start_exe"]() is None
    monkeypatch.setitem(module, "_service_image_path", lambda: str(tmp_path / "x" / "SbieSvc.exe"))
    assert module["find_start_exe"]() is None


# 经宿主的 /api/items/{id}/play


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def app(data_dir: Path, launcher: FakeLauncher) -> FastAPI:
    settings = Settings(data_dir=data_dir, plugins_dir=BUNDLED)
    return create_app(settings, start_worker=False, launcher=launcher)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def _game_item(app: FastAPI, client: TestClient, root: Path, folder: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    body = {"name": root.name, "data_type_id": "galgame", "paths": [str(root)]}
    response = client.post("/api/libraries", json=body)
    assert response.status_code in (200, 201), response.text
    library_id = response.json()["id"]
    with app.state.session_factory() as session:
        now = utc_now()
        item = Item(
            library_id=library_id,
            data_type_id="galgame",
            kind="work",
            title=folder.name,
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        session.flush()
        session.add(
            MediaFile(
                library_id=library_id,
                item_id=item.id,
                path=str(folder),
                file_kind="game",
                size=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        return item.id


def test_api_launches_game_and_magpie(
    app: FastAPI, client: TestClient, launcher: FakeLauncher, tmp_path: Path, data_dir: Path
) -> None:
    root = tmp_path / "Galgame"
    folder = root / "Dies irae"
    _touch(folder / "DiesIrae.exe", size=50)
    _touch(folder / "Config.exe")
    _touch(folder / "tools" / "patch.exe", size=100)
    item = _game_item(app, client, root, folder)
    magpie = _touch(tmp_path / "Magpie" / "Magpie.exe")

    plugins = {p["id"]: p for p in client.get("/api/plugins").json()}
    entry = plugins[PLAYER_ID]
    assert entry["parent"] == "galgame" and entry["file_kinds"] == ["game"]
    assert entry["available"] is True and entry["notice"] is None
    assert set(entry["settings_schema"]["properties"]) == {
        "magpie_path",
        "ensure_magpie",
        "use_sandbox",
        "sandboxie_path",
        "sandbox_name",
    }
    saved = client.put(
        f"/api/plugins/{PLAYER_ID}",
        json={"settings": {"magpie_path": str(magpie), "ensure_magpie": True}},
    )
    assert saved.status_code == 200, saved.text

    first = client.post(f"/api/items/{item}/play")
    assert first.status_code == 409, first.text
    targets = [c["target"] for c in first.json()["candidates"]]
    assert targets == ["DiesIrae.exe", _rel("tools", "patch.exe")]
    assert launcher.launched == []

    picked = client.post(f"/api/items/{item}/play", json={"target": "DiesIrae.exe"})
    assert picked.status_code == 200, picked.text
    assert picked.json()["player"] == PLAYER_ID
    assert launcher.launched == [(str(magpie), ["-t"]), (str(folder / "DiesIrae.exe"), [])]
    assert launcher.cwds == [str(magpie.parent), str(folder)]
    values = ConfigStore(data_dir).load_config().sources[PLAYER_ID]
    assert values["executables"] == {str(folder): "DiesIrae.exe"}
    assert values["magpie_path"] == str(magpie) and values["ensure_magpie"] is True

    again = client.post(f"/api/items/{item}/play")
    assert again.status_code == 200
    assert launcher.launched[-1] == (str(folder / "DiesIrae.exe"), [])
    assert len(launcher.launched) == 4


def test_api_magpie_failure_still_launches_game(
    app: FastAPI, client: TestClient, launcher: FakeLauncher, tmp_path: Path
) -> None:
    root = tmp_path / "Galgame"
    folder = root / "只有一个"
    _touch(folder / "game.exe")
    item = _game_item(app, client, root, folder)
    magpie = _touch(tmp_path / "Magpie" / "Magpie.exe")
    client.put(
        f"/api/plugins/{PLAYER_ID}",
        json={"settings": {"magpie_path": str(magpie), "ensure_magpie": True}},
    )
    calls: list[str] = []

    def flaky(executable: str, args: list[str], cwd: str | None = None) -> None:
        calls.append(executable)
        if executable == str(magpie):
            raise OSError(2, "系统找不到指定的文件。")
        launcher.launched.append((executable, args))
        launcher.cwds.append(cwd)

    launcher.launch = flaky  # type: ignore[method-assign]
    response = client.post(f"/api/items/{item}/play")
    assert response.status_code == 200, response.text
    assert calls == [str(magpie), str(folder / "game.exe")]
    assert launcher.launched == [(str(folder / "game.exe"), [])]
