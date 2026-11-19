"""能力组合：一个包带来源、播放、钩子和数据类型，清单与代码逐项一致。"""

import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.plugin_api import HookPlugin, Plugin, SourcePlugin
from mortis.plugins.registry import PluginRegistry
from mortis.settings import Settings

HEADER = """
from mortis.plugin_api import Hit, HookPlugin, PlayerPlugin, Plugin, SourcePlugin


class Src(SourcePlugin):
    id = "me.src"
    name = "来源"
    data_type = "movie"
    provider = "me"

    def search(self, host, title):
        return [Hit(external_id="1", title=title)]

    def fetch(self, host, external_id):
        return {}

    def candidates(self, raw):
        return {}


class Play(PlayerPlugin):
    id = "me.play"
    name = "播放"

    def play(self, host, path):
        host.open_default(path)


class Hook(HookPlugin):
    id = "me.hook"
    name = "钩子"

    def on_event(self, host, event):
        pass
"""

TOML = """
id = "me.combo"
name = "组合"
version = "1.0"

[mortis]
api_version = 1

[[sources]]
id = "me.src"
name = "来源"
data_type = "movie"

[[players]]
id = "me.play"
name = "播放"

[[hooks]]
id = "me.hook"
event = "scrape.finished"
"""

FULL = HEADER + "\nPLUGIN = Plugin(sources=(Src(),), players=(Play(),), hooks=(Hook(),))\n"


def _write(root: Path, folder: str, toml: str, code: str) -> None:
    target = root / folder
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text(textwrap.dedent(toml), encoding="utf-8")
    (target / "__init__.py").write_text(code, encoding="utf-8")


def _load(root: Path, disabled: tuple[str, ...] = ()) -> PluginRegistry:
    registry = PluginRegistry([(root, "builtin")])
    registry.load(disabled)
    return registry


def test_combined_package_exposes_every_extension(tmp_path: Path) -> None:
    _write(tmp_path, "combo", TOML, FULL)
    registry = _load(tmp_path)
    package = registry.get("me.combo")
    assert package is not None and package.error is None and package.active
    assert package.capabilities == ("source", "player", "hook")
    assert package.package is not None
    assert [s.id for s in package.package.sources] == ["me.src"]
    assert [p.id for p in package.package.players] == ["me.play"]
    assert [h.id for h in package.package.hooks] == ["me.hook"]
    assert [h.id for h in package.hooks] == ["me.hook"]
    assert [pid for pid, _ in registry.hooks()] == ["me.combo"]
    assert [s.id for s in registry.sources("movie")] == ["me.src"]
    assert [p.id for p in registry.players()] == ["me.play"]
    source = registry.get("me.src")
    assert source is not None and source.parent == "me.combo"
    assert source.capabilities == ("source",) and source.data_type == "movie"
    player = registry.get("me.play")
    assert player is not None and player.capabilities == ("player",)


def test_children_toggle_independently_and_package_disables_all(tmp_path: Path) -> None:
    _write(tmp_path, "combo", TOML, FULL)
    registry = _load(tmp_path, ("me.play",))
    assert [s.id for s in registry.sources("movie")] == ["me.src"]
    assert registry.players() == []
    off = registry.get("me.play")
    assert off is not None and not off.enabled and off.error is None
    registry = _load(tmp_path, ("me.combo",))
    package = registry.get("me.combo")
    assert package is not None and not package.enabled
    assert package.capabilities == ("source", "player", "hook")
    assert registry.sources("movie") == [] and registry.hooks() == []
    child = registry.get("me.src")
    assert child is not None and not child.enabled and child.data_type == "movie"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('[[hooks]]\nid = "me.hook"\nevent = "scrape.finished"\n', "", "hooks"),
        ('id = "me.src"\nname = "来源"', 'id = "me.other"\nname = "来源"', "sources"),
        ('id = "me.play"\nname = "播放"', 'id = "me.play2"\nname = "播放"', "players"),
        ('data_type = "movie"', 'data_type = "tv"', "data_type"),
    ],
)
def test_manifest_must_match_objects(tmp_path: Path, old: str, new: str, message: str) -> None:
    assert old in TOML
    _write(tmp_path, "combo", TOML.replace(old, new), FULL)
    info = _load(tmp_path).get("me.combo")
    assert info is not None and info.error is not None and message in info.error
    assert not info.active


def test_hook_missing_from_manifest_lists_extra_object(tmp_path: Path) -> None:
    code = HEADER + "\nPLUGIN = Plugin(sources=(Src(),), players=(Play(),))\n"
    _write(tmp_path, "combo", TOML, code)
    info = _load(tmp_path).get("me.combo")
    assert info is not None and info.error is not None
    assert "hooks" in info.error and "me.hook" in info.error


def test_failed_package_lists_children_disabled_with_parent_error(tmp_path: Path) -> None:
    code = HEADER + "\nPLUGIN = Plugin(sources=(Src(),))\n"
    _write(tmp_path, "combo", TOML, code)
    registry = _load(tmp_path)
    child = registry.get("me.src")
    assert child is not None and not child.enabled
    assert child.error is not None and "me.combo" in child.error
    assert registry.sources("movie") == []


def test_hooks_have_unique_ids_across_plugins(tmp_path: Path) -> None:
    _write(tmp_path, "combo", TOML, FULL)
    other = TOML.replace('id = "me.combo"', 'id = "me.other"').replace(
        'id = "me.src"', 'id = "me.src2"'
    )
    other = other.replace('id = "me.play"', 'id = "me.play2"')
    _write(tmp_path, "other", other, FULL)
    registry = _load(tmp_path)
    errors = [info.error for info in registry.all() if info.error]
    assert errors  # 代码里的 id 和清单对不上，或者和别的包撞名，总之不会静默通过


def test_shorthand_object_cannot_list_children(tmp_path: Path) -> None:
    code = HEADER + "\nPLUGIN = Src()\n"
    _write(tmp_path, "single", TOML.replace("me.combo", "me.single"), code)
    info = _load(tmp_path).get("me.single")
    assert info is not None and info.error is not None and "不能再写" in info.error


def test_shorthand_source_and_player_keep_single_entry(tmp_path: Path) -> None:
    short = """
        id = "me.src"
        name = "来源"
        version = "1"
        [mortis]
        api_version = 1
    """
    _write(tmp_path, "src", short, HEADER + "\nPLUGIN = Src()\n")
    _write(
        tmp_path,
        "play",
        short.replace("me.src", "me.play"),
        HEADER + "\nPLUGIN = Play()\n",
    )
    registry = _load(tmp_path)
    src = registry.get("me.src")
    assert src is not None and src.parent is None and src.capabilities == ("source",)
    assert isinstance(src.plugin, SourcePlugin) and src.data_type == "movie"
    assert [s.id for s in registry.sources("movie")] == ["me.src"]
    assert [p.id for p in registry.players()] == ["me.play"]
    assert [info.id for info in registry.all()] == ["me.play", "me.src"]


def test_disabled_shorthand_still_reports_what_it_is(tmp_path: Path) -> None:
    short = """
        id = "me.play"
        name = "播放"
        version = "1"
        [mortis]
        api_version = 1
    """
    code = """
from mortis.plugin_api import PlayerPlugin


class Play(PlayerPlugin):
    id = "me.play"
    name = "播放"

    def play(self, host, path):
        host.open_default(path)


PLUGIN = Play()
"""
    _write(tmp_path, "play", short, code)
    info = _load(tmp_path, ("me.play",)).get("me.play")
    assert info is not None and not info.enabled
    assert info.capabilities == ("player",)


def test_non_plugin_object_is_rejected(tmp_path: Path) -> None:
    short = """
        id = "me.x"
        name = "x"
        version = "1"
        [mortis]
        api_version = 1
    """
    _write(tmp_path, "x", short, "PLUGIN = object()\n")
    info = _load(tmp_path).get("me.x")
    assert info is not None and info.error is not None and "不是插件对象" in info.error


def test_hook_object_must_be_hook_plugin(tmp_path: Path) -> None:
    code = HEADER + "\nPLUGIN = Plugin(sources=(Src(),), players=(Play(),), hooks=(object(),))\n"
    _write(tmp_path, "combo", TOML, code)
    info = _load(tmp_path).get("me.combo")
    assert info is not None and info.error is not None and "HookPlugin" in info.error


def test_plugin_api_exports_package_types() -> None:
    assert Plugin().sources == () and Plugin().hooks == ()
    assert issubclass(HookPlugin, object)


def test_api_lists_combined_package_by_capabilities(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    _write(builtin, "combo", TOML, FULL)
    app = create_app(Settings(data_dir=tmp_path / "data", plugins_dir=builtin), start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        listed = {item["id"]: item for item in client.get("/api/plugins").json()}
        assert "kind" not in listed["me.combo"]
        assert listed["me.combo"]["capabilities"] == ["source", "player", "hook"]
        assert sorted(listed["me.combo"]["members"]) == ["me.play", "me.src"]
        assert listed["me.combo"]["hooks"] == [
            {
                "id": "me.hook",
                "event": "scrape.finished",
                "data_types": [],
                "writes": [],
                "disabled": False,
                "reason": None,
                "failures": 0,
                "last_error": None,
                "runs": 0,
            }
        ]
        assert listed["me.src"]["capabilities"] == ["source"]
        assert listed["me.src"]["data_type"] == "movie" and listed["me.src"]["members"] == []
        assert listed["me.play"]["capabilities"] == ["player"]
        assert listed["me.play"]["available"] is True
        # 组合包里的来源和播放各自能开关。
        off = client.put("/api/plugins/me.play", json={"enabled": False}).json()
        assert off["enabled"] is False
        after = {item["id"]: item for item in client.get("/api/plugins").json()}
        assert after["me.src"]["enabled"] is True and after["me.play"]["enabled"] is False
        assert after["me.play"]["capabilities"] == ["player"]
