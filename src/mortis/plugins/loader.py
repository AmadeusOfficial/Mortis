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
DENIED_STDLIB = frozenset(
    {
        "subprocess",
        "multiprocessing",
        "_winapi",
        "_posixsubprocess",
        "ctypes",
        "importlib",
        "socket",
        "ssl",
        "http",
        "urllib.request",
        "ftplib",
        "smtplib",
    }
)
"""启动程序只能经宿主的 launch，联网只能经宿主的客户端。"""
DENIED_CALLS = frozenset({"__import__", "eval", "exec", "compile"})
_OS_MODULES = frozenset({"os", "nt", "posix"})
_DENIED_ATTR = re.compile(
    r"^(system|popen|startfile|fork|forkpty|spawn\w*|exec[lv]\w*|posix_spawn\w*)$"
)
_ID = re.compile(r"^[a-z][a-z0-9_]*$")

Kind = Literal["data_type", "source", "player"]
KINDS: tuple[Kind, ...] = ("data_type", "source", "player")


class PluginLoadError(Exception):
    pass


@dataclass(frozen=True)
class ManifestSource:
    id: str
    name: str


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    kind: Kind
    folder: Path
    sources: tuple[ManifestSource, ...] = ()


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
    if data["kind"] not in KINDS:
        raise PluginLoadError(f"插件种类只能是 data_type、source 或 player，不是「{data['kind']}」")
    sources = _read_sources(data.get("sources", []))
    if sources and data["kind"] != "data_type":
        raise PluginLoadError(f"只有 data_type 插件能声明 [[sources]]，{data['kind']} 不能")
    return Manifest(
        plugin_id, str(data["name"]), str(data["version"]), data["kind"], folder, sources
    )


def _read_sources(raw: object) -> tuple[ManifestSource, ...]:
    """plugin.toml 的 [[sources]] 表，每项要有合法的 id 和非空 name，id 不能重复。"""
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise PluginLoadError(f"{MANIFEST} 的 sources 必须是 [[sources]] 表")
    sources: list[ManifestSource] = []
    seen: set[str] = set()
    for item in raw:
        source_id = str(item.get("id", ""))
        name = str(item.get("name", "")).strip()
        if not _ID.fullmatch(source_id):
            raise PluginLoadError(f"子来源 id「{source_id}」只能用小写字母、数字和下划线")
        if not name:
            raise PluginLoadError(f"子来源 {source_id} 缺少 name")
        if source_id in seen:
            raise PluginLoadError(f"子来源 {source_id} 在 plugin.toml 里重复")
        seen.add(source_id)
        sources.append(ManifestSource(source_id, name))
    return tuple(sources)


def _allowed(module: str) -> bool:
    top = module.split(".")[0]
    if top == "mortis":
        return module == ALLOWED_MORTIS
    if any(module == denied or module.startswith(f"{denied}.") for denied in DENIED_STDLIB):
        return False
    return top in sys.stdlib_module_names or top in ALLOWED_PACKAGES


def _denied_use(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name) and node.id in DENIED_CALLS:
        return node.id
    if isinstance(node, ast.Attribute):
        if node.attr == "__import__":
            return node.attr
        if (
            isinstance(node.value, ast.Name)
            and node.value.id in _OS_MODULES
            and _DENIED_ATTR.fullmatch(node.attr)
        ):
            return f"{node.value.id}.{node.attr}"
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in _OS_MODULES:
        for alias in node.names:
            if _DENIED_ATTR.fullmatch(alias.name):
                return f"{node.module}.{alias.name}"
    return None


def check_imports(folder: Path) -> None:
    """插件只能用标准库、pydantic 和宿主接口，以及自己目录里的模块。

    这是约定检查，挡住误用和常见写法，不是安全沙箱：插件只从本机目录加载，由用户自己放进去。
    """
    for path in sorted(folder.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as error:
            raise PluginLoadError(f"{path.name} 读不了：{error}") from None
        for node in ast.walk(tree):
            used = _denied_use(node)
            if used is not None:
                relative = path.relative_to(folder).as_posix()
                raise PluginLoadError(f"{relative} 用了不允许的 {used}，开进程和联网要经宿主")
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
