"""从插件目录读清单、检查 import、导入模块。"""

import ast
import importlib.util
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Literal

MANIFEST = "plugin.toml"
PACKAGE = "mortis_plugins"
ALLOWED_PACKAGES = frozenset({"pydantic"})
ALLOWED_MORTIS = "mortis.plugin_api"
_ID = re.compile(r"^[a-z][a-z0-9_]*$")

Kind = Literal["data_type", "source"]


class PluginLoadError(Exception):
    pass


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    kind: Kind
    folder: Path


def read_manifest(folder: Path) -> Manifest:
    try:
        data = tomllib.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise PluginLoadError(f"{MANIFEST} 读不了：{error}") from None
    missing = [key for key in ("id", "name", "version", "kind") if not data.get(key)]
    if missing:
        raise PluginLoadError(f"{MANIFEST} 缺少 {'、'.join(missing)}")
    plugin_id = str(data["id"])
    if not _ID.fullmatch(plugin_id):
        raise PluginLoadError(f"插件 id「{plugin_id}」只能用小写字母、数字和下划线")
    if data["kind"] not in ("data_type", "source"):
        raise PluginLoadError(f"插件种类只能是 data_type 或 source，不是「{data['kind']}」")
    return Manifest(plugin_id, str(data["name"]), str(data["version"]), data["kind"], folder)


def _allowed(module: str) -> bool:
    top = module.split(".")[0]
    if top == "mortis":
        return module == ALLOWED_MORTIS
    return top in sys.stdlib_module_names or top in ALLOWED_PACKAGES


def check_imports(folder: Path) -> None:
    """插件只能用标准库、pydantic 和宿主接口，以及自己目录里的模块。"""
    for path in sorted(folder.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as error:
            raise PluginLoadError(f"{path.name} 读不了：{error}") from None
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
                if node.module == "mortis":
                    names = [f"mortis.{alias.name}" for alias in node.names]
            else:
                continue
            for name in names:
                if not _allowed(name):
                    relative = path.relative_to(folder).as_posix()
                    raise PluginLoadError(f"{relative} 用了不允许的模块 {name}")


def import_plugin(manifest: Manifest) -> ModuleType:
    """每次都重新执行模块，改过的代码立即生效。"""
    name = f"{PACKAGE}.{manifest.id}"
    for loaded in [key for key in sys.modules if key == name or key.startswith(f"{name}.")]:
        del sys.modules[loaded]
    if PACKAGE not in sys.modules:
        package = ModuleType(PACKAGE)
        package.__path__ = []
        sys.modules[PACKAGE] = package
    init = manifest.folder / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        name, init, submodule_search_locations=[str(manifest.folder)]
    )
    if spec is None or spec.loader is None or not init.is_file():
        raise PluginLoadError("插件目录里没有 __init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        del sys.modules[name]
        raise PluginLoadError(f"导入出错：{type(error).__name__}: {error}") from None
    return module
