"""examples/plugins 里的示例要真的能被加载、导入，而且不会被打进发布包或当成内置插件。"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.pack_plugin import pack

from mortis.plugins.packaging import PluginInstaller
from mortis.plugins.registry import PluginRegistry
from mortis.services import KeyedLocks

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "plugins"
NAMES = ("hello_source", "tag_rules")


def test_examples_load_through_the_registry_without_errors(tmp_path: Path) -> None:
    user_dir = tmp_path / "plugins"
    for name in NAMES:
        shutil.copytree(
            EXAMPLES / name, user_dir / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    registry = PluginRegistry([(user_dir, "user")])
    registry.load()
    infos = {info.id: info for info in registry.all()}
    # 钩子不单独占注册表的一条，跟着包走
    assert set(infos) == {"example.hello_source", "example.tag_rules", "example.tag_rules_films"}
    assert all(info.error is None and info.origin == "user" for info in infos.values())
    assert infos["example.hello_source"].capabilities == ("source",)
    assert infos["example.tag_rules"].capabilities == ("source", "hook")
    assert infos["example.tag_rules_films"].parent == "example.tag_rules"
    hooks = registry.subscribers("scrape.finished", "movie")
    assert [(s.plugin_id, s.hook.id, s.entry.writes) for s in hooks] == [
        ("example.tag_rules", "example.tag_rules_tagger", ("tags:keyword",))
    ]
    assert registry.subscribers("scrape.finished", "tv") == []
    assert registry.subscribers("scrape.started", "movie") == []


@pytest.mark.parametrize("name", NAMES)
def test_examples_go_through_the_import_flow(name: str, tmp_path: Path) -> None:
    result = pack(EXAMPLES / name, tmp_path / "out")
    user_dir = tmp_path / "host" / "plugins"
    registry = PluginRegistry([(user_dir, "user")])
    installer = PluginInstaller(user_dir, registry, KeyedLocks(), registry.load)
    preview = installer.inspect([result.path.read_bytes()])
    assert preview.id == f"example.{name}" and preview.existing is None
    installed = installer.confirm(preview.token)
    assert not installed.replaced
    info = registry.get(preview.id)
    assert info is not None and info.active and info.error is None


def test_examples_own_tests_pass() -> None:
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(EXAMPLES)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]


def test_examples_are_not_shipped_or_loaded_as_builtin() -> None:
    script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert not re.search(r"examples", script, re.IGNORECASE)
    assert not (ROOT / "plugins" / "hello_source").exists()
    assert not any(path.name.startswith("example") for path in (ROOT / "plugins").iterdir())
