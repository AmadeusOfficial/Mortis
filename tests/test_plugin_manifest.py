from pathlib import Path

import pytest

from mortis.plugins import loader
from mortis.plugins.loader import PluginLoadError, check_imports, read_manifest

BASE = 'id = "{id}"\nname = "示例"\nversion = "1.0"\n'


def _folder(tmp_path: Path, toml: str) -> Path:
    folder = tmp_path / f"plug{len(list(tmp_path.iterdir()))}"
    folder.mkdir()
    (folder / "plugin.toml").write_text(toml, encoding="utf-8")
    return folder


def _manifest(tmp_path: Path, extra: str = "", plugin_id: str = "demo", mortis: str | None = None):
    mortis = "[mortis]\napi_version = 1\n" if mortis is None else mortis
    return read_manifest(_folder(tmp_path, BASE.format(id=plugin_id) + mortis + extra))


def test_reads_api_version_and_min_version(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path, mortis='[mortis]\napi_version = 1\nmin_mortis_version = "0.0.1"\n'
    )
    assert manifest.api_version == 1
    assert manifest.min_mortis_version == "0.0.1"


def test_missing_mortis_table_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PluginLoadError, match=r"\[mortis\]"):
        _manifest(tmp_path, mortis="")


@pytest.mark.parametrize("body", ["", "api_version = '1'\n", "api_version = true\n"])
def test_api_version_must_be_an_integer(tmp_path: Path, body: str) -> None:
    with pytest.raises(PluginLoadError, match="api_version"):
        _manifest(tmp_path, mortis=f"[mortis]\n{body}")


def test_newer_api_version_asks_to_upgrade_mortis(tmp_path: Path) -> None:
    with pytest.raises(PluginLoadError, match="升级莫提斯"):
        _manifest(tmp_path, mortis=f"[mortis]\napi_version = {loader.API_VERSION_MAX + 1}\n")


def test_older_api_version_says_plugin_is_too_old(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(loader, "API_VERSION_MIN", 2)
    monkeypatch.setattr(loader, "API_VERSION_MAX", 3)
    with pytest.raises(PluginLoadError, match="太旧"):
        _manifest(tmp_path, mortis="[mortis]\napi_version = 1\n")


def test_min_mortis_version_is_checked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(loader, "get_version", lambda: "0.1.2")
    mortis = '[mortis]\napi_version = 1\nmin_mortis_version = "{}"\n'
    assert _manifest(tmp_path, mortis=mortis.format("0.1.2")).min_mortis_version == "0.1.2"
    with pytest.raises(PluginLoadError, match="升级莫提斯"):
        _manifest(tmp_path, mortis=mortis.format("0.2.0"))
    with pytest.raises(PluginLoadError, match="不是版本号"):
        _manifest(tmp_path, mortis=mortis.format("新版"))


def test_unknown_host_version_does_not_block(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(loader, "get_version", lambda: "0.0.0")
    manifest = _manifest(
        tmp_path, mortis='[mortis]\napi_version = 1\nmin_mortis_version = "9.9.9"\n'
    )
    assert manifest.min_mortis_version == "9.9.9"


def test_kind_is_optional_and_ignored(tmp_path: Path) -> None:
    assert _manifest(tmp_path).id == "demo"
    folder = tmp_path / "old"
    folder.mkdir()
    (folder / "plugin.toml").write_text(
        BASE.format(id="demo") + 'kind = "whatever"\n[mortis]\napi_version = 1\n', encoding="utf-8"
    )
    assert read_manifest(folder).id == "demo"


def test_sources_can_declare_data_type(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        '\n[[sources]]\nid = "a"\nname = "A"\ndata_type = "movie"\n'
        '\n[[sources]]\nid = "b"\nname = "B"\n',
    )
    assert [(s.id, s.data_type) for s in manifest.sources] == [("a", "movie"), ("b", "")]


def test_players_cannot_declare_data_type(tmp_path: Path) -> None:
    with pytest.raises(PluginLoadError, match="data_type"):
        _manifest(tmp_path, '\n[[players]]\nid = "p"\nname = "P"\ndata_type = "movie"\n')


@pytest.mark.parametrize("plugin_id", ["demo", "author.demo", "a1.b_2"])
def test_valid_ids(tmp_path: Path, plugin_id: str) -> None:
    assert _manifest(tmp_path, plugin_id=plugin_id).id == plugin_id


@pytest.mark.parametrize(
    "plugin_id",
    ["a.b.c", "Author.demo", ".demo", "demo.", "1a.b", "a-b.c", "mortis.demo", "builtin.x"],
)
def test_invalid_ids(tmp_path: Path, plugin_id: str) -> None:
    with pytest.raises(PluginLoadError, match="id"):
        _manifest(tmp_path, plugin_id=plugin_id)


def test_child_ids_follow_the_same_rules(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, '\n[[sources]]\nid = "me.src"\nname = "S"\n')
    assert manifest.sources[0].id == "me.src"
    with pytest.raises(PluginLoadError, match="id"):
        _manifest(tmp_path, '\n[[sources]]\nid = "mortis.src"\nname = "S"\n')


def test_reads_hooks(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        '\n[[hooks]]\nid = "me.hook"\nevent = "scrape.finished"\n'
        'data_types = ["movie"]\nwrites = ["tags:custom"]\n',
    )
    [hook] = manifest.hooks
    assert (hook.id, hook.event, hook.data_types, hook.writes) == (
        "me.hook",
        "scrape.finished",
        ("movie",),
        ("tags:custom",),
    )


def test_hook_needs_event_and_unique_id(tmp_path: Path) -> None:
    with pytest.raises(PluginLoadError, match="event"):
        _manifest(tmp_path, '\n[[hooks]]\nid = "h"\n')
    with pytest.raises(PluginLoadError, match="重复"):
        _manifest(
            tmp_path,
            '\n[[sources]]\nid = "h"\nname = "S"\n\n[[hooks]]\nid = "h"\nevent = "x"\n',
        )


def test_check_imports_skips_tests(tmp_path: Path) -> None:
    folder = tmp_path / "p"
    (folder / "tests").mkdir(parents=True)
    (folder / "__init__.py").write_text("PLUGIN = None\n", encoding="utf-8")
    (folder / "tests" / "test_x.py").write_text("import pytest\n", encoding="utf-8")
    (folder / "test_y.py").write_text("import pytest\n", encoding="utf-8")
    check_imports(folder)
    (folder / "helper.py").write_text("import pytest\n", encoding="utf-8")
    with pytest.raises(PluginLoadError, match="pytest"):
        check_imports(folder)
