"""数据类型插件带的播放插件：清单 [[players]] 和 PLUGIN.players 的校验、停用、覆盖、重名。"""

from pathlib import Path

from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.plugin_api import PlayerPlugin
from mortis.plugins.loader import PluginLoadError, read_manifest
from mortis.plugins.registry import PluginRegistry
from mortis.settings import Settings
from tests.fakes import FakeLauncher

LOCAL = "http://127.0.0.1"

TYPE_HEADER = """
from mortis.plugin_api import DataTypePlugin, DataTypeSpec, FieldSpec, PlayerPlugin
"""

PLAYER_CLASS = """

class P{index}(PlayerPlugin):
    id = "{player_id}"
    name = "{player_id} 启动"
    file_kinds = ("game",)

    def play(self, host, path):
        host.launch(path, [])
"""

TYPE_FOOTER = """
PLUGIN = DataTypePlugin(
    spec=DataTypeSpec(
        id="{type_id}",
        name="{type_id}",
        structure="item",
        fields=[
            FieldSpec(field_key="title", label="名称", value_type="text",
                      stored_in="items_column", column_name="title", detail_section="基本信息"),
        ],
    ),
    players=({players}),
)
"""


def _write_type(
    folder: Path,
    type_id: str,
    player_ids: list[str],
    *,
    toml_players: list[tuple[str, str]] | None = None,
    init: str | None = None,
) -> Path:
    classes = "".join(
        PLAYER_CLASS.format(index=index, player_id=player_id)
        for index, player_id in enumerate(player_ids)
    )
    instances = "".join(f"P{index}(), " for index in range(len(player_ids)))
    code = TYPE_HEADER + classes + TYPE_FOOTER.format(type_id=type_id, players=instances)
    listed = toml_players if toml_players is not None else [(p, f"{p} 启动") for p in player_ids]
    toml = (
        f'id = "{type_id}"\nname = "{type_id}"\nversion = "1.0"\nkind = "data_type"\n'
        "[mortis]\napi_version = 1\n"
    )
    for player_id, name in listed:
        toml += f'\n[[players]]\nid = "{player_id}"\nname = "{name}"\n'
    target = folder / type_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "plugin.toml").write_text(toml, encoding="utf-8")
    (target / "__init__.py").write_text(init if init is not None else code, encoding="utf-8")
    return target


def _registry(*folders: tuple[Path, str]) -> PluginRegistry:
    registry = PluginRegistry(folders)  # type: ignore[arg-type]
    registry.load()
    return registry


def _assert_unique_ids(registry: PluginRegistry) -> None:
    ids = [info.id for info in registry.all()]
    assert len(ids) == len(set(ids)), ids


def test_manifest_reads_players(tmp_path: Path) -> None:
    folder = _write_type(tmp_path, "doujin", ["doujin_launcher"])
    manifest = read_manifest(folder)
    assert [(entry.id, entry.name) for entry in manifest.players] == [
        ("doujin_launcher", "doujin_launcher 启动")
    ]


def test_bundled_player_loads_with_parent(tmp_path: Path) -> None:
    _write_type(tmp_path, "doujin", ["doujin_launcher"])
    registry = _registry((tmp_path, "builtin"))
    info = registry.get("doujin_launcher")
    assert info is not None and info.error is None
    assert info.kind == "player" and info.parent == "doujin" and info.active
    assert isinstance(info.plugin, PlayerPlugin)
    assert registry.player("doujin_launcher") is info.plugin
    assert [plugin.id for plugin in registry.players()] == ["doujin_launcher"]
    assert info.plugin.file_kinds == ("game",)


def test_player_default_file_kinds_is_video() -> None:
    assert PlayerPlugin.file_kinds == ("video",)


def test_toml_players_must_match_object_players(tmp_path: Path) -> None:
    _write_type(tmp_path, "doujin", ["doujin_launcher"], toml_players=[])
    info = _registry((tmp_path, "builtin")).get("doujin")
    assert info is not None and info.error is not None
    assert "players" in info.error and "不一致" in info.error and "doujin_launcher" in info.error


def test_toml_players_extra_id_reported(tmp_path: Path) -> None:
    _write_type(
        tmp_path,
        "doujin",
        ["doujin_launcher"],
        toml_players=[("doujin_launcher", "启动"), ("ghost", "Ghost")],
    )
    info = _registry((tmp_path, "builtin")).get("doujin")
    assert info is not None and info.error is not None
    assert "不一致" in info.error and "ghost" in info.error


def test_toml_player_needs_name_and_valid_id(tmp_path: Path) -> None:
    _write_type(tmp_path, "doujin", ["doujin_launcher"], toml_players=[("doujin_launcher", " ")])
    info = _registry((tmp_path, "builtin")).get("doujin")
    assert info is not None and info.error is not None and "name" in info.error

    _write_type(tmp_path / "b", "eroge", ["x"], toml_players=[("Bad-Id", "坏")])
    try:
        read_manifest(tmp_path / "b" / "eroge")
    except PluginLoadError as error:
        assert "Bad-Id" in str(error)
    else:
        raise AssertionError("应拒绝不合法的 id")


def test_declared_players_need_a_matching_object(tmp_path: Path) -> None:
    target = tmp_path / "lone"
    target.mkdir()
    (target / "plugin.toml").write_text(
        'id = "lone"\nname = "lone"\nversion = "1.0"\nkind = "player"\n[mortis]\napi_version = 1\n'
        '\n[[players]]\nid = "sub"\nname = "Sub"\n',
        encoding="utf-8",
    )
    (target / "__init__.py").write_text("PLUGIN = None\n", encoding="utf-8")
    info = _registry((tmp_path, "builtin")).get("lone")
    assert info is not None and info.error is not None


def test_bundled_player_must_be_player_plugin(tmp_path: Path) -> None:
    init = TYPE_HEADER + TYPE_FOOTER.format(type_id="doujin", players="object(), ")
    _write_type(tmp_path, "doujin", ["x"], init=init)
    info = _registry((tmp_path, "builtin")).get("doujin")
    assert info is not None and info.error is not None and "PlayerPlugin" in info.error


def test_bundled_player_and_source_ids_must_differ(tmp_path: Path) -> None:
    target = _write_type(tmp_path, "doujin", ["doujin_launcher"])
    toml = (target / "plugin.toml").read_text(encoding="utf-8")
    toml += '\n[[sources]]\nid = "doujin_launcher"\nname = "同名来源"\n'
    (target / "plugin.toml").write_text(toml, encoding="utf-8")
    try:
        read_manifest(target)
    except PluginLoadError as error:
        assert "doujin_launcher" in str(error) and "重复" in str(error)
    else:
        raise AssertionError("来源和播放插件同 id 应被拒绝")


def test_disabled_parent_lists_players_from_toml(tmp_path: Path) -> None:
    _write_type(
        tmp_path, "doujin", ["doujin_launcher"], toml_players=[("doujin_launcher", "启动器")]
    )
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load(disabled=["doujin"])
    info = registry.get("doujin_launcher")
    assert info is not None
    assert info.kind == "player" and info.name == "启动器" and info.parent == "doujin"
    assert not info.enabled and not info.active and info.error is None
    assert registry.players() == []


def test_failed_parent_disables_players_with_error(tmp_path: Path) -> None:
    _write_type(tmp_path, "doujin", ["doujin_launcher"], init="raise RuntimeError('坏了')\n")
    registry = _registry((tmp_path, "builtin"))
    info = registry.get("doujin_launcher")
    assert info is not None and not info.enabled and not info.active
    assert info.parent == "doujin" and info.error is not None and "doujin" in info.error
    assert registry.players() == []


def test_disabling_one_bundled_player(tmp_path: Path) -> None:
    _write_type(tmp_path, "doujin", ["doujin_launcher"])
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load(disabled=["doujin_launcher"])
    parent = registry.get("doujin")
    assert parent is not None and parent.active
    info = registry.get("doujin_launcher")
    assert info is not None and not info.enabled and info.parent == "doujin"
    assert registry.player("doujin_launcher") is None


def test_user_override_replaces_whole_package_players(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    data = tmp_path / "data"
    _write_type(builtin, "doujin", ["old_launcher"])
    _write_type(data, "doujin", ["new_launcher"])
    registry = _registry((builtin, "builtin"), (data, "user"))
    assert registry.get("old_launcher") is None
    new = registry.get("new_launcher")
    assert new is not None and new.origin == "user" and new.active
    _assert_unique_ids(registry)


def test_bundled_player_clashing_with_other_plugin(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    _write_type(builtin, "aaa", ["shared_launcher"])
    _write_type(builtin, "bbb", ["shared_launcher"])
    registry = _registry((builtin, "builtin"))
    assert registry.player("shared_launcher") is not None
    bbb = registry.get("bbb")
    assert bbb is not None and bbb.error is not None
    assert "shared_launcher" in bbb.error and "重名" in bbb.error
    _assert_unique_ids(registry)


def test_api_lists_bundled_player_with_parent(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _write_type(data / "plugins", "doujin", ["doujin_launcher"])
    app = create_app(Settings(data_dir=data), start_worker=False, launcher=FakeLauncher())
    with TestClient(app, base_url=LOCAL) as client:
        plugins = {plugin["id"]: plugin for plugin in client.get("/api/plugins").json()}
        launcher = plugins["doujin_launcher"]
        assert launcher["kind"] == "player" and launcher["parent"] == "doujin"
        assert launcher["file_kinds"] == ["game"]
        assert plugins["system_player"]["file_kinds"] == ["video"]

        assert client.put("/api/plugins/doujin", json={"enabled": False}).status_code == 200
        plugins = {plugin["id"]: plugin for plugin in client.get("/api/plugins").json()}
        assert plugins["doujin_launcher"]["enabled"] is False
        assert plugins["doujin_launcher"]["parent"] == "doujin"
