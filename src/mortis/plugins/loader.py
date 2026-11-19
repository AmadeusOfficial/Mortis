"""从插件目录读清单、检查 import、导入模块。"""

import ast
import importlib.util
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from mortis.version import get_version

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
_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$")
_VERSION = re.compile(r"^\d+(\.\d+)*")
RESERVED_NAMESPACES = frozenset({"mortis", "builtin"})
"""带点的 id 里点前面的部分叫命名空间；这两个留给宿主，插件不能用。"""

API_VERSION_MIN = 1
API_VERSION_MAX = 1
"""本版宿主支持的插件接口版本范围（含两端）。api_version 是整数，只在破坏性改动时加一。"""


class PluginLoadError(Exception):
    pass


def id_problem(plugin_id: str) -> str | None:
    """id 格式不对的原因，合格返回 None。

    格式是 `名字` 或 `作者.名字`：只能一个点，每段小写字母、数字和下划线、字母开头。
    不带点的 id 留给内置插件；`mortis.*`、`builtin.*` 保留给宿主。
    """
    if not _ID.fullmatch(plugin_id):
        return "只能用小写字母、数字和下划线，最多一个点（作者.名字）"
    namespace, dot, _ = plugin_id.partition(".")
    if dot and namespace in RESERVED_NAMESPACES:
        return f"命名空间 {namespace} 保留给宿主"
    return None


def has_namespace(plugin_id: str) -> bool:
    """第三方插件的 id 必须带命名空间（作者.名字）。不带点的 id 留给内置插件。"""
    return "." in plugin_id


@dataclass(frozen=True)
class ManifestSource:
    """[[sources]]、[[players]] 的一项。data_type 只有来源用，为空表示没声明。"""

    id: str
    name: str
    data_type: str = ""


@dataclass(frozen=True)
class ManifestHook:
    """[[hooks]] 的一项：钩子 id、订阅的事件、限定的数据类型（空表示不限）、声明要写的权限。"""

    id: str
    event: str
    data_types: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    folder: Path
    api_version: int = API_VERSION_MIN
    min_mortis_version: str | None = None
    sources: tuple[ManifestSource, ...] = ()
    players: tuple[ManifestSource, ...] = ()
    hooks: tuple[ManifestHook, ...] = ()


def _version_tuple(text: str) -> tuple[int, ...] | None:
    match = _VERSION.match(text.strip())
    return tuple(int(part) for part in match.group(0).split(".")) if match else None


def _read_mortis(data: dict[str, Any]) -> tuple[int, str | None]:
    table = data.get("mortis")
    if not isinstance(table, dict):
        raise PluginLoadError(
            f"{MANIFEST} 缺少 [mortis] 表（写上 api_version），"
            "没有它的不是莫提斯插件，或是还没升级的旧插件"
        )
    api_version = table.get("api_version")
    if isinstance(api_version, bool) or not isinstance(api_version, int):
        raise PluginLoadError(f"{MANIFEST} 的 [mortis] 缺少整数 api_version")
    if api_version > API_VERSION_MAX:
        raise PluginLoadError(
            f"插件需要接口版本 {api_version}，本版莫提斯只支持 "
            f"{API_VERSION_MIN} 到 {API_VERSION_MAX}，请升级莫提斯"
        )
    if api_version < API_VERSION_MIN:
        raise PluginLoadError(
            f"插件的接口版本 {api_version} 太旧，本版莫提斯只支持 "
            f"{API_VERSION_MIN} 到 {API_VERSION_MAX}，请换新版插件"
        )
    minimum = table.get("min_mortis_version")
    if minimum is None:
        return api_version, None
    wanted = _version_tuple(minimum) if isinstance(minimum, str) else None
    if wanted is None:
        raise PluginLoadError(f"{MANIFEST} 的 min_mortis_version「{minimum}」不是版本号")
    current_text = get_version()
    current = _version_tuple(current_text)
    # 从源码目录直接运行时版本是 0.0.0，不拿它挡插件。
    if current is not None and current != (0, 0, 0) and current < wanted:
        raise PluginLoadError(
            f"插件需要莫提斯 {minimum} 或更高，当前是 {current_text}，请升级莫提斯"
        )
    return api_version, minimum


def read_manifest(folder: Path) -> Manifest:
    try:
        data = tomllib.loads((folder / MANIFEST).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise PluginLoadError(f"{MANIFEST} 读不了：{error}") from None
    missing = [key for key in ("id", "name", "version") if not data.get(key)]
    if missing:
        raise PluginLoadError(f"{MANIFEST} 缺少 {'、'.join(missing)}")
    plugin_id = str(data["id"])
    problem = id_problem(plugin_id)
    if problem:
        raise PluginLoadError(f"插件 id「{plugin_id}」{problem}")
    # 旧文件里的 kind 不再有用，写了也不报错。
    api_version, minimum = _read_mortis(data)
    seen: set[str] = {plugin_id}
    sources = _read_children(data.get("sources", []), "sources", "子来源", seen)
    players = _read_children(data.get("players", []), "players", "子播放插件", seen)
    hooks = _read_hooks(data.get("hooks", []), seen)
    return Manifest(
        plugin_id,
        str(data["name"]),
        str(data["version"]),
        folder,
        api_version,
        minimum,
        sources,
        players,
        hooks,
    )


def _read_children(
    raw: object, table: str, label: str, seen: set[str]
) -> tuple[ManifestSource, ...]:
    """plugin.toml 的 [[sources]] 或 [[players]] 表，每项要有合法的 id 和非空 name。

    seen 是这个包里已经用掉的 id（父插件和前面读过的表），id 在整个包里不能重复。
    [[sources]] 可以写 data_type，设置页不 import 代码就知道来源服务哪个类型。
    """
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise PluginLoadError(f"{MANIFEST} 的 {table} 必须是 [[{table}]] 表")
    entries: list[ManifestSource] = []
    for item in raw:
        child_id = str(item.get("id", ""))
        name = str(item.get("name", "")).strip()
        problem = id_problem(child_id)
        if problem:
            raise PluginLoadError(f"{label} id「{child_id}」{problem}")
        if not name:
            raise PluginLoadError(f"{label} {child_id} 缺少 name")
        data_type = item.get("data_type", "")
        if data_type != "" and (not isinstance(data_type, str) or table != "sources"):
            raise PluginLoadError(
                f"{label} {child_id} 的 data_type 只有 [[sources]] 能写，且是字符串"
            )
        if child_id in seen:
            raise PluginLoadError(f"{label} {child_id} 在 plugin.toml 里重复（包内 id 不能重名）")
        seen.add(child_id)
        entries.append(ManifestSource(child_id, name, data_type))
    return tuple(entries)


def _string_list(item: dict[str, Any], key: str, label: str) -> tuple[str, ...]:
    value = item.get(key, [])
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise PluginLoadError(f"{label} 的 {key} 必须是字符串列表")
    return tuple(value)


def _read_hooks(raw: object, seen: set[str]) -> tuple[ManifestHook, ...]:
    """[[hooks]]：id、event 必填，data_types 和 writes 可选。这一版只解析并保存，
    事件名和权限是否存在由事件运行时检查。"""
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise PluginLoadError(f"{MANIFEST} 的 hooks 必须是 [[hooks]] 表")
    entries: list[ManifestHook] = []
    for item in raw:
        hook_id = str(item.get("id", ""))
        problem = id_problem(hook_id)
        if problem:
            raise PluginLoadError(f"钩子 id「{hook_id}」{problem}")
        label = f"钩子 {hook_id}"
        event = item.get("event")
        if not isinstance(event, str) or not event:
            raise PluginLoadError(f"{label} 缺少 event")
        if hook_id in seen:
            raise PluginLoadError(f"{label} 在 plugin.toml 里重复（包内 id 不能重名）")
        seen.add(hook_id)
        entries.append(
            ManifestHook(
                hook_id,
                event,
                _string_list(item, "data_types", label),
                _string_list(item, "writes", label),
            )
        )
    return tuple(entries)


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


def _is_test_file(relative: Path) -> bool:
    """作者自己的测试（tests/ 目录和 test_*.py）不随插件加载，不检查，也不打进包。"""
    return "tests" in relative.parts[:-1] or relative.name.startswith("test_")


def check_imports(folder: Path) -> None:
    """插件只能用标准库、pydantic 和宿主接口，以及自己目录里的模块。

    这是约定检查，挡住误用和常见写法，不是安全沙箱：插件只从本机目录加载，由用户自己放进去。
    """
    for path in sorted(folder.rglob("*.py")):
        if _is_test_file(path.relative_to(folder)):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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
