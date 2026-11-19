"""scripts/pack_plugin.py：校验并打包，错误信息对作者友好，打出的 zip 能被导入流程接受。"""

import hashlib
import zipfile
from pathlib import Path

import pytest
from scripts import pack_plugin
from scripts.pack_plugin import PackError, pack

from mortis.plugins.packaging import PluginInstaller
from mortis.plugins.registry import PluginRegistry
from mortis.services import KeyedLocks

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "plugins"

CODE = """
from mortis.plugin_api import Hit, SourcePlugin


class Src(SourcePlugin):
    id = "me.demo"
    name = "来源"
    data_type = "movie"
    provider = "me"

    def search(self, host, title):
        return [Hit(external_id="1", title=title)]

    def fetch(self, host, external_id):
        return {}

    def candidates(self, raw):
        return {}


PLUGIN = Src()
"""
TOML = 'id = "me.demo"\nname = "示例"\nversion = "1.2"\n\n[mortis]\napi_version = 1\n'


def make_plugin(root: Path, files: dict[str, str] | None = None) -> Path:
    folder = root / "demo"
    folder.mkdir()
    content = {"plugin.toml": TOML, "__init__.py": CODE, **(files or {})}
    for name, text in content.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return folder


def names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return sorted(archive.namelist())


def fails(folder: Path, text: str, tmp: Path) -> None:
    with pytest.raises(PackError) as caught:
        pack(folder, tmp / "out")
    assert text in str(caught.value)
    assert not (tmp / "out").exists() or not list((tmp / "out").glob("*.zip"))


def test_packs_a_plugin_and_reports_hashes(tmp_path: Path) -> None:
    folder = make_plugin(tmp_path)
    result = pack(folder, tmp_path / "out")
    assert result.path == tmp_path / "out" / "me.demo-1.2.zip"
    assert names(result.path) == ["__init__.py", "plugin.toml"]
    assert result.plugin_id == "me.demo" and result.version == "1.2"
    assert result.capabilities == ("source",)
    assert result.zip_sha256 == hashlib.sha256(result.path.read_bytes()).hexdigest()
    assert len(result.package_hash) == 64 and result.file_count == 2


def test_zip_leaves_out_tests_caches_and_hidden_files(tmp_path: Path) -> None:
    folder = make_plugin(
        tmp_path,
        {
            "tests/test_demo.py": "import pytest\n",
            "test_helper.py": "import pytest\n",
            "__pycache__/x.cpython-314.pyc": "",
            ".hidden/a.txt": "x",
            ".env": "x",
            "notes/说明.md": "说明",
        },
    )
    result = pack(folder, tmp_path / "out")
    assert names(result.path) == ["__init__.py", "notes/说明.md", "plugin.toml"]


def test_same_content_gives_the_same_zip(tmp_path: Path) -> None:
    folder = make_plugin(tmp_path)
    first = pack(folder, tmp_path / "a.zip")
    second = pack(folder, tmp_path / "b.zip")
    assert first.zip_sha256 == second.zip_sha256


def test_package_hash_matches_the_import_preview(tmp_path: Path) -> None:
    result = pack(make_plugin(tmp_path), tmp_path / "out")
    user_dir = tmp_path / "host" / "plugins"
    registry = PluginRegistry([(user_dir, "user")])
    installer = PluginInstaller(user_dir, registry, KeyedLocks(), registry.load)
    preview = installer.inspect([result.path.read_bytes()])
    assert preview.package_hash == result.package_hash


@pytest.mark.parametrize("name", ["hello_source", "tag_rules"])
def test_examples_pack_cleanly(name: str, tmp_path: Path) -> None:
    result = pack(EXAMPLES / name, tmp_path)
    listed = names(result.path)
    assert "plugin.toml" in listed and "__init__.py" in listed
    assert not any(part.startswith("tests") or "test_" in part for part in listed)


def test_missing_folder_and_manifest(tmp_path: Path) -> None:
    fails(tmp_path / "nope", "找不到插件目录", tmp_path)
    (tmp_path / "empty").mkdir()
    fails(tmp_path / "empty", "没有 plugin.toml", tmp_path)


def test_missing_init(tmp_path: Path) -> None:
    folder = make_plugin(tmp_path)
    (folder / "__init__.py").unlink()
    fails(folder, "没有 __init__.py", tmp_path)


def test_manifest_without_mortis_table(tmp_path: Path) -> None:
    folder = make_plugin(tmp_path, {"plugin.toml": 'id = "me.demo"\nname = "x"\nversion = "1"\n'})
    fails(folder, "[mortis]", tmp_path)


def test_id_must_have_a_namespace(tmp_path: Path) -> None:
    toml = TOML.replace("me.demo", "demo")
    fails(make_plugin(tmp_path, {"plugin.toml": toml}), "命名空间", tmp_path)


def test_forbidden_import_is_explained(tmp_path: Path) -> None:
    folder = make_plugin(tmp_path, {"extra.py": "import requests\n"})
    fails(folder, "requests", tmp_path)
    fails(folder, "只能 import 标准库", tmp_path)


def test_manifest_and_code_must_match(tmp_path: Path) -> None:
    toml = TOML + '\n[[hooks]]\nid = "me.hook"\nevent = "scrape.finished"\n'
    fails(make_plugin(tmp_path, {"plugin.toml": toml}), "试运行没通过", tmp_path)


@pytest.mark.parametrize(
    ("name", "text"),
    [("helper.dll", "二进制或可执行文件"), ("data.toml", "不接受这种文件")],
)
def test_files_the_importer_rejects_are_reported(name: str, text: str, tmp_path: Path) -> None:
    fails(make_plugin(tmp_path, {name: "x"}), text, tmp_path)


def test_main_prints_a_summary_or_a_chinese_error(tmp_path: Path, capsys) -> None:
    folder = make_plugin(tmp_path)
    assert pack_plugin.main([str(folder), "-o", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "me.demo" in out and "包哈希" in out and "zip SHA-256" in out
    assert pack_plugin.main([str(tmp_path / "missing")]) == 1
    assert "打包失败：找不到插件目录" in capsys.readouterr().err
